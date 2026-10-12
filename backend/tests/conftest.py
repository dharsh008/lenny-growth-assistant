import os
import tempfile

_tmp = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_tmp}/test.db"
os.environ["AUTO_INGEST"] = "false"
os.environ["TRANSCRIPTS_DIR"] = f"{_tmp}/transcripts"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["RETRIEVAL_MIN_SCORE"] = "0.1"  # tiny fixture corpus => small idf values

EPISODES = {
    "ada-example": ("Ada Example", "How to find product-market fit", """\
Ada Example (00:00:05):
Product-market fit is when retention flattens and users would be very disappointed without the product. We ran a survey every week and tracked the percent who said very disappointed.
Lenny (00:01:10):
How do you measure product-market fit in practice with a survey?
Ada Example (00:01:30):
The survey asks how disappointed users would be. Above forty percent very disappointed means product-market fit. Retention curves should flatten too."""),
    "bo-example": ("Bo Example", "Pricing B2B SaaS", """\
Bo Example (00:00:05):
For B2B SaaS pricing start with value metrics, packaging tiers and willingness to pay interviews. Pricing power comes from packaging.
Lenny (00:02:00):
What about pricing experiments?
Bo Example (00:02:20):
Test pricing with willingness to pay interviews before changing packaging tiers."""),
    "cy-example": ("Cy Example", "Growth loops for marketplaces", """\
Cy Example (00:00:05):
Growth loops compound. A marketplace growth loop connects supply, demand and liquidity so product-market fit survey methods matter less than liquidity.
Lenny (00:01:00):
Why are loops better than funnels?
Cy Example (00:01:20):
Funnels leak; loops reinvest output as input."""),
}


def write_fixture(root):
    for slug, (guest, title, body) in EPISODES.items():
        d = root / "episodes" / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "transcript.md").write_text(
            f"---\nguest: {guest}\ntitle: {title} | {guest}\nyoutube_url: https://www.youtube.com/watch?v=abc{slug[:2]}\n"
            f"video_id: abc\npublish_date: 2023-04-21\nduration_seconds: 600\nkeywords:\n- growth\n---\n\n# {title}\n\n## Transcript\n\n{body}\n"
        )
