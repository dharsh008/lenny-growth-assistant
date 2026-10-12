import { describe, expect, it } from "vitest";
import { parseFrame } from "./api";
import { ARTIFACT_CSP, ARTIFACT_SANDBOX, htmlSrcDoc, renderChat, renderMarkdown } from "./safe";

describe("markdown rendering is sanitised", () => {
  it("strips scripts, handlers and javascript: links", () => {
    const html = renderMarkdown('# Hi\n\n<script>alert(1)</script><img src=x onerror=alert(1)>\n\n[x](javascript:alert(1))');
    expect(html).not.toMatch(/<script/i);
    expect(html).not.toMatch(/onerror/i);
    expect(html).not.toMatch(/javascript:/i);
  });
  it("opens external links safely and drops remote images", () => {
    const html = renderMarkdown("[a](https://example.com) ![t](https://evil.test/p.png)");
    expect(html).toContain('rel="noopener noreferrer nofollow"');
    expect(html).toContain('target="_blank"');
    expect(html).not.toContain("evil.test");
  });
  it("turns known [n] markers into citation buttons only", () => {
    const html = renderChat("Claim [1] and bogus [7].", new Set([1]));
    expect(html).toContain('class="cite" data-n="1"');
    expect(html).toContain("[7]");
  });
});

describe("html artifact policy", () => {
  it("sandbox has no allow-* flags", () => {
    expect(ARTIFACT_SANDBOX).toBe("");
  });
  it("CSP denies by default and allows no scripts or remote loads", () => {
    expect(ARTIFACT_CSP).toContain("default-src 'none'");
    expect(ARTIFACT_CSP).not.toMatch(/script-src/);
    expect(ARTIFACT_CSP).not.toMatch(/https?:/);
    expect(htmlSrcDoc("<p>x</p>")).toContain("Content-Security-Policy");
  });
});

describe("SSE frame parser", () => {
  it("parses event + json data and ignores comments", () => {
    expect(parseFrame('event: delta\ndata: {"text":"hi"}')).toEqual({ type: "delta", text: "hi" });
    expect(parseFrame(": keep-alive")).toBeNull();
  });
});
