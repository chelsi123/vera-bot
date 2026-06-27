"""
Composer: turns (category, merchant, trigger, customer?) into a WhatsApp message.

Two paths:
  1. Claude (if ANTHROPIC_API_KEY is set) — a single rubric-aligned prompt.
  2. Deterministic per-trigger-kind templates — high-specificity fallback that
     pulls real numbers/dates/citations from the contexts. Used when no key is
     set or the LLM call fails/times out, and as the baseline submission engine.

Both paths run through `finalize()` which strips URLs (Meta would reject them),
enforces a single CTA, and guarantees the output schema.
"""
from __future__ import annotations

import json
import re

from llm import LLMClient

llm = LLMClient()

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)

VALID_CTA = {
    "open_ended", "binary_yes_no", "binary_confirm_cancel",
    "multi_choice_slot", "none",
}


# ===========================================================================
# Public entry point
# ===========================================================================
def compose(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    """Return the composed message dict (body, cta, send_as, template_name,
    template_params, suppression_key, rationale)."""
    result = None
    if llm.available:
        result = _compose_with_llm(category, merchant, trigger, customer)
    if result is None:
        result = _compose_with_template(category, merchant, trigger, customer)
    return finalize(result, trigger, customer)


# ===========================================================================
# Claude path
# ===========================================================================
SYSTEM_PROMPT = """You are Vera, magicpin's merchant-AI assistant on WhatsApp. You write ONE outbound message.

You are scored 0-10 on each of five dimensions:
- SPECIFICITY: anchor on a concrete, verifiable fact from the contexts (a number, date, price, headline, peer stat, source citation). Generic "X% off" / "boost your sales" loses points.
- CATEGORY FIT: match the vertical's voice exactly (dentists=clinical peer; salons=warm practical; restaurants=operator-to-operator; gyms=coaching; pharmacies=trustworthy precise). Respect vocab taboos.
- MERCHANT FIT: personalize to THIS merchant's real numbers, offers, signals and owner name. Honor language preference (Hindi-English code-mix when languages include "hi").
- TRIGGER RELEVANCE: make the "why now" unmistakable — tie directly to the trigger event.
- ENGAGEMENT COMPULSION: make them want to reply. Use compulsion levers (specificity, loss aversion, social proof, effort externalization "I've drafted it — just say go", curiosity, asking the merchant) and ONE low-friction CTA.

HARD RULES:
- Never fabricate. Only use facts present in the provided contexts. No invented research, competitors, or numbers.
- No URLs in the body (Meta rejects them).
- Exactly one primary CTA. Pure-information triggers may use cta "none".
- No long preambles, no re-introducing yourself, no internal jargon.
- For customer-facing sends (a customer object is provided), set send_as="merchant_on_behalf", write as the merchant's clinic/business, and respect the customer's language + slot preferences. Otherwise send_as="vera".
- Keep it concise and WhatsApp-native. Hindi-English code-mix is welcome when allowed.

Return ONLY a JSON object with these keys:
{
  "body": "the message text",
  "cta": one of ["open_ended","binary_yes_no","binary_confirm_cancel","multi_choice_slot","none"],
  "send_as": "vera" or "merchant_on_behalf",
  "template_name": "snake_case_template_id",
  "template_params": ["param1","param2", ...],
  "rationale": "1-2 sentences: why this message, what it should achieve"
}"""


def _compose_with_llm(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict | None:
    resolved = _resolve_trigger_facts(category, trigger)
    user = json.dumps({
        "category": _trim_category(category),
        "merchant": merchant,
        "trigger": trigger,
        "resolved_trigger_facts": resolved,
        "customer": customer,
    }, ensure_ascii=False)
    out = llm.complete_json(SYSTEM_PROMPT, "Compose the message for:\n" + user)
    if not out or not out.get("body"):
        return None
    return out


def _trim_category(category: dict) -> dict:
    """Send the LLM only the fields it needs (keeps the prompt tight)."""
    keys = ("slug", "voice", "offer_catalog", "peer_stats", "digest",
            "patient_content_library", "seasonal_beats", "trend_signals")
    return {k: category.get(k) for k in keys if k in category}


# ===========================================================================
# Shared helpers
# ===========================================================================
def _digest_item(category: dict, item_id: str | None) -> dict | None:
    if not item_id:
        return None
    for item in category.get("digest", []) or []:
        if item.get("id") == item_id:
            return item
    return None


_DIGEST_ID_KEYS = ("top_item_id", "digest_item_id", "alert_id", "item_id")


def _payload_digest(category: dict, payload: dict) -> dict | None:
    for k in _DIGEST_ID_KEYS:
        item = _digest_item(category, payload.get(k))
        if item:
            return item
    return None


def _resolve_trigger_facts(category: dict, trigger: dict) -> dict:
    """Resolve id references in the trigger payload into concrete facts."""
    payload = trigger.get("payload", {}) or {}
    facts: dict = {}
    item = _payload_digest(category, payload)
    if item:
        facts["digest_item"] = item
    return facts


def _owner(merchant: dict) -> str:
    ident = merchant.get("identity", {})
    return ident.get("owner_first_name") or ident.get("name", "there")


def _salutation(category: dict, merchant: dict) -> str:
    slug = category.get("slug")
    owner = _owner(merchant)
    if slug == "dentists":
        return f"Dr. {owner}"
    return f"Hi {owner}"


def _hinglish(merchant: dict) -> bool:
    return "hi" in (merchant.get("identity", {}).get("languages") or [])


def _active_offers(merchant: dict) -> list[str]:
    return [o["title"] for o in merchant.get("offers", []) if o.get("status") == "active"]


# ===========================================================================
# Deterministic template path — one handler per trigger kind
# ===========================================================================
def _compose_with_template(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict:
    kind = trigger.get("kind", "")
    handler = _HANDLERS.get(kind, _generic)
    return handler(category, merchant, trigger, customer)


def _msg(body: str, cta: str, template_name: str, params: list, rationale: str,
         send_as: str = "vera") -> dict:
    return {
        "body": body, "cta": cta, "send_as": send_as,
        "template_name": template_name, "template_params": params,
        "rationale": rationale,
    }


def _research_digest(cat, m, t, c):
    sal = _salutation(cat, m)
    item = _payload_digest(cat, t.get("payload", {})) or {}
    title = item.get("title", "this week's research digest")
    source = item.get("source", "")
    trial_n = item.get("trial_n")
    seg = (item.get("patient_segment") or "").replace("_", " ")
    src_tag = f" — {source}" if source else ""
    n_clause = f"{trial_n:,}-patient trial: " if trial_n else ""
    seg_clause = f" Relevant to your {seg} cohort." if seg else ""
    body = (f"{sal}, this week's digest landed. {n_clause}{title}.{seg_clause} "
            f"Worth a 2-min look. Want me to pull the abstract + draft a patient-ed "
            f"WhatsApp you can reshare?{src_tag}")
    return _msg(body, "open_ended", "vera_research_digest_v1",
                [sal, title, source],
                "External research digest with a clinical anchor + source citation; "
                "low-friction reciprocity CTA (I'll draft it for you).")


def _regulation_change(cat, m, t, c):
    sal = _salutation(cat, m)
    item = _payload_digest(cat, t.get("payload", {})) or {}
    title = item.get("title", "a compliance update")
    source = item.get("source", "")
    action = item.get("actionable", "")
    src_tag = f" — {source}" if source else ""
    act_clause = f" {action}." if action else ""
    body = (f"{sal}, compliance heads-up: {title}.{act_clause} "
            f"Want me to send the 1-page checklist so you're covered before the deadline?{src_tag}")
    return _msg(body, "binary_yes_no", "vera_regulation_change_v1",
                [sal, title, source],
                "Regulatory change with deadline = loss aversion; binary CTA to send a checklist.")


def _perf_dip(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    metric = p.get("metric", "calls")
    delta = abs(int(round(p.get("delta_pct", 0) * 100)))
    window = p.get("window", "7d")
    baseline = p.get("vs_baseline")
    base_clause = f" (from ~{baseline}/wk)" if baseline else ""
    body = (f"{sal}, heads-up — your {metric} dropped {delta}% this {window}{base_clause}. "
            f"Often it's a stale listing or paused offer. I can run a quick diagnosis and "
            f"draft a fix today. Want me to take a look?")
    return _msg(body, "binary_yes_no", "vera_perf_dip_v1",
                [sal, metric, f"{delta}%"],
                "Internal perf dip with the exact metric+delta = specific loss aversion; "
                "effort-externalized binary CTA.")


def _perf_spike(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    metric = p.get("metric", "views")
    delta = abs(int(round(p.get("delta_pct", 0) * 100)))
    body = (f"{sal}, nice — your {metric} are up {delta}% this week. Momentum like this is the "
            f"best time to capture leads. Want me to publish a Google post today to ride the spike?")
    return _msg(body, "binary_yes_no", "vera_perf_spike_v1",
                [sal, metric, f"{delta}%"],
                "Perf spike framed as a momentum window; binary CTA to act while hot.")


def _renewal_due(cat, m, t, c):
    sal = _salutation(cat, m)
    days = m.get("subscription", {}).get("days_remaining")
    p = t.get("payload", {})
    days = p.get("days_remaining", days)
    perf = m.get("performance", {})
    leads = perf.get("leads")
    proof = f" Last 30d you got {leads} leads through the listing." if leads else ""
    day_clause = f"in {days} days" if days is not None else "soon"
    body = (f"{sal}, your magicpin plan renews {day_clause}.{proof} "
            f"Want me to keep everything running without a gap? Reply YES to renew.")
    return _msg(body, "binary_yes_no", "vera_renewal_due_v1",
                [sal, str(days)],
                "Renewal with concrete value proof (leads) + deadline; single YES CTA.")


def _festival_upcoming(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    fest = p.get("festival", "the festival")
    days = p.get("days_until")
    offers = _active_offers(m)
    offer_clause = f' Your "{offers[0]}" would land well.' if offers else ""
    when = f"{days} days out" if days is not None else "coming up"
    body = (f"{sal}, {fest} is {when} — peak booking window for {cat.get('slug','your category')}. "
            f"{offer_clause} Want me to draft a {fest} Google post + offer now so you're ready early?")
    return _msg(body, "binary_yes_no", "vera_festival_v1",
                [sal, fest, str(days)],
                "Festival timing = curiosity + early-mover advantage; uses the merchant's real offer.")


def _competitor_opened(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    name = p.get("competitor_name", "a new competitor")
    dist = p.get("distance_km")
    their = p.get("their_offer", "")
    dist_clause = f"{dist}km away" if dist is not None else "nearby"
    their_clause = f' running "{their}"' if their else ""
    body = (f"{sal}, {name} just opened {dist_clause}{their_clause}. "
            f"Easiest counter is to refresh your listing + a sharper service-at-price offer. "
            f"Want to see how your listing stacks up against theirs?")
    return _msg(body, "open_ended", "vera_competitor_opened_v1",
                [sal, name, str(dist)],
                "Competitor opening = voyeur-curiosity + loss aversion; specific name/distance/offer.")


def _milestone_reached(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    metric = p.get("metric", "reviews")
    value = p.get("value")
    val_clause = f"{value} {metric}" if value is not None else f"a {metric} milestone"
    body = (f"{sal}, you just crossed {val_clause} 🎉 Social proof like this converts browsers. "
            f"Want me to turn it into a 'thank you' Google post that nudges new customers?")
    return _msg(body, "binary_yes_no", "vera_milestone_v1",
                [sal, metric, str(value)],
                "Milestone = social proof; CTA converts the milestone into acquisition content.")


def _review_theme_emerged(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    theme = p.get("theme", "a recurring theme")
    count = p.get("count")
    cnt_clause = f"{count} reviews this week mention" if count else "Reviews are mentioning"
    body = (f"{sal}, {cnt_clause} '{theme}'. Worth addressing before it spreads. "
            f"Want me to draft a short public reply template + a fix note for your team?")
    return _msg(body, "binary_yes_no", "vera_review_theme_v1",
                [sal, theme, str(count)],
                "Review theme = specific, time-bound reputational loss aversion.")


def _ipl_match_today(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    match = p.get("match", "tonight's match")
    venue = p.get("venue", "")
    venue_clause = f" at {venue}" if venue else ""
    body = (f"{sal}, {match} is on{venue_clause} tonight — big footfall window for your area. "
            f"Want me to push a match-night offer + Google post in the next 30 min so you catch the crowd?")
    return _msg(body, "binary_yes_no", "vera_ipl_match_v1",
                [sal, match, venue],
                "Same-day local event = urgency; tight time-boxed CTA to capture footfall.")


def _supply_alert(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    mol = p.get("molecule", "a medicine")
    batches = ", ".join(p.get("affected_batches", []) or [])
    mfr = p.get("manufacturer", "")
    mfr_clause = f" ({mfr})" if mfr else ""
    batch_clause = f" Batches: {batches}." if batches else ""
    body = (f"{sal}, urgent: {mol}{mfr_clause} recall notice.{batch_clause} "
            f"Please pull affected stock and check pending orders. "
            f"Want me to draft a customer SMS for anyone who bought these batches?")
    return _msg(body, "binary_yes_no", "vera_supply_alert_v1",
                [sal, mol, batches],
                "High-urgency safety recall with exact molecule+batches; actionable binary CTA.")


def _gbp_unverified(cat, m, t, c):
    sal = _salutation(cat, m)
    body = (f"{sal}, your Google listing is still unverified — that's why updates show slowly and "
            f"you're missing map visibility. It's a 5-min fix and I'll walk you through it. "
            f"Want me to start the verification now?")
    return _msg(body, "binary_yes_no", "vera_gbp_unverified_v1", [sal],
                "Unverified GBP = concrete loss (visibility); effort-externalized 5-min CTA.")


def _cde_opportunity(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    item = _payload_digest(cat, p) or {}
    title = item.get("title", "a CDE session")
    credits = p.get("credits") or item.get("credits")
    date = item.get("date", "")
    cr_clause = f"{credits} CDE credits" if credits else "CDE credits"
    date_clause = f" on {date[:10]}" if date else ""
    body = (f"{sal}, {cr_clause} up for grabs — {title}{date_clause}. "
            f"Want me to add it to your calendar and send the registration steps?")
    return _msg(body, "binary_yes_no", "vera_cde_webinar_v1",
                [sal, title, str(credits)],
                "Professional-development hook (CDE credits) fits the clinical peer voice.")


def _dormant_with_vera(cat, m, t, c):
    sal = _salutation(cat, m)
    offers = _active_offers(m)
    hook = f' One quick win: your "{offers[0]}" could use a fresh Google post.' if offers else \
           " One quick win: a fresh Google post."
    body = (f"{sal}, haven't heard from you in a while.{hook} "
            f"Want me to draft it so you just hit publish?")
    return _msg(body, "binary_yes_no", "vera_dormant_v1", [sal],
                "Re-engagement after dormancy with a concrete, zero-effort win.")


def _winback_eligible(cat, m, t, c):
    sal = _salutation(cat, m)
    agg = m.get("customer_aggregate", {})
    lapsed = agg.get("lapsed_180d_plus")
    lapsed_clause = f"{lapsed} customers haven't returned in 6+ months" if lapsed else \
                    "several customers have gone quiet"
    body = (f"{sal}, {lapsed_clause}. A single win-back message often brings 10-15% back. "
            f"Want me to draft a win-back offer you can send to that list?")
    return _msg(body, "binary_yes_no", "vera_winback_v1",
                [sal, str(lapsed)],
                "Win-back uses the merchant's real lapsed count; social-proof return rate.")


def _seasonal_perf_dip(cat, m, t, c):
    sal = _salutation(cat, m)
    beats = cat.get("seasonal_beats", []) or []
    note = beats[0].get("note") if beats else ""
    note_clause = f" Seasonal pattern: {note}." if note else ""
    body = (f"{sal}, this is a known slow stretch for your category.{note_clause} "
            f"Best counter is a trial-class push now. Want me to draft one?")
    return _msg(body, "binary_yes_no", "vera_seasonal_dip_v1", [sal],
                "Frames the dip as seasonal (not their fault) + a concrete counter-move.")


def _category_seasonal(cat, m, t, c):
    sal = _salutation(cat, m)
    beats = cat.get("seasonal_beats", []) or []
    note = beats[0].get("note") if beats else "a seasonal demand shift"
    body = (f"{sal}, demand is shifting — {note}. Stocking/positioning for it early pays off. "
            f"Want me to draft a Google post highlighting the in-season picks?")
    return _msg(body, "open_ended", "vera_category_seasonal_v1", [sal],
                "Category seasonal trend with a specific beat; low-pressure open CTA.")


def _curious_ask_due(cat, m, t, c):
    sal = _salutation(cat, m)
    body = (f"{sal}, quick one to tune your content — what's your most-requested service this week? "
            f"I'll build your next Google post around it.")
    return _msg(body, "open_ended", "vera_curious_ask_v1", [sal],
                "Curiosity/ask-the-merchant lever (Vera's biggest underused family); open CTA.")


def _active_planning_intent(cat, m, t, c):
    sal = _salutation(cat, m)
    p = t.get("payload", {})
    topic = p.get("topic") or p.get("program") or "what you're planning"
    body = (f"{sal}, picking up on {topic} — I can draft the listing copy + a launch Google post "
            f"so it's ready to go. Want me to draft it now?")
    return _msg(body, "binary_yes_no", "vera_planning_intent_v1",
                [sal, str(topic)],
                "Honors an in-flight planning intent; moves straight to drafting (action-bias).")


# ---- customer-facing handlers --------------------------------------------
def _recall_due(cat, m, t, c):
    if not c:
        return _generic(cat, m, t, c)
    name = c.get("identity", {}).get("name", "there")
    biz = m.get("identity", {}).get("name", "the clinic")
    p = t.get("payload", {})
    slots = p.get("available_slots", []) or []
    slot_labels = [s.get("label") for s in slots if s.get("label")]
    offers = _active_offers(m)
    price = next((o for o in offers if "₹" in o), offers[0] if offers else "")
    hinglish = "hi" in (c.get("identity", {}).get("language_pref", "")).lower() or _hinglish(m)
    if len(slot_labels) >= 2:
        slot_clause = (f"Aapke liye 2 slots ready hain: {slot_labels[0]} ya {slot_labels[1]}."
                       if hinglish else
                       f"Two slots ready for you: {slot_labels[0]} or {slot_labels[1]}.")
        cta_line = "Reply 1, 2, or tell us a time that works."
        cta = "multi_choice_slot"
    else:
        slot_clause = "Let us know a time that suits you."
        cta_line = "Reply with your preferred day/time."
        cta = "open_ended"
    price_clause = f" {price}." if price else ""
    body = (f"Hi {name}, {biz} here 🦷 It's been a few months since your last visit — your "
            f"6-month cleaning recall is due. {slot_clause}{price_clause} {cta_line}")
    return _msg(body, cta, "merchant_recall_reminder_v1",
                [name, biz, slot_labels[0] if slot_labels else "", price],
                "Customer recall sent on the merchant's behalf; honors language + evening slots, "
                "real catalog price, multi-choice slot CTA for booking.",
                send_as="merchant_on_behalf")


def _chronic_refill_due(cat, m, t, c):
    if not c:
        return _generic(cat, m, t, c)
    name = c.get("identity", {}).get("name", "there")
    biz = m.get("identity", {}).get("name", "the pharmacy")
    p = t.get("payload", {})
    med = p.get("medicine") or p.get("molecule") or "your monthly medicine"
    body = (f"Hi {name}, {biz} here. Your refill for {med} is due. We can keep it ready for "
            f"pickup or home-deliver it. Reply 1 for pickup, 2 for delivery.")
    return _msg(body, "multi_choice_slot", "merchant_refill_reminder_v1",
                [name, biz, str(med)],
                "Chronic refill reminder on merchant's behalf; specific medicine, two low-friction options.",
                send_as="merchant_on_behalf")


def _customer_lapsed_hard(cat, m, t, c):
    if not c:
        return _generic(cat, m, t, c)
    name = c.get("identity", {}).get("name", "there")
    biz = m.get("identity", {}).get("name", "us")
    offers = _active_offers(m)
    offer_clause = f' Welcome back with "{offers[0]}".' if offers else ""
    body = (f"Hi {name}, {biz} here — we've missed you!{offer_clause} "
            f"Want to grab a slot this week? Reply YES and we'll set it up.")
    return _msg(body, "binary_yes_no", "merchant_winback_customer_v1",
                [name, biz],
                "Hard-lapsed customer win-back on merchant's behalf with a real offer.",
                send_as="merchant_on_behalf")


def _trial_followup(cat, m, t, c):
    if not c:
        return _generic(cat, m, t, c)
    name = c.get("identity", {}).get("name", "there")
    biz = m.get("identity", {}).get("name", "us")
    body = (f"Hi {name}, {biz} here — how was your trial class? If you enjoyed it, we can hold "
            f"your spot on the same slot. Reply YES to continue.")
    return _msg(body, "binary_yes_no", "merchant_trial_followup_v1",
                [name, biz],
                "Trial follow-up on merchant's behalf; reciprocity + easy continuation CTA.",
                send_as="merchant_on_behalf")


def _wedding_package_followup(cat, m, t, c):
    if not c:
        return _generic(cat, m, t, c)
    name = c.get("identity", {}).get("name", "there")
    biz = m.get("identity", {}).get("name", "us")
    body = (f"Hi {name}, {biz} here ✨ Following up on your bridal package — we can lock your "
            f"trial + dates now so your preferred slots don't fill up. Want me to hold them?")
    return _msg(body, "binary_yes_no", "merchant_bridal_followup_v1",
                [name, biz],
                "Bridal follow-up on merchant's behalf; scarcity (slots filling) + hold CTA.",
                send_as="merchant_on_behalf")


def _generic(cat, m, t, c):
    """Last-resort: still anchors on the trigger kind + a real merchant number."""
    sal = _salutation(cat, m)
    kind = t.get("kind", "an update").replace("_", " ")
    perf = m.get("performance", {})
    views = perf.get("views")
    view_clause = f" Your listing got {views} views last month." if views else ""
    body = (f"{sal}, quick note re: {kind}.{view_clause} "
            f"I can draft the next best step for you — want me to?")
    return _msg(body, "binary_yes_no", "vera_generic_v1",
                [sal, kind],
                f"Generic handler for trigger kind '{kind}'; anchors on a real merchant metric.")


_HANDLERS = {
    "research_digest": _research_digest,
    "regulation_change": _regulation_change,
    "perf_dip": _perf_dip,
    "perf_spike": _perf_spike,
    "renewal_due": _renewal_due,
    "festival_upcoming": _festival_upcoming,
    "competitor_opened": _competitor_opened,
    "milestone_reached": _milestone_reached,
    "review_theme_emerged": _review_theme_emerged,
    "ipl_match_today": _ipl_match_today,
    "supply_alert": _supply_alert,
    "gbp_unverified": _gbp_unverified,
    "cde_opportunity": _cde_opportunity,
    "dormant_with_vera": _dormant_with_vera,
    "winback_eligible": _winback_eligible,
    "seasonal_perf_dip": _seasonal_perf_dip,
    "category_seasonal": _category_seasonal,
    "curious_ask_due": _curious_ask_due,
    "active_planning_intent": _active_planning_intent,
    # customer-facing
    "recall_due": _recall_due,
    "chronic_refill_due": _chronic_refill_due,
    "customer_lapsed_hard": _customer_lapsed_hard,
    "trial_followup": _trial_followup,
    "wedding_package_followup": _wedding_package_followup,
}


# ===========================================================================
# Finalize: enforce invariants
# ===========================================================================
def finalize(result: dict, trigger: dict, customer: dict | None) -> dict:
    body = (result.get("body") or "").strip()
    body = URL_RE.sub("", body).strip()              # strip URLs (Meta rejects)
    body = re.sub(r"\s{2,}", " ", body)

    cta = result.get("cta", "open_ended")
    if cta not in VALID_CTA:
        cta = "open_ended"

    send_as = result.get("send_as")
    if send_as not in ("vera", "merchant_on_behalf"):
        send_as = "merchant_on_behalf" if customer else "vera"

    template_name = result.get("template_name") or "vera_generic_v1"
    template_params = result.get("template_params") or []
    if not isinstance(template_params, list):
        template_params = [str(template_params)]

    return {
        "body": body,
        "cta": cta,
        "send_as": send_as,
        "template_name": template_name,
        "template_params": template_params,
        "suppression_key": trigger.get("suppression_key", ""),
        "rationale": result.get("rationale", ""),
    }
