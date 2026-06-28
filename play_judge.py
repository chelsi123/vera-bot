"""
Practice judge — runs the judge's flow against the live cloud bot and prints a
readable report. No API key needed (it shows messages + behavior, not AI scores).

Run:  python play_judge.py
"""
import json, sys, urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8")  # so emojis/₹/— print on Windows
except Exception:
    pass

BASE = "https://vera-bot-eo0m.onrender.com"
DS = Path(__file__).resolve().parent.parent / "dataset"


def post(path, obj):
    r = urllib.request.Request(BASE + path, data=json.dumps(obj).encode(),
                               headers={"content-type": "application/json"}, method="POST")
    return json.loads(urllib.request.urlopen(r, timeout=90).read())


def line(c="-"):
    print(c * 64)


cats = {json.loads(f.read_text(encoding="utf-8"))["slug"]: json.loads(f.read_text(encoding="utf-8"))
        for f in (DS / "categories").glob("*.json")}
mer = {x["merchant_id"]: x for x in json.loads((DS / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"]}
cus = {x["customer_id"]: x for x in json.loads((DS / "customers_seed.json").read_text(encoding="utf-8"))["customers"]}
trg = {x["id"]: x for x in json.loads((DS / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"]}

print("Practice judge -> talking to", BASE)
print("(waking the bot; first call may take ~30s)\n")

# Start from a clean slate (clears any leftover playground data).
post("/v1/teardown", {})

# --- Phase 1: load everything, like the judge's warmup ---
for slug, c in cats.items():
    post("/v1/context", {"scope": "category", "context_id": slug, "version": 1, "payload": c})
for mid, m in mer.items():
    post("/v1/context", {"scope": "merchant", "context_id": mid, "version": 1, "payload": m})
for cid, c in cus.items():
    post("/v1/context", {"scope": "customer", "context_id": cid, "version": 1, "payload": c})
for tid, t in trg.items():
    post("/v1/context", {"scope": "trigger", "context_id": tid, "version": 1, "payload": t})
print("Loaded all shops, customers and events into the bot.\n")

# --- Phase 2: ask the bot for messages on a few events ---
line("=")
print("MESSAGES THE BOT COMPOSED (the judge scores these 0-10)")
line("=")
for tid in ["trg_001_research_digest_dentists", "trg_010_ipl_match_delhi",
            "trg_018_supply_atorvastatin_recall", "trg_003_recall_due_priya"]:
    res = post("/v1/tick", {"now": "2026-04-26T10:35:00Z", "available_triggers": [tid]})
    if not res["actions"]:
        continue
    a = res["actions"][0]
    nums = sum(ch.isdigit() for ch in a["body"])
    print(f"\nEVENT: {trg[tid]['kind']}")
    print(f"  -> {a['body']}")
    print(f"  [specific facts/numbers in message: {nums} digits | CTA: {a['cta']}]")

# --- Phase 3: role-play the merchant, like the judge's conversation test ---
line("\n=" if False else "=")
print("CONVERSATION TESTS (the judge plays the merchant)")
line("=")
tests = [
    ("Owner says: 'Yes, lets do it!'", "Ok lets do it, whats next?"),
    ("Owner sends an auto-reply", "Thank you for contacting us! Our team will respond shortly."),
    ("Owner is rude / wants out", "Stop messaging me, this is useless spam"),
    ("Owner asks something off-topic", "Can you also help me file my GST this month?"),
]
for i, (label, msg) in enumerate(tests):
    r = post("/v1/reply", {"conversation_id": f"play_{i}", "merchant_id": "m_001_drmeera_dentist_delhi",
                           "message": msg, "turn_number": 2})
    print(f"\n{label}")
    print(f"  bot action: {r.get('action')}")
    if r.get("body"):
        print(f"  bot says : {r['body']}")
    print(f"  why      : {r.get('rationale','')}")

# --- cleanup so the real judge starts clean ---
post("/v1/teardown", {})
print("\n" + "=" * 64)
print("Done. Bot reset to a clean state for the real judge. ")
print("=" * 64)
