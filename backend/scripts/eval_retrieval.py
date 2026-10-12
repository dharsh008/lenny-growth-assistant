"""Retrieval smoke-eval against the real index:  python -m scripts.eval_retrieval
Reports hit@k for in-scope questions (expected guest in top-k) and gate behaviour for off-topic ones."""
import asyncio

from app.retrieval.service import RetrievalService

IN_SCOPE = [
    ("How do I find product-market fit?", "Todd Jackson"),
    ("What is the best way to run a pre-mortem?", "Shreyas Doshi"),
    ("What did Brian Chesky say about founder mode?", "Brian Chesky"),
    ("How did Superhuman measure product-market fit with a survey?", "Rahul Vohra"),
    ("How did Snyk build product-led growth?", "Ben Williams"),
    ("How does Duolingo think about retention?", "Jackson Shuttleworth"),
    ("What is the original growth hacker's approach to experimentation?", "Sean Ellis"),
    ("How should I think about hiring a head of product?", "Lauren Ipsen"),
]
OFF_TOPIC = ["how to bake sourdough bread", "what is the capital of France", "who won the world cup in 2022", "zzzxqv blorptastic"]


async def main(k: int = 8) -> None:
    r = RetrievalService()
    await r.reload()
    hits = 0
    for q, guest in IN_SCOPE:
        res = r.search(q, k)
        ok = any(guest.lower() in h.guest.lower() for h in res.hits)
        hits += ok
        print(f"{'PASS' if ok else 'MISS'}  [{res.strength:6}] {q}  (expected {guest})")
    print(f"\nin-scope hit@{k}: {hits}/{len(IN_SCOPE)}")
    gated = 0
    for q in OFF_TOPIC:
        res = r.search(q, k)
        flagged = res.strength in ("none", "weak")
        gated += flagged
        print(f"{'GATED' if flagged else 'LEAK '}  [{res.strength:6}] {q}")
    print(f"off-topic flagged weak/none (LLM verifies 'weak'): {gated}/{len(OFF_TOPIC)}")


if __name__ == "__main__":
    asyncio.run(main())
