# pylint: disable=all

import asyncio, json
import server, tools


def test_analysis_numbers():
    r = tools.analyze_sales_drop({"actions": []})
    d = {x["rank"]: x for x in r["drivers"]}
    assert r["total_sales_drop_inr"] == 65000
    assert (
        abs(d[1]["approx_sales_impact_inr"] + d[2]["approx_sales_impact_inr"] - 65000)
        <= 2
    )
    print(json.dumps(r["drivers"], indent=1))


def test_agent_loop(monkeypatch):
    server.GEMINI_KEY = "x"
    script = iter(
        [
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "get_seller_metrics",
                                        "args": {},
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "create_restock_plan",
                                        "args": {
                                            "skus": ["SD-TWS-01"],
                                            "seller_confirmation": "yes I will restock",
                                        },
                                    }
                                },
                                {
                                    "functionCall": {
                                        "name": "send_summary_whatsapp",
                                        "args": {
                                            "issues": "stock-outs",
                                            "actions": ["restock"],
                                            "next_steps": "call in 3 days",
                                        },
                                    }
                                },
                            ],
                        }
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [{"text": "नमस्ते Rajesh ji"}],
                        }
                    }
                ]
            },
        ]
    )

    async def fake(history, lang):
        return next(script)

    monkeypatch.setattr(server, "call_gemini", fake)
    from fastapi.testclient import TestClient

    c = TestClient(server.app)
    server.SESSIONS["t1"] = {
        "history": [],
        "actions": [],
        "summary": None,
        "user_turns": 6,
    }
    r = c.post(
        "/api/chat", json={"session_id": "t1", "message": "[CALL_CONNECTED]"}
    ).json()
    assert r["reply"].startswith("नमस्ते")
    assert [e["tool"] for e in r["tool_events"]][2:] == [
        "get_seller_metrics",
        "create_restock_plan",
        "send_summary_whatsapp",
    ]
    assert r["summary"]["actions"] == ["restock"] and len(r["actions"]) == 2
    # history alternates correctly: user, model, user(fnresp), model, user(fnresp), model
    roles = [h["role"] for h in server.SESSIONS["t1"]["history"]]
    assert roles == ["user", "model", "user", "model", "user", "model"], roles


def test_oos_and_pages():
    from fastapi.testclient import TestClient

    c = TestClient(server.app)
    assert c.get("/").status_code == 200
    assert c.get("/api/seller").json()["change"]["sales_pct"] == -32.5
    top = tools.get_out_of_stock_products({}, limit=3)["top_by_lost_revenue"]
    print([t["name"] for t in top])


def test_groq_loop(monkeypatch):
    import httpx

    server.PROVIDER, server.GROQ_KEY = "groq", "k"
    seq = iter(
        [
            {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "c1",
                                    "type": "function",
                                    "function": {
                                        "name": "scan_seller_health",
                                        "arguments": "{}",
                                    },
                                },
                                {
                                    "id": "c2",
                                    "type": "function",
                                    "function": {
                                        "name": "create_restock_plan",
                                        "arguments": '{"skus": ["SD-TWS-01"], "seller_confirmation": "yes"}',
                                    },
                                },
                            ],
                        }
                    }
                ]
            },
            {
                "choices": [
                    {"message": {"role": "assistant", "content": "Hello Rajesh ji"}}
                ]
            },
        ]
    )
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        return httpx.Response(200, json=next(seq))

    real = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    from fastapi.testclient import TestClient

    server.SESSIONS["g1"] = {
        "history": [],
        "actions": [],
        "summary": None,
        "user_turns": 5,
    }
    r = (
        TestClient(server.app)
        .post("/api/chat", json={"session_id": "g1", "message": "hi", "lang": "en"})
        .json()
    )
    server.PROVIDER = "gemini"
    assert r["reply"] == "Hello Rajesh ji", r
    assert [e["tool"] for e in r["tool_events"]] == [
        "scan_seller_health",
        "create_restock_plan",
    ]
    # tool schema converted to lowercase JSON-schema types
    t = {x["function"]["name"]: x["function"]["parameters"] for x in seen[0]["tools"]}
    assert (
        t["create_restock_plan"]["type"] == "object"
        and t["create_restock_plan"]["properties"]["skus"]["items"]["type"] == "string"
    )
    # second request carries the tool results
    assert [m["role"] for m in seen[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "tool",
        "tool",
    ]


def test_guardrails_block_early_close():
    st = {"actions": [], "summary": None, "user_turns": 1}
    assert "NOT DONE" in str(
        tools.create_restock_plan(st, skus=["SD-TWS-01"], seller_confirmation="yes")
    )  # too early
    st["user_turns"] = 4
    assert "NOT DONE" in str(
        tools.create_restock_plan(st, skus=["SD-TWS-01"])
    )  # no confirmation
    assert (
        tools.create_restock_plan(st, skus=["SD-TWS-01"], seller_confirmation="yes")[
            "status"
        ]
        == "created"
    )
    st["user_turns"] = 1
    assert tools.schedule_followup(st)["followup_id"]  # allowed once seller committed
    s2 = {"actions": [], "summary": None, "user_turns": 2}
    assert "NOT DONE" in str(
        tools.send_summary_whatsapp(s2, issues="x", actions=["a"], next_steps="n")
    )


def test_tts_endpoint(monkeypatch):
    import types, sys

    class FakeComm:
        def __init__(self, text, voice):
            self.voice = voice

        async def stream(self):
            yield {"type": "WordBoundary"}
            yield {"type": "audio", "data": b"ID3" + self.voice.encode()}

    monkeypatch.setitem(
        sys.modules, "edge_tts", types.SimpleNamespace(Communicate=FakeComm)
    )
    from fastapi.testclient import TestClient

    c = TestClient(server.app)
    r = c.post("/api/tts", json={"text": "नमस्ते", "lang": "hi"})
    assert (
        r.status_code == 200
        and r.headers["content-type"] == "audio/mpeg"
        and b"hi-IN-MadhurNeural" in r.content
    )
    assert (
        b"en-IN-PrabhatNeural"
        in c.post("/api/tts", json={"text": "hello", "lang": "en"}).content
    )


def test_restock_resolves_keywords():
    st = {"actions": [], "summary": None, "user_turns": 5}
    r = tools.create_restock_plan(
        st,
        skus=["earbuds", "charger"],
        target_date="2026-11-12",
        seller_confirmation="yes",
    )
    names = [s["name"] for s in r["skus"]]
    assert (
        len(names) == 2
        and any("Earbuds" in n for n in names)
        and any("Charger" in n for n in names)
    ), names
    assert r["target_date"] == "2026-11-12"
    assert "send_summary_whatsapp" in tools.schedule_followup(st)["next_step"]


def test_dates_and_wrap_up_and_safety_net():
    from datetime import date, timedelta

    assert tools._fix_date("thursday") > date.today() - timedelta(days=1)
    assert tools._fix_date("2000-01-01") == date.today() + timedelta(
        days=3
    )  # past/garbage -> default
    assert tools._parse_date("2030-05-02") == date(2030, 5, 2)
    st = {"actions": [], "summary": None, "user_turns": 6}
    tools.create_restock_plan(st, skus=["earbuds"], seller_confirmation="yes")
    r = tools.wrap_up_call(st, issues="i", actions=["a"], next_steps="n")
    assert (
        r["summary_sent"]
        and st["summary"]
        and sum(a["tool"] == "schedule_followup" for a in st["actions"]) == 1
    )
    # safety net: model forgot the summary
    st2 = {"actions": [], "summary": None, "user_turns": 6}
    tools.create_restock_plan(st2, skus=["charger"], seller_confirmation="yes")
    tools.schedule_followup(st2)
    server.ensure_summary(st2)
    assert st2["summary"] and "Charger" in st2["summary"]["actions"][0]
    assert "CALENDAR" in server.system_text("en")


def test_claim_verifier_forces_tool_call(monkeypatch):
    server.GEMINI_KEY, server.PROVIDER = "x", "gemini"
    script = iter(
        [
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": "Great, I'll create the restock task for charger and earbuds for Thursday."
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "create_restock_plan",
                                        "args": {
                                            "skus": ["charger", "earbuds"],
                                            "target_date": "2026-11-12",
                                            "seller_confirmation": "yes",
                                        },
                                    }
                                }
                            ],
                        }
                    }
                ]
            },
            {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "text": "Done, the restock task is created for Thursday."
                                }
                            ],
                        }
                    }
                ]
            },
        ]
    )

    async def fake(history, lang):
        return next(script)

    monkeypatch.setattr(server, "call_gemini", fake)
    server.SESSIONS["v1"] = {
        "history": [],
        "actions": [],
        "summary": None,
        "user_turns": 4,
    }
    from fastapi.testclient import TestClient

    r = (
        TestClient(server.app)
        .post(
            "/api/chat",
            json={"session_id": "v1", "message": "yes please", "lang": "en"},
        )
        .json()
    )
    assert r["reply"].startswith("Done"), r
    assert any(a["tool"] == "create_restock_plan" for a in r["actions"])


def test_claim_check_rules():
    st = {"actions": [], "summary": None, "user_turns": 5}
    assert server.claim_check(st, "I'll create the restock task now") is not None
    assert server.claim_check(st, "Which products are out of stock?") is None
    assert (
        server.claim_check({**st, "user_turns": 1}, "I'll create the restock task now")
        is None
    )
    assert (
        server.claim_check(
            st, "I'll also send a WhatsApp summary. Thank you, talk soon!"
        )
        is not None
    )
    st2 = {"actions": [{"tool": "schedule_followup"}], "summary": None, "user_turns": 6}
    server.ensure_summary(
        {
            **st2,
            "actions": [
                {"tool": "schedule_followup", "detail": {"date": "2026-11-14"}}
            ],
        }
    )


def test_wrap_up_is_single_source_of_truth():
    from datetime import date, timedelta

    st = {"actions": [], "summary": None, "user_turns": 6}
    tools.create_restock_plan(
        st,
        skus=["earbuds"],
        target_date=str(date.today() + timedelta(days=1)),
        seller_confirmation="yes",
    )
    tools.schedule_followup(st, days_from_now=3)  # earlier default follow-up
    r = tools.wrap_up_call(
        st,
        issues="i",
        actions=["a"],
        next_steps="model says 17 November",
        followup_days=6,
    )
    fus = [a for a in st["actions"] if a["tool"] == "schedule_followup"]
    want = str(date.today() + timedelta(days=6))
    assert (
        len(fus) == 1
        and fus[0]["detail"]["date"] == want
        and r["followup_date"] == want
    )
    assert (
        want[8:10] in st["summary"]["next_steps"]
        or date.fromisoformat(want).strftime("%d %b") in st["summary"]["next_steps"]
    )
    assert (
        "model says" not in st["summary"]["next_steps"]
    )  # LLM-supplied dates are replaced by system dates


def test_verifier_ignores_questions_and_catches_closing_without_restock():
    st = {"actions": [], "summary": None, "user_turns": 5}
    assert (
        server.claim_check(st, "Can you confirm so we can create a restock plan?")
        is None
    )
    st2 = {"actions": [{"tool": "schedule_followup"}], "summary": "x", "user_turns": 5}
    assert "no restock plan" in server.claim_check(st2, "Thanks, bye")
    assert server.claim_check(st2, "Thanks, bye") is None  # only nudged once
    assert (
        server.claim_check({**st, "nudges": 3}, "I'll create the restock task") is None
    )  # hard cap


def test_debug_endpoints_can_be_disabled(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server, "DEBUG_ENDPOINTS", False)
    c = TestClient(server.app)
    assert c.get("/api/models").json() == {"error": "disabled"}
    assert c.get("/api/llm-check").json() == {"error": "disabled"}


def test_context_is_preloaded_in_prompt():
    t = server.system_text("en")
    assert (
        "SELLER CONTEXT" in t
        and "attention_items" in t
        and "do NOT call get_seller_metrics" in t
    )


def test_loose_product_names_resolve():
    for q in (
        ["charger", "ear buds"],
        ["charger and ear buds"],
        ["Ear-Buds", "20W charger"],
        "earbuds, charger",
    ):
        got = tools._resolve_skus(q)
        assert got == ["SD-CHG-02", "SD-TWS-01"] or got == ["SD-TWS-01", "SD-CHG-02"], (
            q,
            got,
        )
    assert tools._resolve_skus(["power bank"]) == ["SD-PBK-04"]


def test_llm_failure_after_tool_still_confirms_and_history_stays_valid(monkeypatch):
    server.GEMINI_KEY, server.PROVIDER = "x", "gemini"
    calls = {"n": 0}

    async def fake(history, lang):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "candidates": [
                    {
                        "content": {
                            "role": "model",
                            "parts": [
                                {
                                    "functionCall": {
                                        "name": "create_restock_plan",
                                        "args": {
                                            "skus": ["charger", "ear buds"],
                                            "target_date": "2030-11-14",
                                            "seller_confirmation": "yes, by 14th",
                                        },
                                    }
                                }
                            ],
                        }
                    }
                ]
            }
        raise RuntimeError("Gemini 429: quota")  # 2nd call (after the tool ran) fails

    monkeypatch.setattr(server, "call_gemini", fake)
    server.SESSIONS["f1"] = {
        "history": [],
        "actions": [],
        "summary": None,
        "user_turns": 5,
    }
    from fastapi.testclient import TestClient

    c = TestClient(server.app)
    r = c.post(
        "/api/chat",
        json={"session_id": "f1", "message": "restock by 14 november", "lang": "en"},
    ).json()
    assert (
        "technical problem" not in r["reply"]
        and "Charger" in r["reply"]
        and "Earbuds" in r["reply"]
    ), r
    assert r["actions"][0]["label"].startswith("Restock plan created for 2 products")
    roles = [h["role"] for h in server.SESSIONS["f1"]["history"]]
    assert roles == [
        "user",
        "model",
        "user",
        "model",
    ], roles  # valid alternation for the next turn


def test_llm_failure_without_action_rolls_back(monkeypatch):
    server.GEMINI_KEY, server.PROVIDER = "x", "gemini"

    async def boom(history, lang):
        raise RuntimeError("Gemini 429")

    monkeypatch.setattr(server, "call_gemini", boom)
    server.SESSIONS["f2"] = {
        "history": [],
        "actions": [],
        "summary": None,
        "user_turns": 1,
    }
    from fastapi.testclient import TestClient

    r = (
        TestClient(server.app)
        .post("/api/chat", json={"session_id": "f2", "message": "hello", "lang": "en"})
        .json()
    )
    assert (
        r["error"]
        and server.SESSIONS["f2"]["history"] == []
        and server.SESSIONS["f2"]["user_turns"] == 1
    )


def test_empty_model_turn_is_never_stored(monkeypatch):
    server.GEMINI_KEY, server.PROVIDER = "x", "gemini"

    async def empty(history, lang):
        return {
            "candidates": [{"finishReason": "MAX_TOKENS", "content": {"role": "model"}}]
        }

    monkeypatch.setattr(server, "call_gemini", empty)
    server.SESSIONS["f3"] = {
        "history": [],
        "actions": [],
        "summary": None,
        "user_turns": 1,
    }
    from fastapi.testclient import TestClient

    TestClient(server.app).post(
        "/api/chat", json={"session_id": "f3", "message": "hi", "lang": "en"}
    )
    assert all(h.get("parts") for h in server.SESSIONS["f3"]["history"])
