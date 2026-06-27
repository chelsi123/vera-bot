# Vera Challenge Bot — Team Vera

A WhatsApp merchant-engagement bot for the magicpin "Vera" challenge. Exposes the
5 judge endpoints, composes merchant- and customer-facing messages from the
4-context framework, and handles multi-turn conversations.

## Approach

**Composer (the scoring engine).** Each message is composed from
`(category, merchant, trigger, customer?)`. Two paths, same output schema:

1. **Claude** (`claude-sonnet-4-6`, `temperature=0`) with a single rubric-aligned
   prompt that encodes the 5 scoring dimensions, the compulsion levers, the voice
   rules per category, and the hard constraints (no fabrication, no URLs, one CTA,
   language match). Used when `ANTHROPIC_API_KEY` is set.
2. **Deterministic per-trigger-kind templates** (no key required). 24 handlers that
   pull *real* facts out of the contexts — trial sizes, source citations, perf
   deltas, peer stats, recall slots, recall prices, competitor offers, recall batch
   numbers — so even the keyless baseline scores well on Specificity and Merchant
   Fit. Also used as a fast fallback if the LLM call times out.

Every output passes through `finalize()`, which strips URLs (Meta rejects them),
clamps to a single valid CTA, and guarantees the response schema.

**Reply router** (`conversation.py`). Critical routing is deterministic regex
classification (fast, no LLM latency, reliable for the replay test):

- **Auto-reply** → escalate per merchant: nudge once → wait 4h → end. Stops Vera's
  #1 production waste (burning turns on canned auto-replies).
- **Explicit intent** ("ok let's do it") → flip to **action mode**: a concrete next
  step with measurable scope, never another qualifying question.
- **Opt-out / hostility** → end and suppress the merchant's future triggers.
- **Off-topic** (GST, etc.) → decline + redirect to mission. **Asked for time** → wait.
- **Engaged** → Claude composes the contextual advance (template ack if no key).

**State** (`store.py`). Versioned, idempotent context store keyed on
`(scope, context_id)`: same version is a no-op, higher replaces, lower → 409.
Suppression-key dedup + per-merchant opt-out/auto-reply tracking. In-memory.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env          # add ANTHROPIC_API_KEY for Claude composition (optional)
uvicorn bot:app --host 0.0.0.0 --port 8080
```

Expose publicly (e.g. `ngrok http 8080`) and submit the URL.

## Test

```bash
python selftest.py            # 20-check end-to-end harness (all endpoints + scenarios)
python generate_submission.py # writes submission.jsonl (one message per dataset trigger)
```

To run magicpin's judge: set `BOT_URL` + an `LLM_API_KEY` in `../judge_simulator.py`
and `python ../judge_simulator.py`.

## Tradeoffs

- **Deterministic routing over LLM routing** for auto-reply/intent/opt-out — these
  must be reliable and fast; an LLM adds latency and variance where keyword signals
  are unambiguous. Composition (where nuance pays) stays LLM-first.
- **Template fallback is first-class**, not a stub — the bot is fully functional and
  reasonably high-scoring with no API key, and degrades gracefully under timeout.
- **In-memory state** per the brief (no restarts during a test). Swap `Store` for
  Redis/SQLite for production durability.

## What additional context would have helped most

1. **The merchant's open appointment slots** for merchant-facing booking nudges
   (we have them for customer recalls, not for proactive merchant offers).
2. **Per-trigger language hint** — we infer Hindi-English mix from `identity.languages`;
   an explicit per-merchant style/length preference would sharpen voice match.
3. **Conversation outcome history** (which past nudges converted) to rank which
   trigger to fire when several are active in one tick.
