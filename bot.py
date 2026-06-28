"""
Vera challenge bot — HTTP server exposing the 5 endpoints the judge harness calls.

  GET  /v1/healthz   liveness + context counts
  GET  /v1/metadata  team / model identity
  POST /v1/context   versioned, idempotent context ingestion
  POST /v1/tick      proactive composition (returns actions[])
  POST /v1/reply     conversation turn handling (send / wait / end)
  POST /v1/teardown  (optional) wipe state at end of test

Run:  uvicorn bot:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from store import Store
from composer import compose
from conversation import handle_reply

app = FastAPI(title="Vera Challenge Bot")
# Allow the interactive playground page (and any browser tool) to call the bot.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
START = time.time()
store = Store()
_pool = ThreadPoolExecutor(max_workers=8)

TEAM_NAME = os.getenv("TEAM_NAME", "Team Vera")
TEAM_MEMBERS = [m for m in os.getenv("TEAM_MEMBERS", "Chelsi").split(",") if m]
CONTACT_EMAIL = os.getenv("CONTACT_EMAIL", "chelsisinghal1362@gmail.com")
MODEL = os.getenv("COMPOSER_MODEL", "claude-sonnet-4-6")
TICK_BUDGET_SECONDS = float(os.getenv("TICK_BUDGET_SECONDS", "20"))
MAX_ACTIONS_PER_TICK = 20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ===========================================================================
# Liveness / identity
# ===========================================================================
@app.get("/v1/healthz")
def healthz():
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START),
        "contexts_loaded": store.counts(),
    }


@app.get("/v1/metadata")
def metadata():
    return {
        "team_name": TEAM_NAME,
        "team_members": TEAM_MEMBERS,
        "model": MODEL,
        "approach": "Per-trigger-kind composer (Claude with a rubric-aligned prompt, "
                    "deterministic template fallback) over a versioned 4-context store; "
                    "intent-classified reply router for auto-reply/intent/opt-out handling.",
        "contact_email": CONTACT_EMAIL,
        "version": "1.0.0",
        "submitted_at": _now_iso(),
    }


# ===========================================================================
# Context ingestion
# ===========================================================================
class CtxBody(BaseModel):
    scope: str
    context_id: str
    version: int = 1
    payload: dict = Field(default_factory=dict)
    delivered_at: str | None = None


@app.post("/v1/context")
def push_context(body: CtxBody):
    if body.scope not in ("category", "merchant", "customer", "trigger"):
        return JSONResponse(status_code=400,
                            content={"accepted": False, "reason": "invalid_scope",
                                     "details": f"unknown scope '{body.scope}'"})

    accepted, reason, current = store.put_context(
        body.scope, body.context_id, body.version, body.payload)

    if not accepted:
        return JSONResponse(status_code=409,
                            content={"accepted": False, "reason": reason,
                                     "current_version": current})

    return {"accepted": True,
            "ack_id": f"ack_{body.context_id}_v{current}",
            "stored_at": _now_iso()}


# ===========================================================================
# Proactive composition
# ===========================================================================
class TickBody(BaseModel):
    now: str | None = None
    available_triggers: list[str] = Field(default_factory=list)


def _compose_one(trigger_id: str) -> dict | None:
    trigger = store.get("trigger", trigger_id)
    if not trigger:
        return None

    merchant_id = trigger.get("merchant_id")
    merchant = store.get("merchant", merchant_id)
    if not merchant:
        return None

    # Respect opt-outs and dedup on suppression_key.
    if merchant_id in store.opted_out_merchants:
        return None
    supp = trigger.get("suppression_key")
    if store.has_seen_suppression(supp):
        return None

    category = store.get("category", merchant.get("category_slug"))
    if not category:
        category = {"slug": merchant.get("category_slug", "")}

    customer_id = trigger.get("customer_id")
    customer = store.get("customer", customer_id) if customer_id else None

    msg = compose(category, merchant, trigger, customer)
    if not msg.get("body"):
        return None

    conv_id = f"conv_{merchant_id}_{trigger_id}"
    store.get_or_create_conversation(
        conv_id, merchant_id=merchant_id, customer_id=customer_id, trigger_id=trigger_id)
    store.mark_suppression(supp)

    return {
        "conversation_id": conv_id,
        "merchant_id": merchant_id,
        "customer_id": customer_id,
        "send_as": msg["send_as"],
        "trigger_id": trigger_id,
        "template_name": msg["template_name"],
        "template_params": msg["template_params"],
        "body": msg["body"],
        "cta": msg["cta"],
        "suppression_key": msg["suppression_key"],
        "rationale": msg["rationale"],
    }


@app.post("/v1/tick")
def tick(body: TickBody):
    deadline = time.time() + TICK_BUDGET_SECONDS
    trigger_ids = body.available_triggers[:MAX_ACTIONS_PER_TICK]

    # Compose in parallel so the 20-action cap fits the time budget.
    futures = {tid: _pool.submit(_compose_one, tid) for tid in trigger_ids}
    actions: list[dict] = []
    for tid in trigger_ids:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        try:
            result = futures[tid].result(timeout=max(0.1, remaining))
        except (FuturesTimeout, Exception):
            result = None
        if result:
            actions.append(result)

    return {"actions": actions}


# ===========================================================================
# Conversation turns
# ===========================================================================
class ReplyBody(BaseModel):
    conversation_id: str
    merchant_id: str | None = None
    customer_id: str | None = None
    from_role: str = "merchant"
    message: str = ""
    received_at: str | None = None
    turn_number: int = 1


@app.post("/v1/reply")
def reply(body: ReplyBody):
    return handle_reply(
        store, body.conversation_id, body.merchant_id, body.customer_id,
        body.message, body.turn_number)


# ===========================================================================
# Optional teardown
# ===========================================================================
@app.post("/v1/teardown")
def teardown():
    store.reset()
    return {"status": "ok", "wiped": True}
