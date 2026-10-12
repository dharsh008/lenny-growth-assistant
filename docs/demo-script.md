# Demo video script (2–3 min, camera on)
0:00 **Problem (20s):** "Product teams can't search 300 hour-long transcripts, and ChatGPT can't cite Lenny's guests. I built an assistant that answers only from the transcripts, with timestamped sources, and turns answers into essays and pages."
0:20 **Local Ollama (30s):** show `ollama ps` / header toggle = Local · qwen3:8b ●. Ask "How do top teams find product-market fit?" → streamed answer, click a citation chip, open the YouTube timestamp.
0:50 **Grounding (20s):** ask "What is the capital of France?" → refusal with closest episodes.
1:10 **Ship 30 skill (30s):** "Write a Ship 30 for 30 essay on activation" → show steps, viewer, skill report.
1:40 **Artifact + security (30s):** "Create an HTML one-pager" → render; ask for one with `<script>` → show Security panel removing it.
2:10 **Trade-off (30s):** "I chose deterministic orchestration + BM25 over letting a small local model call tools / embeddings: it keeps grounding reliable on a laptop model and is explainable; the cost is weaker paraphrase recall, which the evidence gate and the weak/strong tiers compensate for. Next: embeddings re-rank and auth."
2:40 Wrap + repo link.
