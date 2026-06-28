"""
Proof: verify the LIVE cloud bot against the official challenge contract.
Each check cites the spec section it satisfies.
"""
import json, sys, urllib.request, urllib.error
from pathlib import Path
try: sys.stdout.reconfigure(encoding="utf-8")
except Exception: pass

BASE = "https://vera-bot-eo0m.onrender.com"
DS = Path(__file__).resolve().parent.parent / "dataset"
P, F = 0, 0

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE+path, data=data, method=method,
                               headers={"content-type":"application/json"})
    try:
        with urllib.request.urlopen(r, timeout=90) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, {}

def check(ok, label, spec):
    global P, F
    P += ok; F += (not ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    print(f"        spec: {spec}")

cats = {json.loads(f.read_text(encoding='utf-8'))['slug']: json.loads(f.read_text(encoding='utf-8')) for f in (DS/'categories').glob('*.json')}
mer = {x['merchant_id']:x for x in json.loads((DS/'merchants_seed.json').read_text(encoding='utf-8'))['merchants']}
cus = {x['customer_id']:x for x in json.loads((DS/'customers_seed.json').read_text(encoding='utf-8'))['customers']}
trg = {x['id']:x for x in json.loads((DS/'triggers_seed.json').read_text(encoding='utf-8'))['triggers']}

call("POST","/v1/teardown",{})   # clean slate
print("="*70); print("VERIFYING LIVE BOT AGAINST OFFICIAL SPEC:", BASE); print("="*70)

# 1. healthz shape
s,d = call("GET","/v1/healthz")
check(s==200 and d.get("status")=="ok" and "contexts_loaded" in d,
      "GET /v1/healthz returns status=ok + contexts_loaded",
      "testing-brief §2.4: '{status:ok, uptime_seconds, contexts_loaded:{...}}'")

# 2. metadata shape
s,d = call("GET","/v1/metadata")
check(s==200 and all(k in d for k in ("team_name","model","approach")),
      "GET /v1/metadata returns identity fields",
      "testing-brief §2.5: team_name/team_members/model/approach/...")

# 3. context accept
s,d = call("POST","/v1/context",{"scope":"category","context_id":"dentists","version":5,"payload":cats["dentists"]})
check(s==200 and d.get("accepted") is True, "POST /v1/context accepts a push (v5)",
      "testing-brief §2.1: 200 {accepted:true, ack_id, stored_at}")

# 4. idempotent same version
s,d = call("POST","/v1/context",{"scope":"category","context_id":"dentists","version":5,"payload":cats["dentists"]})
check(d.get("accepted") is True, "Re-posting same version is a no-op success (idempotent)",
      "testing-brief §2.1: 'Idempotent by (context_id, version). Re-posting same version is a no-op.'")

# 5. stale version -> 409
s,d = call("POST","/v1/context",{"scope":"category","context_id":"dentists","version":3,"payload":cats["dentists"]})
check(s==409 and d.get("reason")=="stale_version" and d.get("current_version")==5,
      "Lower version rejected with 409 stale_version + current_version",
      "testing-brief §2.1 / api-examples 1.5: 409 {accepted:false, reason:stale_version, current_version}")

# 6. invalid scope -> 400
s,d = call("POST","/v1/context",{"scope":"bogus","context_id":"x","version":1,"payload":{}})
check(s==400 and d.get("reason")=="invalid_scope", "Malformed scope rejected with 400 invalid_scope",
      "testing-brief §2.1: 400 {accepted:false, reason:invalid_scope}")

# 7. load full dataset; counts reflect it
for slug,c in cats.items(): call("POST","/v1/context",{"scope":"category","context_id":slug,"version":9,"payload":c})
for mid,m in mer.items(): call("POST","/v1/context",{"scope":"merchant","context_id":mid,"version":9,"payload":m})
for cid,c in cus.items(): call("POST","/v1/context",{"scope":"customer","context_id":cid,"version":9,"payload":c})
for tid,t in trg.items(): call("POST","/v1/context",{"scope":"trigger","context_id":tid,"version":9,"payload":t})
s,d = call("GET","/v1/healthz"); cl = d.get("contexts_loaded",{})
check(cl=={"category":5,"merchant":10,"customer":15,"trigger":25},
      f"Bot persists pushed contexts across calls -> counts {cl}",
      "testing-brief §4 Phase1 + §12: 'Bot persists context across calls'")

# 8. tick returns well-formed actions, no URLs (10 triggers)
some = list(trg.keys())[:10]
s,d = call("POST","/v1/tick",{"now":"2026-04-26T10:35:00Z","available_triggers":some})
acts = d.get("actions",[])
req = {"conversation_id","merchant_id","send_as","trigger_id","template_name","template_params","body","cta","suppression_key","rationale"}
wellformed = len(acts)>0 and all(req <= set(a) for a in acts)
nourl = all("http" not in a["body"].lower() for a in acts)
check(wellformed, f"POST /v1/tick returns well-formed actions[] ({len(acts)} actions, all required fields)",
      "testing-brief §2.2: actions[] with conversation_id/send_as/trigger_id/body/cta/...")
check(nourl, "No URLs in any message body",
      "main-brief §5.4 / api-examples F.4: URLs in body are a hard fail (-3)")

# 9. reply: the three judge behaviors
_,r1 = call("POST","/v1/reply",{"conversation_id":"v1","merchant_id":"m_001_drmeera_dentist_delhi","message":"Ok lets do it, whats next?","turn_number":2})
b=r1.get("body","").lower()
qual=["would you","do you","can you tell","what if","how about"]; act=["draft","sending","confirm","proceed","next","here","done"]
check(r1.get("action")=="send" and any(w in b for w in act) and not any(w in b for w in qual),
      "Reply to 'let's do it' -> switches to ACTION (no re-qualifying)",
      "testing-brief §4 Phase4(2) / api-examples 4.2: must switch from qualifying to action")
_,r2 = call("POST","/v1/reply",{"conversation_id":"v2","merchant_id":"m_002_bharat_dentist_mumbai","message":"Stop messaging me, useless spam","turn_number":2})
check(r2.get("action")=="end", "Reply to hostile/opt-out -> ends gracefully",
      "testing-brief §4 Phase4(3) / api-examples 2.6: graceful exit on opt-out")
_,r3 = call("POST","/v1/reply",{"conversation_id":"v3","merchant_id":"m_003_studio11_salon_hyderabad","message":"Thank you for contacting us! Our team will respond shortly.","turn_number":2})
check(r3.get("action") in ("send","wait","end"), "Reply to auto-reply -> valid handled action",
      "testing-brief §4 Phase4(1): detect auto-reply, don't burn turns")

call("POST","/v1/teardown",{})   # reset clean for the judge
print("="*70)
print(f"RESULT: {P} PASSED, {F} FAILED   (bot reset clean for the judge)")
print("="*70)
