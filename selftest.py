"""End-to-end smoke test of all endpoints using FastAPI TestClient (no network)."""
import io, json, sys
from pathlib import Path
from fastapi.testclient import TestClient
import bot

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
client = TestClient(bot.app)
ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "dataset"

def cats():
    return {json.loads(f.read_text(encoding="utf-8"))["slug"]: json.loads(f.read_text(encoding="utf-8"))
            for f in (DS/"categories").glob("*.json")}
def seed(name, key, idf):
    return {x[idf]: x for x in json.loads((DS/name).read_text(encoding="utf-8"))[key]}

categories = cats()
merchants = seed("merchants_seed.json","merchants","merchant_id")
customers = seed("customers_seed.json","customers","customer_id")
triggers  = seed("triggers_seed.json","triggers","id")

ok = True
def check(label, cond):
    global ok
    ok = ok and cond
    print(("PASS" if cond else "FAIL"), label)

# 1. healthz at boot
r = client.get("/v1/healthz").json()
check("healthz boot zeros", r["contexts_loaded"]=={"category":0,"merchant":0,"customer":0,"trigger":0})

# 2. metadata
check("metadata", client.get("/v1/metadata").json()["team_name"] != "")

# 3. push all contexts
for s,d in [("category",categories),("merchant",merchants),("customer",customers),("trigger",triggers)]:
    for cid,p in d.items():
        rr = client.post("/v1/context", json={"scope":s,"context_id":cid,"version":1,"payload":p})
        if not rr.json().get("accepted"): check(f"push {s}/{cid}", False)
r = client.get("/v1/healthz").json()["contexts_loaded"]
check("counts after push", r=={"category":5,"merchant":10,"customer":15,"trigger":25})

# 4. idempotency + version bump + stale
check("idempotent same version", client.post("/v1/context", json={"scope":"category","context_id":"dentists","version":1,"payload":categories["dentists"]}).json()["accepted"])
check("version bump accepted", client.post("/v1/context", json={"scope":"category","context_id":"dentists","version":2,"payload":categories["dentists"]}).json()["accepted"])
stale = client.post("/v1/context", json={"scope":"category","context_id":"dentists","version":1,"payload":categories["dentists"]})
check("stale -> 409", stale.status_code==409 and stale.json()["current_version"]==2)
check("invalid scope -> 400", client.post("/v1/context", json={"scope":"bogus","context_id":"x","version":1,"payload":{}}).status_code==400)

# 5. tick -> actions for all triggers
r = client.post("/v1/tick", json={"now":"2026-04-26T10:35:00Z","available_triggers":list(triggers.keys())}).json()
acts = r["actions"]
check("tick produced actions", len(acts) >= 20)
required = {"conversation_id","merchant_id","send_as","trigger_id","template_name","template_params","body","cta","suppression_key","rationale"}
check("actions well-formed", all(required <= set(a) for a in acts))
check("no URLs in bodies", all("http" not in a["body"].lower() for a in acts))
check("customer trigger -> merchant_on_behalf", any(a["send_as"]=="merchant_on_behalf" for a in acts))

# 6. tick dedup: re-ticking same triggers should now suppress (already sent)
r2 = client.post("/v1/tick", json={"now":"2026-04-26T10:40:00Z","available_triggers":list(triggers.keys())}).json()
check("dedup on re-tick", len(r2["actions"])==0)

# 7. reply: auto-reply escalation send->wait->end
mid = "m_001_drmeera_dentist_delhi"
auto = "Thank you for contacting us! Our team will respond shortly."
a1 = client.post("/v1/reply", json={"conversation_id":"conv_a1","merchant_id":mid,"message":auto,"turn_number":2}).json()
a2 = client.post("/v1/reply", json={"conversation_id":"conv_a2","merchant_id":mid,"message":auto,"turn_number":3}).json()
a3 = client.post("/v1/reply", json={"conversation_id":"conv_a3","merchant_id":mid,"message":auto,"turn_number":4}).json()
check("auto-reply 1 -> send", a1["action"]=="send")
check("auto-reply 2 -> wait", a2["action"]=="wait")
check("auto-reply 3 -> end", a3["action"]=="end")

# 8. intent transition -> action mode (actioning words, no qualifying)
ir = client.post("/v1/reply", json={"conversation_id":"conv_i","merchant_id":"m_002_bharat_dentist_mumbai","message":"Ok lets do it. Whats next?","turn_number":2}).json()
b = ir.get("body","").lower()
qualifying = ["would you","do you","can you tell","what if","how about"]
actioning = ["done","sending","draft","here","confirm","proceed","next"]
check("intent -> send", ir["action"]=="send")
check("intent action-mode", any(w in b for w in actioning) and not any(w in b for w in qualifying))

# 9. hostile -> end
hr = client.post("/v1/reply", json={"conversation_id":"conv_h","merchant_id":"m_003_studio11_salon_hyderabad","message":"Stop messaging me. This is useless spam.","turn_number":2}).json()
check("hostile -> end", hr["action"]=="end")

# 10. off-topic -> redirect (send), not end
fr = client.post("/v1/reply", json={"conversation_id":"conv_f","merchant_id":"m_006_southindiancafe_restaurant_bangalore","message":"Can you also help with my GST filing?","turn_number":2}).json()
check("off-topic -> send/redirect", fr["action"]=="send")

# 11. teardown
client.post("/v1/teardown")
check("teardown wipes", client.get("/v1/healthz").json()["contexts_loaded"]["merchant"]==0)

print("\nRESULT:", "ALL PASS" if ok else "SOME FAILED")
sys.exit(0 if ok else 1)
