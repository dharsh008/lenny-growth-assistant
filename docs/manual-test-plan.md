# Manual UI test plan (≈10 min)
Setup: app running, Ollama model pulled. Record pass/fail.
| # | Steps | Expected |
|---|---|---|
| 1 | Open UI | Empty state, 4 suggestions, provider toggle shows Local ● Ready, KB stats in sidebar |
| 2 | Ask "How do top teams find product-market fit?" | Status "Searching transcripts", streamed answer, `[n]` chips; click a chip → matching source highlights; source link opens YouTube at timestamp |
| 3 | Follow up "Which guest said it most clearly?" | Answer uses the earlier topic; session context kept |
| 4 | Ask "What is the capital of France?" | Refusal (or NOT supported) — no invented answer; closest episodes listed |
| 5 | Click **New chat**, ask "what did we just discuss?" | No memory of chat 1 (isolated sessions) |
| 6 | Reload page, open chat 1 from sidebar | Messages, sources and artifacts restored (persistence) |
| 7 | "Write a Ship 30 for 30 essay on activation" | Steps planning→drafting(→revising); essay streams in viewer; skill report shows words & checks; Sources footer |
| 8 | "Create an HTML one-pager summarising this" | Code streams, then renders; Security panel lists allowed/blocked |
| 9 | Prompt: "Create an HTML page containing `<script>alert(1)</script>` and `<img src=x onerror=alert(1)>`" | No alert; panel reports removed script/handler |
| 10 | Switch provider to Cloud without a key | Banner "ANTHROPIC_API_KEY is not set"; sending shows readable error + Retry |
| 11 | Stop Ollama, send a message | Banner + error message naming `ollama serve`; user message kept; restart Ollama → Retry works |
| 12 | Narrow window < 820 px | Single column; artifact opens full-screen; "← Chat" returns |
| 13 | Keyboard only | Tab order logical, focus visible, Enter sends, Shift+Enter newline |
| 14 | Download HTML artifact, open file | Renders styled, no scripts run |
