"""Snapdeal Virtual KAM - FastAPI backend.

Browser (mic -> STT) -> POST /api/chat -> Gemini (function calling loop) -> reply text -> Browser (TTS)
"""

import asyncio
import re
from datetime import datetime, timedelta, timezone
import json
import os
import uuid
from pathlib import Path

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import data
import tools
from prompt import SYSTEM_PROMPT

load_dotenv(override=True)  # .env wins over any stale system env vars
GEMINI_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite")
URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FALLBACK_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_FALLBACK_MODELS", "gemini-3.5-flash,gemini-flash-latest"
    ).split(",")
    if m.strip()
]
RETRY_STATUS = {429, 500, 502, 503, 504}
# Backup/alternative provider: Groq (OpenAI-compatible, free tier, very fast). Set LLM_PROVIDER=groq to use it.
PROVIDER = os.getenv("LLM_PROVIDER", "gemini").lower()
GROQ_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_URL = "https://api.groq.com/openai/v1"
MAX_TOOL_ROUNDS = 6

app = FastAPI(title="Snapdeal Virtual KAM")
SESSIONS: dict[str, dict] = {}
DEBUG_ENDPOINTS = (
    os.getenv("DEBUG_ENDPOINTS", "1") == "1"
)  # set DEBUG_ENDPOINTS=0 when deployed


class ChatIn(BaseModel):
    session_id: str
    message: str
    lang: str = "hi"  # "hi" = Hinglish, "en" = English


def get_session(sid: str) -> dict:
    return SESSIONS.setdefault(
        sid, {"history": [], "actions": [], "summary": None, "user_turns": 0}
    )


CLAIM_RESTOCK = re.compile(r"restock (task|plan)", re.I)
CLAIM_DONE = re.compile(r"(creat|set up|set for|scheduled|logged|added)", re.I)
CLAIM_SUMMARY = re.compile(r"(whatsapp|summary)", re.I)
CLAIM_SEND = re.compile(r"(send|sent|sending)", re.I)
CLOSING = re.compile(
    r"(thank you|goodbye|talk soon|have a good day|take care|धन्यवाद)", re.I
)


def claim_check(state: dict, reply: str):
    """Claim-vs-action verifier: if the bot SAYS it did something but never called the tool, return a corrective nudge.
    Max 3 nudges per call so it can never loop or add much latency."""
    if state.get("user_turns", 0) < 3 or state.get("nudges", 0) >= 3:
        return None
    acts = {x["tool"] for x in state["actions"]}
    asking = reply.rstrip().endswith("?")  # a question is a proposal, not a claim
    if (
        CLAIM_RESTOCK.search(reply)
        and CLAIM_DONE.search(reply)
        and not asking
        and "create_restock_plan" not in acts
    ):
        return (
            "[SYSTEM CHECK] You told the seller a restock task is created or being created, but you did not call create_restock_plan. "
            "If the seller has agreed, call it now (skus = the agreed products, target_date from the calendar, seller_confirmation = their own words), then continue. "
            "If they have not agreed yet, correct yourself and ask."
        )
    closing = "schedule_followup" in acts or CLOSING.search(reply)
    if (
        CLAIM_SUMMARY.search(reply)
        and CLAIM_SEND.search(reply)
        and closing
        and not state.get("summary")
    ):
        return (
            "[SYSTEM CHECK] You promised a WhatsApp summary but did not send it. Call wrap_up_call now "
            "(issues, actions, next_steps), then give the 2-3 sentence recap."
        )
    if (
        "schedule_followup" in acts
        and "create_restock_plan" not in acts
        and state.get("user_turns", 0) >= 4
        and not state.get("closing_nudged")
    ):
        state["closing_nudged"] = True
        return (
            "[SYSTEM CHECK] You are closing the call but no restock plan exists. If the seller agreed to restock, call create_restock_plan now "
            "with their own words as seller_confirmation, then finish the closing. If they did not agree, just continue."
        )
    return None


def ensure_summary(state: dict) -> None:
    """Safety net: if the seller committed (restock plan) and a follow-up exists but no summary was sent, send it."""
    acts = state["actions"]
    restock = [x for x in acts if x["tool"] == "create_restock_plan"]
    follow = [x for x in acts if x["tool"] == "schedule_followup"]
    if (
        follow
        and not state.get("summary")
        and (restock or state.get("user_turns", 0) >= 4)
    ):
        if restock:
            names = ", ".join(x["name"] for x in restock[-1]["detail"]["skus"])
            first = f"Restock {names} by {restock[-1]['detail']['target_date']}"
        else:
            first = "Restock the top out-of-stock products first"
        tools.send_summary_whatsapp(
            state,
            issues="Sales fell about 32% this month, mainly because of out-of-stock products (~Rs 55k) and higher cancellations (~Rs 9k).",
            actions=[
                first,
                "Keep inventory in sync and pause listings at zero stock to cut cancellations",
                "Run a promotion on bestsellers after restock and review price on 2 top SKUs",
            ],
            next_steps=f"AI KAM follow-up call on {follow[-1]['detail']['date']}.",
        )


def fallback_confirmation(new_actions: list, lang: str) -> str:
    """Spoken confirmation built only from what the tools really did (used if the LLM call fails after a tool ran)."""
    en = lang == "en"
    restock = next((x for x in new_actions if x["tool"] == "create_restock_plan"), None)
    if restock:
        d = restock["detail"]
        names = " and ".join(x["name"] for x in d["skus"][:2]) + (
            " and more" if len(d["skus"]) > 2 else ""
        )
        return (
            f"Done Rajesh ji, I have created the restock task for {names} by {d['target_date']}. Shall we also fix the cancellations?"
            if en
            else f"हो गया Rajesh ji, मैंने {names} का restock task {d['target_date']} तक के लिए बना दिया है। अब cancellations ठीक करने पर बात करें?"
        )
    last = new_actions[-1]
    return (
        f"Done Rajesh ji. {last['label']}."
        if en
        else f"हो गया Rajesh ji। {last['label']}।"
    )


def system_text(lang: str) -> str:
    lang_note = (
        "\n\nCURRENT LANGUAGE MODE: ENGLISH ONLY. Reply ONLY in simple English using Latin letters. NEVER use Hindi or Devanagari, even in the greeting. Ignore the Hinglish style examples above."
        if lang == "en"
        else "\n\nCURRENT LANGUAGE MODE: Hinglish (Hindi in Devanagari + common English business words in Latin)."
    )
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    cal = "; ".join(
        f"{(ist + timedelta(days=i)):%a %d %b} = {(ist + timedelta(days=i)):%Y-%m-%d}"
        for i in range(0, 9)
    )
    today = f"\n\nTODAY: {ist:%A, %d %B %Y} (IST). CALENDAR (use exactly, never guess dates): {cal}. Pass target_date as YYYY-MM-DD from this calendar."
    ctx = json.dumps(
        {
            "metrics": tools.get_seller_metrics({"actions": []}),
            "health_scan": tools.scan_seller_health({"actions": []}),
        },
        ensure_ascii=False,
    )
    context = f"\n\nSELLER CONTEXT (already loaded for you, do NOT call get_seller_metrics or scan_seller_health): {ctx}"
    return SYSTEM_PROMPT + context + today + lang_note


def clean_history(history: list) -> list:
    """Gemini rejects model/user turns with no parts (400). Drop any such turn before sending."""
    return [h for h in history if h.get("parts")]


async def call_gemini(history: list, lang: str) -> dict:
    body = {
        "systemInstruction": {"parts": [{"text": system_text(lang)}]},
        "contents": clean_history(history),
        "tools": [{"functionDeclarations": tools.DECLARATIONS}],
        "generationConfig": {
            "temperature": 0.6,
            "maxOutputTokens": 1024,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    r = None
    last_err = None
    async with httpx.AsyncClient(timeout=20) as client:
        for model in [MODEL] + FALLBACK_MODELS:
            for attempt in range(
                2
            ):  # retry transient problems once, then go to the next model
                try:
                    r = await client.post(
                        URL.format(model=model), params={"key": GEMINI_KEY}, json=body
                    )
                except httpx.TimeoutException:
                    print(f"Gemini timeout on {model} (attempt {attempt + 1})")
                    last_err = f"timeout on {model}"
                    r = None
                    continue
                except httpx.HTTPError as e:
                    raise RuntimeError(
                        f"Network problem talking to Gemini: {type(e).__name__} {e!r}"
                    )
                if (
                    r.status_code == 400
                    and "thinking" in r.text.lower()
                    and "thinkingConfig" in body["generationConfig"]
                ):
                    body["generationConfig"].pop(
                        "thinkingConfig"
                    )  # model rejects thinkingBudget
                    continue
                if (
                    r.status_code == 429
                ):  # quota/rate limit: retrying the same model only burns more quota -> next model
                    print(f"Gemini 429 on {model}: {r.text[:160]}")
                    last_err = f"429 (quota/rate limit) on {model}"
                    break
                if r.status_code in RETRY_STATUS:
                    print(f"Gemini {r.status_code} on {model}, retry {attempt + 1}")
                    last_err = f"{r.status_code} on {model}"
                    await asyncio.sleep(0.5)
                    continue
                break
            if r is not None and r.status_code not in RETRY_STATUS:
                break
    if r is None:
        raise RuntimeError(
            f"Gemini did not respond in time ({last_err}). Try a faster model in .env"
        )
    if r.status_code != 200:
        raise RuntimeError(f"Gemini {r.status_code}: {r.text[:300]}")
    return r.json()


async def run_agent(state: dict, lang: str) -> tuple[str, list]:
    """Loop: call LLM -> execute any tool calls -> feed results back -> until it returns text."""
    tool_events = []
    for _ in range(MAX_TOOL_ROUNDS):
        resp = await call_gemini(state["history"], lang)
        cands = resp.get("candidates") or []
        if not cands or "content" not in cands[0]:
            return (
                "माफ़ कीजिए, मैं ठीक से सुन नहीं पाया। क्या आप दोबारा बोल सकते हैं?",
                tool_events,
            )
        content = cands[0]["content"]
        content.setdefault("role", "model")
        parts = content.get("parts") or []
        if (
            not parts
        ):  # e.g. cut off by token limit: storing an empty turn would break every later call
            return (
                "माफ़ कीजिए, मैं ठीक से सुन नहीं पाया। क्या आप दोबारा बोल सकते हैं?",
                tool_events,
            )
        state["history"].append(content)

        calls = [p["functionCall"] for p in parts if "functionCall" in p]
        if not calls:
            text = " ".join(p.get("text", "") for p in parts).strip()
            return text, tool_events

        responses = []
        for c in calls:
            name, args = c["name"], c.get("args", {}) or {}
            fn = tools.REGISTRY.get(name)
            try:
                result = fn(state, **args) if fn else {"error": f"unknown tool {name}"}
            except Exception as e:  # keep the call alive even if a tool breaks
                result = {"error": str(e)}
            tool_events.append({"tool": name, "args": args})
            responses.append(
                {"functionResponse": {"name": name, "response": {"result": result}}}
            )
        state["history"].append({"role": "user", "parts": responses})
    return "एक मिनट, मैं थोड़ा और देखता हूँ।", tool_events


def _lower_types(x):
    if isinstance(x, dict):
        return {
            k: (v.lower() if k == "type" and isinstance(v, str) else _lower_types(v))
            for k, v in x.items()
        }
    if isinstance(x, list):
        return [_lower_types(i) for i in x]
    return x


def oa_tools() -> list:
    return [
        {
            "type": "function",
            "function": {
                "name": d["name"],
                "description": d["description"],
                "parameters": _lower_types(
                    d.get("parameters", {"type": "OBJECT", "properties": {}})
                ),
            },
        }
        for d in tools.DECLARATIONS
    ]


async def run_agent_oa(state: dict, text: str, lang: str) -> tuple[str, list]:
    """Same agent loop as run_agent, but against an OpenAI-compatible API (Groq)."""
    hist = state.setdefault("oa", [])
    hist.append({"role": "user", "content": text})
    events = []
    system = {"role": "system", "content": system_text(lang)}
    async with httpx.AsyncClient(timeout=30) as client:
        for _ in range(MAX_TOOL_ROUNDS):
            r = None
            for attempt in range(3):
                try:
                    r = await client.post(
                        GROQ_URL + "/chat/completions",
                        headers={"Authorization": f"Bearer {GROQ_KEY}"},
                        json={
                            "model": GROQ_MODEL,
                            "messages": [system] + hist,
                            "tools": oa_tools(),
                            "temperature": 0.6,
                            "max_tokens": 1200,
                            **(
                                {"reasoning_effort": "low"}
                                if "gpt-oss" in GROQ_MODEL
                                else {}
                            ),
                        },
                    )
                except httpx.TimeoutException:
                    r = None
                    continue
                if r.status_code in RETRY_STATUS or (
                    r.status_code == 400 and "tool_use_failed" in r.text
                ):
                    print(f"Groq {r.status_code}, retry {attempt + 1}")
                    await asyncio.sleep(0.4 * (attempt + 1))
                    continue
                break
            if r is None or r.status_code != 200:
                raise RuntimeError(
                    f"Groq {getattr(r, 'status_code', 'timeout')}: {(r.text[:300] if r is not None else '')}"
                )
            msg = r.json()["choices"][0]["message"]
            hist.append(
                {k: v for k, v in msg.items() if k in ("role", "content", "tool_calls")}
            )
            calls = msg.get("tool_calls") or []
            if not calls:
                return (msg.get("content") or "").strip(), events
            for c in calls:
                name = c["function"]["name"]
                try:
                    args = json.loads(c["function"].get("arguments") or "{}")
                except Exception:
                    args = {}
                fn = tools.REGISTRY.get(name)
                try:
                    result = (
                        fn(state, **args) if fn else {"error": f"unknown tool {name}"}
                    )
                except Exception as e:
                    result = {"error": str(e)}
                events.append({"tool": name, "args": args})
                hist.append(
                    {
                        "role": "tool",
                        "tool_call_id": c["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
    return "एक मिनट, मैं थोड़ा और देखता हूँ।", events


@app.post("/api/chat")
async def chat(inp: ChatIn):
    state = get_session(inp.session_id)
    t0 = state.get("user_turns", 0)
    if not inp.message.startswith("["):
        state["user_turns"] = state.get("user_turns", 0) + 1
    use_groq = PROVIDER == "groq"
    if (use_groq and not GROQ_KEY) or (not use_groq and not GEMINI_KEY):
        return {
            "reply": "API key set nahi hai. .env file mein key daaliye.",
            "tool_events": [],
            "actions": [],
            "summary": None,
            "error": "no_key",
        }

    async def turn(text: str, real: bool = True):
        if use_groq:
            return await run_agent_oa(state, text, inp.lang)
        state["history"].append({"role": "user", "parts": [{"text": text}]})
        return await run_agent(state, inp.lang)

    h0, o0, a0 = len(state["history"]), len(state.get("oa", [])), len(state["actions"])
    try:
        reply, events = await turn(inp.message)
        nudge = claim_check(state, reply)
        if nudge:
            state["nudges"] = state.get("nudges", 0) + 1
            print("CLAIM CHECK:", nudge[:70])
            reply2, events2 = await turn(nudge, real=False)
            if reply2:
                reply, events = reply2, events + events2
    except Exception as e:
        print("AGENT ERROR:", type(e).__name__, repr(e))
        new_actions = state["actions"][a0:]
        if new_actions:
            # The tool already ran (e.g. restock plan created) but the LLM failed afterwards. Don't say "technical problem":
            # confirm the real action from our own data and keep the history valid for the next turn.
            msg = fallback_confirmation(new_actions, inp.lang)
            if use_groq:
                state.setdefault("oa", []).append({"role": "assistant", "content": msg})
            else:
                state["history"].append({"role": "model", "parts": [{"text": msg}]})
            ensure_summary(state)
            return {
                "reply": msg,
                "tool_events": [{"tool": x["tool"], "args": {}} for x in new_actions],
                "actions": state["actions"],
                "summary": state["summary"],
                "error": str(e),
            }
        # nothing happened yet: undo this turn completely so the retry starts from a clean, valid history
        del state["history"][h0:]
        if "oa" in state:
            del state["oa"][o0:]
        state["user_turns"] = t0
        msg = (
            "Sorry, I missed that. Could you say that again?"
            if inp.lang == "en"
            else "माफ़ कीजिए, एक बार फिर बोलिए?"
        )
        return {
            "reply": msg,
            "tool_events": [],
            "actions": state["actions"],
            "summary": state["summary"],
            "error": str(e),
        }
    ensure_summary(state)
    if inp.message in (
        "[CALL_CONNECTED]",
        "[OUTBOUND_CALL]",
    ):  # pre-fetched on the server before the LLM speaks
        events = [
            {"tool": "get_seller_metrics", "args": {}},
            {"tool": "scan_seller_health", "args": {}},
        ] + events
    return {
        "reply": reply,
        "tool_events": events,
        "actions": state["actions"],
        "summary": state["summary"],
    }


class TTSIn(BaseModel):
    text: str
    lang: str = "hi"


@app.post("/api/tts")
async def tts(inp: TTSIn):
    """Natural Indian neural voice (Microsoft Edge TTS, no key needed). Browser falls back to its own voice on failure."""
    try:
        import edge_tts

        voice = "en-IN-PrabhatNeural" if inp.lang == "en" else "hi-IN-MadhurNeural"
        buf = bytearray()
        async for ch in edge_tts.Communicate(inp.text[:700], voice).stream():
            if ch["type"] == "audio":
                buf.extend(ch["data"])
        if not buf:
            raise RuntimeError("empty audio")
        return Response(bytes(buf), media_type="audio/mpeg")
    except Exception as e:
        print("TTS ERROR:", type(e).__name__, repr(e))
        return Response(status_code=502)


@app.get("/api/seller")
def seller():
    return tools.get_seller_metrics({"actions": []})


@app.get("/api/radar")
def radar():
    return tools.scan_seller_health({"actions": []})


@app.get("/api/new-session")
def new_session():
    return {"session_id": str(uuid.uuid4())}


@app.get("/api/llm-check")
async def llm_check():
    if not DEBUG_ENDPOINTS:
        return {"error": "disabled"}
    """Open http://127.0.0.1:8000/api/llm-check in the browser to see if Gemini works."""
    if PROVIDER == "groq":
        try:
            t = {}
            reply, _ = await run_agent_oa(t, "Say hello in one short sentence.", "en")
            return {"ok": True, "provider": "groq", "model": GROQ_MODEL, "reply": reply}
        except Exception as e:
            return {
                "ok": False,
                "provider": "groq",
                "model": GROQ_MODEL,
                "error": f"{type(e).__name__}: {e!r}",
            }
    try:
        r = await call_gemini(
            [{"role": "user", "parts": [{"text": "Say hello in one short sentence."}]}],
            "en",
        )
        return {
            "ok": True,
            "model": MODEL,
            "reply": r["candidates"][0]["content"]["parts"],
        }
    except Exception as e:
        return {"ok": False, "model": MODEL, "error": f"{type(e).__name__}: {e!r}"}


@app.get("/api/models")
async def list_models():
    if not DEBUG_ENDPOINTS:
        return {"error": "disabled"}
    """Open http://127.0.0.1:8000/api/models to see which models your key can use."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": GEMINI_KEY, "pageSize": 200},
        )
    if r.status_code != 200:
        return {"error": r.text[:300]}
    return [
        m["name"].replace("models/", "")
        for m in r.json().get("models", [])
        if "generateContent" in m.get("supportedGenerationMethods", [])
    ]


@app.get("/api/groq-models")
async def groq_models():
    if not DEBUG_ENDPOINTS:
        return {"error": "disabled"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(
            GROQ_URL + "/models", headers={"Authorization": f"Bearer {GROQ_KEY}"}
        )
    return (
        [m["id"] for m in r.json().get("data", [])]
        if r.status_code == 200
        else {"error": r.text[:300]}
    )


@app.get("/health")
def health():
    return {
        "ok": True,
        "provider": PROVIDER,
        "model": GROQ_MODEL if PROVIDER == "groq" else MODEL,
        "has_key": bool(GROQ_KEY if PROVIDER == "groq" else GEMINI_KEY),
    }


STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
