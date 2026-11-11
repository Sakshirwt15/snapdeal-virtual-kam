"""Agent tools. The LLM calls these; they read mock data and 'take actions' (mocked)."""
import itertools
import re
from datetime import date, timedelta

import data

_task_ids = itertools.count(1001)


def _pct(a, b):
    return round((b - a) / a * 100, 1) if a else 0.0


def get_seller_metrics(state, **_):
    lm, tm = data.SELLER_METRICS["last_month"], data.SELLER_METRICS["this_month"]
    return {
        "seller": data.SELLER,
        "last_month": lm,
        "this_month": tm,
        "change": {
            "sales_pct": _pct(lm["sales"], tm["sales"]),
            "orders_pct": _pct(lm["orders"], tm["orders"]),
            "cancellation_rate_points": tm["cancellation_rate"] - lm["cancellation_rate"],
            "oos_products_added": tm["oos_products"] - lm["oos_products"],
            "sales_lost_inr": lm["sales"] - tm["sales"],
        },
        "currency": "INR",
    }


def analyze_sales_drop(state, **_):
    """Split the sales drop into 'cancellation' effect vs 'lost demand/visibility' effect.

    ASSUMPTION: 'orders' in the brief are net (delivered/valid) orders, so
    gross = net / (1 - cancellation_rate).
    """
    lm, tm = data.SELLER_METRICS["last_month"], data.SELLER_METRICS["this_month"]
    asp = tm["asp"]
    gross_lm = lm["orders"] / (1 - lm["cancellation_rate"] / 100)
    gross_tm = tm["orders"] / (1 - tm["cancellation_rate"] / 100)
    # net orders this month if cancellations had stayed at last month's rate
    net_tm_if_old_rate = gross_tm * (1 - lm["cancellation_rate"] / 100)
    cancel_effect_orders = net_tm_if_old_rate - tm["orders"]
    demand_effect_orders = lm["orders"] - net_tm_if_old_rate
    total_drop = lm["sales"] - tm["sales"]
    cancel_inr = round(cancel_effect_orders * asp)
    demand_inr = round(demand_effect_orders * asp)

    oos_rev = sum(p["price"] * p["units_last_month"] for p in data.OOS_PRODUCTS)
    new_oos = [p for p in data.OOS_PRODUCTS if p["sku"] not in data.ALREADY_OOS_LAST_MONTH]
    new_oos_rev = sum(p["price"] * p["units_last_month"] for p in new_oos)

    drivers = [
        {
            "rank": 1,
            "driver": "Out-of-stock products -> fewer orders and lower search visibility",
            "approx_sales_impact_inr": demand_inr,
            "share_of_drop_pct": round(demand_inr / total_drop * 100),
            "evidence": f"OOS products went {lm['oos_products']} -> {tm['oos_products']}; impressions down ~30% while CTR and conversion are flat.",
        },
        {
            "rank": 2,
            "driver": "Higher cancellations (mostly seller-side stock-outs)",
            "approx_sales_impact_inr": cancel_inr,
            "share_of_drop_pct": round(cancel_inr / total_drop * 100),
            "evidence": f"Cancellation rate {lm['cancellation_rate']}% -> {tm['cancellation_rate']}%; ~61% of cancellations are 'seller cancelled, out of stock'.",
        },
        {
            "rank": 3,
            "driver": "Pricing (minor)",
            "approx_sales_impact_inr": 0,
            "share_of_drop_pct": 0,
            "evidence": "Average selling price unchanged at Rs 500; 2 top SKUs are 4-6% above market low. Not the main cause.",
        },
    ]
    return {
        "total_sales_drop_inr": total_drop,
        "gross_orders_last_month_est": round(gross_lm),
        "gross_orders_this_month_est": round(gross_tm),
        "drivers": drivers,
        "oos_skus_last_month_revenue_inr": oos_rev,
        "newly_oos_skus_count": len(new_oos),
        "newly_oos_skus_last_month_revenue_inr": new_oos_rev,
        "assumption": "Orders treated as net of cancellations; impact figures are approximate estimates.",
    }


def get_out_of_stock_products(state, limit=5, **_):
    items = sorted(data.OOS_PRODUCTS, key=lambda p: p["price"] * p["units_last_month"], reverse=True)
    limit = int(limit or 5)
    top = [
        {**p, "revenue_last_month_inr": p["price"] * p["units_last_month"]}
        for p in items[:limit]
    ]
    return {"total_oos": len(items), "top_by_lost_revenue": top}


def get_cancellation_breakdown(state, **_):
    total = sum(data.CANCELLATION_REASONS.values())
    return {
        "total_cancellations_this_month": total,
        "reasons": {k: {"count": v, "pct": round(v / total * 100)} for k, v in data.CANCELLATION_REASONS.items()},
        "insight": "Most cancellations are the seller cancelling because stock is not actually available.",
    }


def get_visibility_and_pricing(state, **_):
    return {"visibility": data.VISIBILITY, "price_check": data.PRICING_CHECK}



def scan_seller_health(state, **_):
    """Proactive scan: what needs the seller's attention right now, ranked by Rs impact, each with the next action."""
    a = analyze_sales_drop(state)
    d = {x["rank"]: x for x in a["drivers"]}
    top = get_out_of_stock_products(state, limit=3)["top_by_lost_revenue"]
    cb = get_cancellation_breakdown(state)
    stockout_pct = cb["reasons"]["seller_cancelled_out_of_stock"]["pct"]
    items = [
        {"priority": 1, "severity": "critical", "issue": "18 products out of stock (13 newly)",
         "impact_inr": d[1]["approx_sales_impact_inr"],
         "next_action": "Restock top sellers first: " + ", ".join(t["name"] for t in top)},
        {"priority": 2, "severity": "high", "issue": f"Cancellations up to 14% ({stockout_pct}% are seller stock-outs)",
         "impact_inr": d[2]["approx_sales_impact_inr"],
         "next_action": "Sync inventory and auto-pause listings at zero stock"},
        {"priority": 3, "severity": "medium", "issue": "Search impressions down ~30%",
         "impact_inr": 0, "next_action": "Relist after restock and run a promotion on bestsellers"},
        {"priority": 4, "severity": "low", "issue": "2 top SKUs priced 4-6% above market low",
         "impact_inr": 0, "next_action": "Review price on earbuds and 20W charger"},
    ]
    return {"seller": data.SELLER["name"], "health": "at_risk", "attention_items": items,
            "recoverable_inr_estimate": d[1]["approx_sales_impact_inr"] + d[2]["approx_sales_impact_inr"]}


def _too_early(state, min_turns, confirmation=None, need_confirmation=False):
    """Guardrail: actions need real seller turns (and their confirming words), else the LLM must keep talking."""
    turns = state.get("user_turns", 0)
    committed = any(a["tool"] == "create_restock_plan" for a in state.get("actions", []))
    if need_confirmation and not (confirmation or "").strip():
        return {"error": "NOT DONE. The seller has not explicitly agreed yet. Keep the conversation going, ask for their agreement, then call again with seller_confirmation = their own words."}
    if turns < min_turns and not committed:
        return {"error": f"NOT DONE. Too early to close: only {turns} seller turns so far. Continue diagnosing / recommending first. Do not mention this action to the seller yet."}
    return None


# ---- Action tools (mocked) -------------------------------------------------
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_date(v):
    """ISO date or weekday name ('thursday') -> date; None if not understood."""
    if not v:
        return None
    v = str(v).strip().lower()
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        pass
    if v in WEEKDAYS:
        today = date.today()
        return today + timedelta(days=(WEEKDAYS.index(v) - today.weekday()) % 7 or 7)
    return None


def _norm(x):
    """lowercase, drop spaces/hyphens/punctuation: 'ear buds' == 'earbuds', 'Power-Bank' == 'powerbank'."""
    return re.sub(r"[^a-z0-9]", "", str(x).lower())


_SYNONYMS = {"earbud": "earbuds", "tws": "earbuds", "airpods": "earbuds", "adapter": "charger", "adaptor": "charger",
             "powerbank": "powerbank", "wire": "cable", "wires": "cable", "glass": "temperedglass"}


def _resolve_skus(items):
    """Accept SKU ids, product names or loose keywords ('earbuds', 'ear buds', 'charger and power bank') -> real out-of-stock SKUs."""
    if isinstance(items, str):
        items = [items]
    queries = []
    for it in items or []:
        # one string may hold several products: "charger and ear buds"
        queries += [q for q in re.split(r",|&|\band\b|\+|/", str(it), flags=re.I) if q.strip()]
    out = []
    for raw in queries:
        q = _norm(raw)
        q = _SYNONYMS.get(q, q)
        if not q:
            continue
        hits = [p for p in data.OOS_PRODUCTS if q == _norm(p["sku"]) or q in _norm(p["name"]) or q in _norm(p["sku"])]
        if not hits:  # loose wording like "20W charger": every word of the query must appear in the product name
            words = [_SYNONYMS.get(w, w) for w in re.findall(r"[a-z0-9]+", raw.lower())]
            hits = [p for p in data.OOS_PRODUCTS if words and all(w in _norm(p["name"]) for w in words)]
        for p in hits:
            if p["sku"] not in out:
                out.append(p["sku"])
    return out


def _fix_date(v):
    d = _parse_date(v)
    return d if d and d >= date.today() else date.today() + timedelta(days=3)


def create_restock_plan(state, skus=None, target_date=None, seller_confirmation="", **_):
    early = _too_early(state, 3, seller_confirmation, need_confirmation=True)
    if early:
        return early
    skus = _resolve_skus(skus)  # only what the seller agreed to; empty -> top 5 below
    if not skus:
        skus = [p["sku"] for p in sorted(data.OOS_PRODUCTS, key=lambda p: p["price"] * p["units_last_month"], reverse=True)[:5]]
    by_sku = {p["sku"]: p["name"] for p in data.OOS_PRODUCTS}
    task = {
        "task_id": f"TASK-{next(_task_ids)}",
        "type": "restock",
        "skus": [{"sku": s, "name": by_sku.get(s, s)} for s in skus],
        "target_date": str(_fix_date(target_date)),
        "status": "created",
    }
    state["actions"].append({"tool": "create_restock_plan", "label": f"Restock plan created for {len(skus)} product{'s' if len(skus) != 1 else ''} by {task['target_date']}", "detail": task})
    return task


def schedule_followup(state, days_from_now=3, topic="Check restock progress and cancellation rate", **_):
    early = _too_early(state, 4)
    if early:
        return early
    when = date.today() + timedelta(days=int(days_from_now))
    rec = {"followup_id": f"FU-{next(_task_ids)}", "date": str(when), "topic": topic, "channel": "AI KAM call"}
    state["actions"].append({"tool": "schedule_followup", "label": f"Follow-up call scheduled for {when:%d %b}", "detail": rec})
    rec["next_step"] = ("Now call send_summary_whatsapp (issues, actions, next_steps) if you have not yet, "
                        "THEN speak a short recap: key issue, actions agreed, follow-up date.")
    return rec


def wrap_up_call(state, issues="", actions=None, next_steps="", followup_days=3, **_):
    """One-shot closing. Schedules/updates the follow-up AND sends the written summary.
    Dates in the summary and in the spoken recap come from the system, never from the LLM (single source of truth)."""
    early = _too_early(state, 4)
    if early:
        return early
    try:
        days = max(1, min(int(followup_days), 14))
    except (TypeError, ValueError):
        days = 3
    when = date.today() + timedelta(days=days)
    existing = next((x for x in state["actions"] if x["tool"] == "schedule_followup"), None)
    if existing:  # update in place, so there is only ever ONE follow-up
        existing["label"] = f"Follow-up call scheduled for {when:%d %b}"
        existing["detail"]["date"] = str(when)
    else:
        schedule_followup(state, days_from_now=days)
    restock = next((x for x in state["actions"] if x["tool"] == "create_restock_plan"), None)
    restock_date = restock["detail"]["target_date"] if restock else None
    truth = (f"Restock by {restock_date}. " if restock_date else "") + f"AI KAM follow-up call on {when:%d %b %Y}."
    send_summary_whatsapp(state, issues=issues, actions=actions, next_steps=truth)
    return {"status": "done", "followup_date": str(when), "restock_date": restock_date, "summary_sent": True,
            "next_step": (f"Now speak a 2-3 sentence recap: key issue, actions agreed, and use EXACTLY these dates: "
                          f"follow-up {when:%d %B}" + (f", restock {restock_date}" if restock_date else "") + ". Then say goodbye.")}


def send_summary_whatsapp(state, issues="", actions=None, next_steps="", **_):
    early = _too_early(state, 4)
    if early:
        return early
    summary = {"issues": issues, "actions": actions or [], "next_steps": next_steps}
    state["summary"] = summary
    state["actions"].append({"tool": "send_summary_whatsapp", "label": "Call summary sent on WhatsApp (mock)", "detail": summary})
    return {"status": "sent", "channel": "WhatsApp", "to": data.SELLER["phone"]}


def escalate_to_human_kam(state, reason="", **_):
    rec = {"ticket_id": f"ESC-{next(_task_ids)}", "reason": reason, "status": "assigned to human KAM"}
    state["actions"].append({"tool": "escalate_to_human_kam", "label": "Escalated to human KAM", "detail": rec})
    return rec


REGISTRY = {
    f.__name__: f
    for f in [
        get_seller_metrics, analyze_sales_drop, get_out_of_stock_products, get_cancellation_breakdown,
        get_visibility_and_pricing, scan_seller_health, create_restock_plan, schedule_followup, send_summary_whatsapp, wrap_up_call, escalate_to_human_kam,
    ]
}

# Gemini function declarations
DECLARATIONS = [
    {"name": "get_seller_metrics", "description": "Get the seller's profile and this-month vs last-month metrics (sales, orders, cancellation rate, ASP, out-of-stock count)."},
    {"name": "analyze_sales_drop", "description": "Break the sales decline into root-cause drivers with approximate Rs impact. Call this once you have the metrics, before recommending."},
    {"name": "get_out_of_stock_products", "description": "List the seller's currently out-of-stock products ranked by revenue they made last month.",
     "parameters": {"type": "OBJECT", "properties": {"limit": {"type": "INTEGER", "description": "How many top products to return (default 5)"}}}},
    {"name": "get_cancellation_breakdown", "description": "Reasons behind this month's cancellations."},
    {"name": "get_visibility_and_pricing", "description": "Impressions / CTR / conversion trend and competitor price gaps for top SKUs."},
    {"name": "scan_seller_health", "description": "Proactive health scan: ranked list of what needs the seller's attention, with Rs impact and next action. Call at the start of every call."},
    {"name": "create_restock_plan", "description": "Create a restock task for the seller. ONLY call after the seller agrees to restock.",
     "parameters": {"type": "OBJECT", "properties": {
         "skus": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "ONLY the products the seller agreed to restock: SKU ids or product keywords like earbuds, charger"},
         "target_date": {"type": "STRING", "description": "YYYY-MM-DD the seller commits to (use the calendar in the instructions)"},
         "seller_confirmation": {"type": "STRING", "description": "The seller's own words agreeing to restock (required)"}}, "required": ["seller_confirmation"]}},
    {"name": "schedule_followup", "description": "Schedule a follow-up AI KAM call. Call when wrapping up.",
     "parameters": {"type": "OBJECT", "properties": {
         "days_from_now": {"type": "INTEGER"}, "topic": {"type": "STRING"}}, "required": ["days_from_now"]}},
    {"name": "wrap_up_call", "description": "Close the call: schedules the follow-up call AND sends the written WhatsApp summary in one step. Call ONCE when the seller has agreed to the actions or wants to end. After it returns, speak a 2-3 sentence recap.",
     "parameters": {"type": "OBJECT", "properties": {
         "issues": {"type": "STRING", "description": "Key issues found, 1-2 sentences"},
         "actions": {"type": "ARRAY", "items": {"type": "STRING"}, "description": "2-3 actions agreed"},
         "next_steps": {"type": "STRING", "description": "What happens next and when, with real dates"},
         "followup_days": {"type": "INTEGER", "description": "Days until the follow-up call (default 3)"}}, "required": ["issues", "actions", "next_steps"]}},
    {"name": "escalate_to_human_kam", "description": "Hand over to a human KAM if the seller is upset, asks for a person, or the issue is outside sales (payments, account block, legal).",
     "parameters": {"type": "OBJECT", "properties": {"reason": {"type": "STRING"}}, "required": ["reason"]}},
]
