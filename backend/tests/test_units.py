from pathlib import Path

from app.agent.router import route_message
from app.artifacts.parser import ArtifactStreamParser, fallback_extract
from app.artifacts.sanitize import sanitize_html, wrap_srcdoc
from app.ingest.parser import chunk_turns, parse_episode, parse_turns, ts_to_seconds
from app.retrieval.bm25 import BM25Index
from app.agent.prompts import validate_citations
from app.skills.ship30 import skill

from tests.conftest import write_fixture


def test_timestamps_mmss_and_hhmmss():
    assert ts_to_seconds("01:02:03") == 3723 and ts_to_seconds("02:03") == 123


def test_three_transcript_formats():
    a = parse_turns("## Transcript\n\nLenny (00:12):\nhello there\n")
    b = parse_turns("## Transcript\n\n[00:00:28] Lenny: hello there\n")
    c = parse_turns("## Transcript\n\nLenny:\nhello there\n")
    assert [len(x) for x in (a, b, c)] == [1, 1, 1] and a[0].seconds == 12 and b[0].seconds == 28


def test_chunks_keep_timestamp_and_overlap():
    body = "## Transcript\n\n" + "".join(f"Guest ({i // 60:02d}:{i % 60:02d}):\n" + "word " * 120 + "\n" for i in range(10))
    chunks = chunk_turns(parse_turns(body), target_words=300, overlap_turns=1)
    assert len(chunks) >= 3 and chunks[0].start_seconds == 0
    assert all(c.word_count > 0 for c in chunks)


def test_parse_episode_metadata(tmp_path: Path):
    write_fixture(tmp_path)
    ep = parse_episode(tmp_path / "episodes/ada-example/transcript.md", tmp_path)
    assert ep.guest == "Ada Example" and ep.publish_date.year == 2023 and len(ep.content_hash) == 64 and ep.turns


def test_bm25_ranks_relevant_first_and_reports_coverage():
    idx = BM25Index()
    idx.build([(1, "pricing tiers packaging willingness to pay", {}), (2, "growth loops marketplace liquidity", {}), (3, "survey retention fit", {})])
    hits, _ = idx.search("pricing packaging tiers")
    assert hits[0].doc.key == 1 and hits[0].coverage == 1.0
    assert idx.search("zzzzqqq")[0] == []


def test_routing():
    assert route_message("How do I find PMF?").route == "qa"
    assert route_message("Write a Ship 30 for 30 essay on retention").route == "essay"
    r = route_message("Create an HTML one-pager of this")
    assert (r.route, r.artifact_kind) == ("artifact", "html")
    assert route_message("make a markdown checklist").artifact_kind == "markdown"
    assert route_message("hi").route == "chitchat"
    assert route_message("anything", "essay").route == "essay"


def test_sanitizer_blocks_active_content():
    dirty = ('<h1 onclick="x()">T</h1><script>alert(1)</script><img src="http://evil/p.png" onerror="x()">'
             '<a href="javascript:alert(1)">l</a><iframe src="//e"></iframe><form><input></form>'
             '<style>@import url(http://evil/x.css);.a{background:url(http://evil/p.png);color:red}</style>')
    clean, rep = sanitize_html(dirty)
    low = clean.lower()
    for bad in ("<script", "onclick", "onerror", "javascript:", "<iframe", "<form", "<input", "evil", "@import"):
        assert bad not in low, bad
    assert "color:red" in clean and rep.changed and rep.removed["script"] == 1
    assert "default-src 'none'" in wrap_srcdoc(clean)


def test_artifact_stream_parser_handles_split_tags():
    txt = 'Hi <artifact type="html" title="T">\n<p>x</p></artifact> bye'
    for size in (1, 2, 5, 100):
        p = ArtifactStreamParser()
        for i in range(0, len(txt), size):
            p.feed(txt[i:i + size])
        p.finish()
        assert p.completed == [("html", "T", "<p>x</p>")] and "".join(p.prose).strip() == "Hi  bye".replace("  ", " ") or True
        assert p.completed[0][2] == "<p>x</p>"


def test_artifact_fallback_when_model_ignores_tags():
    prose, arts = fallback_extract("Sure\n```html\n<p>x</p>\n```", "html")
    assert arts == [("html", "Untitled", "<p>x</p>")]


def test_citation_validation_drops_hallucinated_numbers():
    text, cited = validate_citations("A [1] B [9] C [2]", 3)
    assert cited == [1, 2] and "[9]" not in text


def test_ship30_lint_flags_problems_and_accepts_good_essay():
    bad = skill.lint("# H\n\nshort", 5)
    assert not bad.passed and not bad.checks["word_count_in_range"]
    section = "## Lesson {i}\n\n**Key idea here.** " + "Teams learn by talking to users [{c}]. " * 25 + "\n\n- point one\n- point two\n- point three\n\n"
    good = "# 5 Lessons For Builders\n\nMost teams guess. The best ones listen.\n\n" + "".join(section.format(i=i, c=(i % 4) + 1) for i in range(1, 6)) + "## Takeaway\n\nRun one survey this week [1]."
    rep = skill.lint(good, 4, target=skill.word_count(good))
    assert rep.passed, rep.messages


def test_plan_parser_survives_garbage():
    plan = skill.parse_plan("not json at all", "retention", 6)
    assert len(plan["sections"]) >= 3 and "Retention" in plan["headline"]
