# Snapdeal Virtual KAM (mini voice agent)

Browser voice agent "Aarav": a seller calls (or the AI calls proactively), Aarav diagnoses the sales drop from mock data,
recommends 2-3 actions, takes actions with tools, and closes with a spoken recap + written summary.

## Run (Windows/Mac/Linux)
    pip install -r requirements.txt
    copy .env.example .env     (Mac/Linux: cp .env.example .env)   then put your keys in .env
    uvicorn server:app --port 8000
Open http://127.0.0.1:8000 in **Google Chrome** (not Brave / embedded browsers), use headphones, click "Start call".
Check: /health  and  /api/llm-check

## Architecture
Mic -> Web Speech API (STT) -> POST /api/chat -> LLM with tool calling (Groq gpt-oss-120b, or Gemini) -> text -> /api/tts (Edge neural voice) -> speaker -> loop
- `prompt.py`  persona, call flow, voice rules
- `tools.py`   10 tools: metrics, root-cause analysis, OOS list, cancellation breakdown, visibility/pricing, health scan,
               restock plan, follow-up, wrap-up (summary), human escalation
- `server.py`  agent loop, retries/fallbacks, guardrails (claim-vs-action verifier, guaranteed summary)
- `static/index.html` voice UI (mic orb, transcript, attention radar, agent activity, summary card)

## Assumptions (not in brief)
- From brief: sales, orders, cancellation rate, ASP, out-of-stock counts.
- Assumed mock data: seller profile, 18 OOS SKUs (price/units), cancellation reasons (61% seller stock-outs), impressions (-30%), CTR/conversion, 2 competitor price gaps.
- "Orders" are treated as net of cancellations (gross = net / (1 - cancel rate)).
- All actions (WhatsApp, restock task, follow-up, escalation) are mocked.

## Tests
    pip install pytest && pytest -q
