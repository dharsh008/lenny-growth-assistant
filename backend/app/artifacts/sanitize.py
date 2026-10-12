"""Defence-in-depth for untrusted, model-generated HTML.

Layer 1 (here, server-side): allow-list sanitiser (nh3/ammonia) + CSS scrubbing.
Layer 2 (frontend): rendered in an <iframe sandbox=""> (no scripts, no same-origin,
        no forms, no popups, no top navigation) with a restrictive CSP injected via <meta>.
Either layer alone would be sufficient for scripts; together they also stop
data exfiltration through CSS url(), <img> beacons, external fonts and link clicks."""
import re
from dataclasses import dataclass, field

import nh3

SAFE_TAGS = {
    "a", "abbr", "article", "aside", "b", "blockquote", "br", "caption", "code", "col", "colgroup", "dd", "details",
    "div", "dl", "dt", "em", "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "i",
    "img", "kbd", "li", "main", "mark", "nav", "ol", "p", "pre", "q", "s", "section", "small", "span", "strong", "style",
    "sub", "summary", "sup", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
    # inline SVG (for charts / icons) - no <script>, <foreignObject>, <use>, <image>
    "svg", "g", "path", "circle", "ellipse", "rect", "line", "polyline", "polygon", "text", "tspan", "defs",
    "lineargradient", "radialgradient", "stop", "title", "desc",
}
SVG_ATTRS = {
    "viewBox", "width", "height", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill",
    "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray", "opacity", "transform", "offset",
    "stop-color", "stop-opacity", "text-anchor", "font-size", "font-weight", "font-family", "dominant-baseline",
    "fill-opacity", "stroke-opacity", "xmlns", "preserveAspectRatio", "gradientUnits", "gradientTransform",
}
SAFE_ATTRS = {
    "*": {"class", "id", "style", "title", "lang", "dir", "role", "aria-label", "aria-hidden", "aria-describedby", "tabindex"} | SVG_ATTRS,
    "a": {"href"},
    "img": {"src", "alt", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan", "scope"},
    "ol": {"start"},
    "col": {"span"},
    "details": {"open"},
}
CSS_BLOCKLIST = [
    (re.compile(r"@import[^;{]*;?", re.I), "css_import"),
    (re.compile(r"url\(\s*(?!['\"]?data:image/(?:png|jpe?g|gif|webp|svg\+xml);)[^)]*\)", re.I), "css_external_url"),
    (re.compile(r"expression\s*\(", re.I), "css_expression"),
    (re.compile(r"(?:-moz-binding|behavior)\s*:[^;}]*", re.I), "css_binding"),
    (re.compile(r"javascript\s*:", re.I), "css_js_url"),
    (re.compile(r"@(?:font-face|namespace|charset)[^{;]*(?:\{[^}]*\}|;)", re.I), "css_at_rule"),
]
STYLE_BLOCK_RE = re.compile(r"(<style[^>]*>)(.*?)(</style>)", re.I | re.S)
STYLE_ATTR_RE = re.compile(r'(style\s*=\s*")([^"]*)(")', re.I)


@dataclass
class SanitizeReport:
    removed: dict[str, int] = field(default_factory=dict)
    changed: bool = False

    def add(self, key: str, n: int = 1) -> None:
        if n:
            self.removed[key] = self.removed.get(key, 0) + n
            self.changed = True


def _scrub_css(css: str, report: SanitizeReport) -> str:
    for rx, name in CSS_BLOCKLIST:
        css, n = rx.subn("", css)
        report.add(name, n)
    return css


def _count(rx: str, html: str) -> int:
    return len(re.findall(rx, html, flags=re.I))


def _safe_link(tag: str, attr: str, value: str):
    """Only in-document anchors survive. External hrefs are dropped so a click can never
    navigate the sandboxed frame; img src is limited to inline data: images."""
    if tag == "a" and attr == "href":
        return value if value.startswith("#") else None
    if tag == "img" and attr == "src":
        return value if re.match(r"data:image/(png|jpe?g|gif|webp|svg\+xml);", value, re.I) else None
    return value


def sanitize_html(html: str) -> tuple[str, SanitizeReport]:
    rpt = SanitizeReport()
    # count what the allow-list is about to remove, purely for the audit report / UI policy panel
    rpt.add("script", _count(r"<script\b", html))
    rpt.add("event_handler", _count(r"\son[a-z]+\s*=", html))
    rpt.add("iframe_object_embed", _count(r"<(iframe|object|embed|frame|frameset|applet)\b", html))
    rpt.add("form_controls", _count(r"<(form|input|button|textarea|select)\b", html))
    rpt.add("meta_link_base", _count(r"<(meta|link|base)\b", html))
    rpt.add("javascript_url", _count(r"javascript\s*:", html))
    rpt.add("external_resource", _count(r"""(?:src|href)\s*=\s*["']?\s*(?:https?:)?//""", html))

    html = STYLE_BLOCK_RE.sub(lambda m: m.group(1) + _scrub_css(m.group(2), rpt) + m.group(3), html)
    html = STYLE_ATTR_RE.sub(lambda m: m.group(1) + _scrub_css(m.group(2), rpt) + m.group(3), html)
    clean = nh3.clean(
        html,
        tags=SAFE_TAGS,
        attributes=SAFE_ATTRS,
        clean_content_tags={"script", "iframe", "object", "embed", "noscript", "template"},  # drop these *with* their content; <style> text is kept (scrubbed above)
        url_schemes={"http", "https", "data"},
        attribute_filter=_safe_link,
        link_rel="noopener noreferrer nofollow",
        strip_comments=True,
    )
    return clean, rpt


def wrap_srcdoc(clean_html: str) -> str:
    """Reference implementation of the document the frontend builds for the iframe (kept here so
    the policy is unit-tested server-side as well as implemented in the UI)."""
    csp = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f'<meta http-equiv="Content-Security-Policy" content="{csp}">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"</head><body>{clean_html}</body></html>"
    )
