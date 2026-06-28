"""Generate playground.html — an interactive chat UI for the deployed bot."""
import json
from pathlib import Path

BASE = "https://vera-bot-eo0m.onrender.com"
DS = Path(__file__).resolve().parent.parent / "dataset"

cats = {json.loads(f.read_text(encoding="utf-8"))["slug"]: json.loads(f.read_text(encoding="utf-8"))
        for f in (DS / "categories").glob("*.json")}
mer = {x["merchant_id"]: x for x in json.loads((DS / "merchants_seed.json").read_text(encoding="utf-8"))["merchants"]}
cus = {x["customer_id"]: x for x in json.loads((DS / "customers_seed.json").read_text(encoding="utf-8"))["customers"]}
trg = {x["id"]: x for x in json.loads((DS / "triggers_seed.json").read_text(encoding="utf-8"))["triggers"]}

# A handful of friendly, labelled scenarios spanning categories.
picks = [
    ("trg_001_research_digest_dentists", "🦷 Dentist — a new research study came out"),
    ("trg_023_competitor_opened_dentist", "🦷 Dentist — a competitor opened nearby"),
    ("trg_010_ipl_match_delhi", "🍕 Restaurant — IPL match in town tonight"),
    ("trg_006_festival_diwali", "💇 Salon — Diwali is coming up"),
    ("trg_018_supply_atorvastatin_recall", "💊 Pharmacy — urgent medicine recall"),
    ("trg_003_recall_due_priya", "🦷 Patient message — Priya is due for a checkup"),
]

scenarios = []
for tid, label in picks:
    t = trg[tid]
    m = mer[t["merchant_id"]]
    c = cus.get(t.get("customer_id")) if t.get("customer_id") else None
    scenarios.append({
        "label": label,
        "category": cats[m["category_slug"]],
        "merchant": m,
        "trigger": t,
        "customer": c,
    })

DATA = json.dumps(scenarios, ensure_ascii=False)

html = """<!doctype html><html><head><meta charset="utf-8">
<title>Vera Bot — Chat Playground</title>
<style>
 body{background:#0b141a;color:#e9edef;font-family:Segoe UI,Arial;margin:0;padding:0}
 header{background:#202c33;padding:14px 18px;font-size:18px;font-weight:600}
 .wrap{max-width:720px;margin:0 auto;padding:16px}
 select,button,input{font-size:15px;border-radius:8px;border:none;padding:10px 12px}
 select{width:100%;margin-bottom:10px;background:#2a3942;color:#e9edef}
 .row{display:flex;gap:8px}
 input{flex:1;background:#2a3942;color:#e9edef}
 button{background:#00a884;color:#fff;cursor:pointer;font-weight:600}
 button:disabled{opacity:.5;cursor:default}
 #chat{background:#0b141a;min-height:300px;margin:14px 0;display:flex;flex-direction:column;gap:10px}
 .b{max-width:80%;padding:10px 13px;border-radius:12px;line-height:1.45;white-space:pre-wrap}
 .bot{align-self:flex-start;background:#202c33}
 .me{align-self:flex-end;background:#005c4b}
 .sys{align-self:center;color:#8696a0;font-size:13px;font-style:italic}
 .why{font-size:12px;color:#8696a0;margin-top:6px}
 .reset{background:#b23b3b;margin-top:10px}
 .note{font-size:13px;color:#8696a0;margin-top:8px}
</style></head><body>
<header>📱 Vera Bot — Chat Playground</header>
<div class="wrap">
 <label>1) Pick a shop + event, then press <b>Start</b>:</label>
 <select id="scn"></select>
 <div class="row"><button id="start">Start conversation</button></div>
 <div id="chat"></div>
 <div class="row">
   <input id="msg" placeholder="Type a reply as the shop owner... (e.g. yes lets do it)" disabled>
   <button id="send" disabled>Send</button>
 </div>
 <div class="note">Tip: try replies like <i>"yes lets do it"</i>, <i>"not interested, stop"</i>, or <i>"thank you for contacting us, our team will respond shortly"</i> to see how it reacts.</div>
 <button class="reset" id="reset">🧹 Reset bot (click when you're done, before the judge runs)</button>
 <div class="note" id="status"></div>
</div>
<script>
const BASE="__BASE__";
const SCENARIOS=__DATA__;
let conv=null, merchantId=null, turn=1, ver=Date.now();
const chat=document.getElementById('chat'), sel=document.getElementById('scn');
const statusEl=document.getElementById('status');
SCENARIOS.forEach((s,i)=>{const o=document.createElement('option');o.value=i;o.textContent=s.label;sel.appendChild(o);});

function add(text,cls,why){const d=document.createElement('div');d.className='b '+cls;d.textContent=text;
  if(why){const w=document.createElement('div');w.className='why';w.textContent='why: '+why;d.appendChild(w);}chat.appendChild(d);chat.scrollTop=chat.scrollHeight;}
function sys(t){const d=document.createElement('div');d.className='sys';d.textContent=t;chat.appendChild(d);}

async function post(path,body){const r=await fetch(BASE+path,{method:'POST',headers{'content-type':'application/json'},body:JSON.stringify(body)});return r.json();}

document.getElementById('start').onclick=async()=>{
  chat.innerHTML=''; statusEl.textContent='Waking the bot & sending data (first time can take ~30s)...';
  const s=SCENARIOS[sel.value]; ver++;
  try{
    await post('/v1/context',{scope:'category',context_id:s.category.slug,version:ver,payload:s.category});
    await post('/v1/context',{scope:'merchant',context_id:s.merchant.merchant_id,version:ver,payload:s.merchant});
    if(s.customer){await post('/v1/context',{scope:'customer',context_id:s.customer.customer_id,version:ver,payload:s.customer});}
    await post('/v1/context',{scope:'trigger',context_id:s.trigger.id,version:ver,payload:s.trigger});
    const res=await post('/v1/tick',{now:new Date().toISOString(),available_triggers:[s.trigger.id]});
    if(!res.actions||!res.actions.length){statusEl.textContent='Bot chose not to send (it may have already sent this once — click Reset, then Start again).';return;}
    const a=res.actions[0]; conv=a.conversation_id; merchantId=a.merchant_id; turn=1;
    sys(a.send_as==='merchant_on_behalf'?'(sent to the customer, on the shop\\'s behalf)':'(sent to the shop owner)');
    add(a.body,'bot',a.rationale);
    document.getElementById('msg').disabled=false; document.getElementById('send').disabled=false;
    statusEl.textContent='Now type a reply below as the shop owner and press Send.';
  }catch(e){statusEl.textContent='Error: '+e+' — wait 30s (bot waking) and try again.';}
};

document.getElementById('send').onclick=async()=>{
  const inp=document.getElementById('msg'); const m=inp.value.trim(); if(!m||!conv)return;
  add(m,'me'); inp.value=''; turn++;
  try{
    const r=await post('/v1/reply',{conversation_id:conv,merchant_id:merchantId,message:m,turn_number:turn});
    if(r.action==='send'){add(r.body,'bot',r.rationale);}
    else if(r.action==='wait'){sys('(bot decided to WAIT '+r.wait_seconds+'s — '+(r.rationale||'')+')');}
    else if(r.action==='end'){sys('(bot ENDED the conversation — '+(r.rationale||'')+')');document.getElementById('msg').disabled=true;document.getElementById('send').disabled=true;}
  }catch(e){statusEl.textContent='Error: '+e;}
};
document.getElementById('msg').addEventListener('keydown',e=>{if(e.key==='Enter')document.getElementById('send').click();});

document.getElementById('reset').onclick=async()=>{await post('/v1/teardown',{});chat.innerHTML='';statusEl.textContent='Bot reset to a clean state — ready for the judge. ✅';document.getElementById('msg').disabled=true;document.getElementById('send').disabled=true;};
</script></body></html>"""

html = html.replace("__BASE__", BASE).replace("__DATA__", DATA)
# fix one JS object-literal typo guard (headers:{...})
html = html.replace("headers{'content-type'", "headers:{'content-type'")
Path(__file__).resolve().parent.joinpath("playground.html").write_text(html, encoding="utf-8")
print("wrote playground.html")
