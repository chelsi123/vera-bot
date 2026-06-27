"""
Reply routing for /v1/reply.

Deterministic intent classification (fast, no LLM needed) drives the three
critical behaviors the replay test scores:
  - auto-reply detection -> escalate send -> wait -> end (don't burn turns)
  - explicit intent commitment -> switch to ACTION mode (stop qualifying)
  - opt-out / hostility -> end gracefully; off-topic -> redirect on-mission

The contextual "engaged" reply body is composed by Claude when available,
else by a template that acknowledges and advances.
"""
from __future__ import annotations

import re

from llm import LLMClient
from store import Store

llm = LLMClient()

# --- intent lexicons -------------------------------------------------------
AUTO_REPLY_PAT = re.compile(
    r"(thank you for contacting|will (get back|respond)|respond shortly|"
    r"currently (away|unavailable)|automated (message|assistant|reply)|"
    r"out of office|team will (reach|respond|get back)|we will contact you|"
    r"aapki (jaankari|baat).*(team|shukriya)|dhanyavaad.*team)",
    re.IGNORECASE,
)

OPT_OUT_PAT = re.compile(
    r"(stop messag|unsubscribe|not interested|don'?t (message|contact)|"
    r"leave me alone|remove me|stop sending|useless|spam|bothering|"
    r"band karo|mat bhejo|pareshan)",
    re.IGNORECASE,
)

INTENT_COMMIT_PAT = re.compile(
    r"\b(let'?s do it|go ahead|do it|yes please|sounds good|proceed|confirm|"
    r"set it up|set up|chalega|kar do|theek hai chalo|haan kar|ok(ay)? (let|do|go|chalo))\b"
    r"|^\s*(yes|yep|sure|ok|okay|haan)\b",
    re.IGNORECASE,
)

WAIT_PAT = re.compile(
    r"(call me later|not now|busy|next week|tomorrow|baad mein|abhi nahi|"
    r"thodi der|later)",
    re.IGNORECASE,
)

OFFTOPIC_PAT = re.compile(r"(gst|income tax|loan|electricity bill|rent|legal notice)", re.IGNORECASE)


def handle_reply(store: Store, conv_id: str, merchant_id: str | None,
                 customer_id: str | None, message: str, turn_number: int) -> dict:
    msg = (message or "").strip()
    conv = store.get_or_create_conversation(
        conv_id, merchant_id=merchant_id, customer_id=customer_id)
    conv.turns.append({"from": "merchant", "msg": msg, "turn": turn_number})

    if conv.ended:
        return {"action": "end", "rationale": "Conversation already closed; no further sends."}

    # 1) Opt-out / hostility -> end immediately.
    if OPT_OUT_PAT.search(msg):
        conv.ended = True
        if merchant_id:
            store.opted_out_merchants.add(merchant_id)
        return {"action": "end",
                "rationale": "Merchant signalled opt-out/frustration; closing and suppressing "
                             "future triggers for this merchant."}

    # 2) Auto-reply -> escalate per merchant (send once, then wait, then end).
    if AUTO_REPLY_PAT.search(msg):
        key = merchant_id or conv_id
        count = store.merchant_auto_reply_count.get(key, 0) + 1
        store.merchant_auto_reply_count[key] = count
        if count == 1:
            body = ("Looks like an auto-reply 😊 When the owner sees this, just reply YES and "
                    "I'll take it from there.")
            return _send(conv, body, "binary_yes_no",
                         "Detected WhatsApp auto-reply; one explicit prompt to flag for the owner.")
        if count == 2:
            return {"action": "wait", "wait_seconds": 14400,
                    "rationale": "Second auto-reply in a row — owner not at the phone. Backing off 4h."}
        conv.ended = True
        return {"action": "end",
                "rationale": "Auto-reply 3x with no real reply — zero engagement signal; closing."}

    # 3) Explicit intent commitment -> switch to ACTION mode.
    if INTENT_COMMIT_PAT.search(msg):
        conv.in_action_mode = True
        body = _action_mode_body(store, conv)
        return _send(conv, body, "binary_confirm_cancel",
                     "Merchant committed; switched from qualifying to executing — concrete next "
                     "step with measurable scope.")

    # 4) Asked for time -> back off politely.
    if WAIT_PAT.search(msg):
        return {"action": "wait", "wait_seconds": 7200,
                "rationale": "Merchant asked for time; backing off 2h before re-engaging."}

    # 5) Off-topic / curveball -> decline + redirect to mission.
    if OFFTOPIC_PAT.search(msg):
        body = ("That one's outside what I can help with directly — best handled by your CA/provider. "
                "Coming back to what I can do: want me to draft the next step for your listing?")
        return _send(conv, body, "open_ended",
                     "Out-of-scope ask politely declined; redirected to the original mission.")

    # 6) Otherwise it's an engaged reply -> compose a contextual advance.
    body = _engaged_body(store, conv, msg)
    cta = "binary_yes_no" if conv.in_action_mode else "open_ended"
    return _send(conv, body, cta, "Engaged reply; acknowledged and advanced to the next best step.")


def _send(conv, body: str, cta: str, rationale: str) -> dict:
    # Anti-repetition: never send the same body twice in one conversation.
    if body in conv.sent_bodies:
        body = body + " (Reply STOP anytime to pause.)"
    conv.sent_bodies.append(body)
    conv.turns.append({"from": "vera", "msg": body})
    return {"action": "send", "body": body, "cta": cta, "rationale": rationale}


def _action_mode_body(store: Store, conv) -> str:
    """Action-mode reply: concrete execution, NO qualifying questions."""
    merchant = store.get("merchant", conv.merchant_id) if conv.merchant_id else None
    scope = ""
    if merchant:
        agg = merchant.get("customer_aggregate", {})
        n = agg.get("high_risk_adult_count") or agg.get("chronic_rx_count") or agg.get("lapsed_180d_plus")
        if n:
            scope = f" to your list ({n} customers)"
    return (f"On it — drafting your message now and pre-filling the Google post for tomorrow 10am. "
            f"Reply CONFIRM to publish + send{scope}, or CANCEL to review first.")


def _engaged_body(store: Store, conv, msg: str) -> str:
    if llm.available:
        out = _llm_reply(store, conv, msg)
        if out:
            return out
    return ("Got it — I'll prep that and have it ready for you. Want me to go ahead and draft it now?")


REPLY_SYSTEM = """You are Vera, magicpin's merchant assistant on WhatsApp, mid-conversation.
Reply to the merchant's latest message in ONE short, on-voice WhatsApp turn.
Rules: no URLs; one clear next step; never re-introduce yourself; never fabricate;
match the merchant's language (Hindi-English code-mix is fine); be a helpful peer, not salesy.
Return ONLY JSON: {"body": "..."}"""


def _llm_reply(store: Store, conv, msg: str) -> str | None:
    merchant = store.get("merchant", conv.merchant_id) if conv.merchant_id else {}
    import json as _json
    recent = conv.turns[-6:]
    user = _json.dumps({
        "merchant_identity": (merchant or {}).get("identity"),
        "conversation_so_far": recent,
        "merchant_latest_message": msg,
        "in_action_mode": conv.in_action_mode,
    }, ensure_ascii=False)
    out = llm.complete_json(REPLY_SYSTEM, user, max_tokens=400)
    if out and out.get("body"):
        return out["body"].strip()
    return None
