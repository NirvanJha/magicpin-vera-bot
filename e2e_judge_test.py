"""End-to-end judge-lifecycle test (warmup, 12 ticks, injection, replay, load).

Run against a FRESH local bot only - it pushes test data, opt-outs and suppressions:
    uvicorn bot:app --port 8080 && python e2e_judge_test.py http://127.0.0.1:8080
"""
import glob
import json
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
DS = Path(__file__).parent / "dataset"
results = []


def check(name, ok, detail=""):
    results.append((name, bool(ok)))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def call(method, path, body=None, raw=None, headers=None, timeout=30):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    h = {"Content-Type": "application/json"} if headers is None else headers
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    t = time.time()
    try:
        r = urllib.request.urlopen(req, timeout=timeout)
        return r.status, json.loads(r.read()), (time.time() - t) * 1000
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), (time.time() - t) * 1000


def load_dir(sub):
    return [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(str(DS / "expanded" / sub / "*.json")))]


cats = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(str(DS / "categories" / "*.json")))]
merchants, customers, triggers = load_dir("merchants"), load_dir("customers"), load_dir("triggers")
TRG = {t["id"]: t for t in triggers}
NOW = "2026-04-26T10:00:00Z"

# ---------------------------------------------------------------- 1. warmup
print("\n=== PHASE 1: WARMUP ===")
s, h, lat = call("GET", "/v1/healthz")
check("healthz 200 + zeros before any push", s == 200 and h["status"] == "ok" and sum(h["contexts_loaded"].values()) == 0, h)
s, m, _ = call("GET", "/v1/metadata")
check("metadata has all 7 fields", s == 200 and all(k in m for k in
      ["team_name", "team_members", "model", "approach", "contact_email", "version", "submitted_at"]), m)

for c in cats:
    call("POST", "/v1/context", {"scope": "category", "context_id": c["slug"], "version": 1, "payload": c, "delivered_at": NOW})
for x in merchants:
    call("POST", "/v1/context", {"scope": "merchant", "context_id": x["merchant_id"], "version": 1, "payload": x, "delivered_at": NOW})
for x in customers:
    call("POST", "/v1/context", {"scope": "customer", "context_id": x["customer_id"], "version": 1, "payload": x, "delivered_at": NOW})
s, h, _ = call("GET", "/v1/healthz")
cl = h["contexts_loaded"]
check("healthz reflects 255 base contexts (5/50/200/0)", cl == {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}, cl)

# ---------------------------------------------------------------- 2. context contract
print("\n=== /v1/context CONTRACT ===")
mid = "m_001_drmeera_dentist_delhi"
m1 = next(x for x in merchants if x["merchant_id"] == mid)
s, r1, _ = call("POST", "/v1/context", {"scope": "merchant", "context_id": mid, "version": 1, "payload": {"identity": {"name": "HIJACK"}}})
check("same version re-push is a 200 no-op", s == 200 and r1["accepted"] is True, r1)
s, r, _ = call("POST", "/v1/context", {"scope": "merchant", "context_id": mid, "version": 2, "payload": m1})
check("higher version accepted", s == 200 and r["accepted"] and r["ack_id"] == f"ack_{mid}_v2", r)
s, r, _ = call("POST", "/v1/context", {"scope": "merchant", "context_id": mid, "version": 1, "payload": m1})
check("lower version -> 409 stale_version", s == 409 and r == {"accepted": False, "reason": "stale_version", "current_version": 2}, r)
s, r, _ = call("POST", "/v1/context", {"scope": "banana", "context_id": "x", "version": 1, "payload": {}})
check("invalid scope -> 400 invalid_scope", s == 400 and r.get("reason") == "invalid_scope", (s, r))
s, r, _ = call("POST", "/v1/context", raw=b'{"scope":"merchant", bad json')
check("malformed JSON -> 400 accepted:false", s == 400 and r.get("accepted") is False, (s, r))
s, r, _ = call("POST", "/v1/context", {"scope": "merchant", "context_id": "x", "version": "3", "payload": {}})
check("non-int version -> 400", s == 400 and r.get("reason") == "invalid_version", (s, r))
big = {"slug": "bigcat", "blob": "x" * 480_000}
s, r, lat = call("POST", "/v1/context", {"scope": "category", "context_id": "bigcat", "version": 1, "payload": big})
check(f"~480KB payload accepted ({lat:.0f}ms)", s == 200 and r["accepted"], (s, r))
s, r, _ = call("POST", "/v1/context", raw=json.dumps({"scope": "trigger", "context_id": "t_form", "version": 1, "payload": {}}).encode(),
               headers={"Content-Type": "application/x-www-form-urlencoded"})
check("context works without JSON Content-Type (curl -d)", s == 200 and r["accepted"], (s, r))

# ---------------------------------------------------------------- 3. test window
print("\n=== PHASE 2: 60-MIN TEST WINDOW (12 ticks) ===")
tids = list(TRG)
all_actions, max_lat, conv_ids, bodies = [], 0, set(), []
sent_keys = set()
pushed = set()
ALLOWED_CTA = {"open_ended", "binary", "none"}
for i in range(12):
    batch = tids[i * 8:(i + 1) * 8] + tids[:4]  # 4 triggers stay "active" all window -> suppression must hold
    for t in batch:
        if t not in pushed:
            call("POST", "/v1/context", {"scope": "trigger", "context_id": t, "version": 1, "payload": TRG[t]})
            pushed.add(t)
    now = f"2026-04-26T{10 + (i * 5) // 60:02d}:{(i * 5) % 60:02d}:00Z"
    s, r, lat = call("POST", "/v1/tick", {"now": now, "available_triggers": batch})
    max_lat = max(max_lat, lat)
    acts = r.get("actions", [])
    if s != 200 or len(acts) > 20:
        check(f"tick {i} status/cap", False, (s, len(acts)))
    recips = [a["customer_id"] or a["merchant_id"] for a in acts]
    if len(recips) != len(set(recips)):
        check(f"tick {i}: one message per recipient", False, recips)
    for a in acts:
        k = (a["customer_id"] or a["merchant_id"], a["suppression_key"])
        if k in sent_keys:
            check(f"tick {i}: suppression_key re-sent", False, k)
        sent_keys.add(k)
        all_actions.append(a)
        conv_ids.add(a["conversation_id"])
        bodies.append(a["body"])
REQ = ["conversation_id", "merchant_id", "customer_id", "send_as", "trigger_id", "template_name",
       "template_params", "body", "cta", "suppression_key", "rationale"]
check(f"produced actions across window ({len(all_actions)})", len(all_actions) > 30)
check("every action has all 11 fields", all(all(k in a for k in REQ) for a in all_actions))
check("cta values within {open_ended,binary,none}", all(a["cta"] in ALLOWED_CTA for a in all_actions))
check("send_as correct for scope", all(a["send_as"] == ("merchant_on_behalf" if a["customer_id"] else "vera") for a in all_actions))
check("conversation_ids unique", len(conv_ids) == len(all_actions))
check("no verbatim repeated bodies", len(set(bodies)) == len(bodies))
check(f"max tick latency {max_lat:.0f}ms < 30000ms", max_lat < 30000)
leaks = [b for b in bodies if any(x in b for x in ["None", "placeholder", "{", "}", "nan%", "undefined"])]
check("no template/placeholder leaks in bodies", not leaks, leaks[:2])
check("no suppression violations / recipient dupes (see above)", all(ok for n, ok in results if n.startswith("tick")))

# expired trigger
exp = dict(TRG[tids[-1]], id="trg_expired_test", expires_at="2026-01-01T00:00:00Z", suppression_key="exp:test")
call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_expired_test", "version": 1, "payload": exp})
s, r, _ = call("POST", "/v1/tick", {"now": NOW, "available_triggers": ["trg_expired_test"]})
check("judge-listed trigger honoured even if expires_at passed", len(r["actions"]) == 1, r)
s, r, _ = call("POST", "/v1/tick", {"now": NOW, "available_triggers": ["trg_nope_404", 123, None]})
check("unknown / junk trigger ids -> empty actions, 200", s == 200 and r["actions"] == [], (s, r))
s, r, _ = call("POST", "/v1/tick", raw=b'{"now": "2026-04-26T10:00:00Z", "available_triggers": []}',
               headers={"Content-Type": "application/x-www-form-urlencoded"})
check("tick works without JSON Content-Type (curl -d)", s == 200 and r == {"actions": []}, (s, r))

# ---------------------------------------------------------------- 4. adaptive injection
print("\n=== PHASE 3: ADAPTIVE INJECTION ===")
dent = next(c for c in cats if c["slug"] == "dentists")
dent2 = json.loads(json.dumps(dent))
dent2["digest"].append({"id": "d_NEW_injected", "kind": "research", "title": "Silver diamine fluoride halts 81% of early lesions in 12 months",
                        "source": "IJDR Nov 2026, p.3", "trial_n": 640, "summary": "RCT across 4 Indian cities. SDF arrested 81% of early caries lesions."})
s, r, _ = call("POST", "/v1/context", {"scope": "category", "context_id": "dentists", "version": 2, "payload": dent2})
new_trg = {"id": "trg_inj_research", "scope": "merchant", "kind": "research_digest", "merchant_id": "m_011_dr_sameer_dentist_pune",
           "customer_id": None, "payload": {"category": "dentists", "top_item_id": "d_NEW_injected"}, "urgency": 3,
           "suppression_key": "research:inj:1", "expires_at": "2026-12-31T00:00:00Z"}
if not any(x["merchant_id"] == new_trg["merchant_id"] for x in merchants):
    new_trg["merchant_id"] = merchants[10]["merchant_id"] if merchants[10]["category_slug"] == "dentists" else \
        next(x["merchant_id"] for x in merchants if x["category_slug"] == "dentists" and x["merchant_id"] != mid)
call("POST", "/v1/context", {"scope": "trigger", "context_id": new_trg["id"], "version": 1, "payload": new_trg})
s, r, _ = call("POST", "/v1/tick", {"now": "2026-04-26T11:05:00Z", "available_triggers": [new_trg["id"]]})
b = r["actions"][0]["body"] if r["actions"] else ""
check("new digest item used (title+source+trial_n)", "Silver diamine" in b and "IJDR Nov 2026" in b and "640" in b, b)

pm = json.loads(json.dumps(next(x for x in merchants if x["merchant_id"] == "m_002_bharat_dentist_mumbai")))
pm["performance"].update({"views": 7777, "calls": 3, "ctr": 0.011})
call("POST", "/v1/context", {"scope": "merchant", "context_id": pm["merchant_id"], "version": 5, "payload": pm})
dip = {"id": "trg_inj_dip", "scope": "merchant", "kind": "perf_dip", "merchant_id": pm["merchant_id"], "customer_id": None,
       "payload": {"metric": "calls", "delta_pct": -0.62, "window": "7d"}, "urgency": 4, "suppression_key": "dip:inj",
       "expires_at": "2026-12-31T00:00:00Z"}
call("POST", "/v1/context", {"scope": "trigger", "context_id": dip["id"], "version": 1, "payload": dip})
s, r, _ = call("POST", "/v1/tick", {"now": "2026-04-26T11:10:00Z", "available_triggers": [dip["id"]]})
b = r["actions"][0]["body"] if r["actions"] else ""
check("updated perf snapshot used (7,777 views / 62%)", "7,777" in b and "62%" in b, b)

cust = {"customer_id": "c_inj_neha", "merchant_id": pm["merchant_id"], "identity": {"name": "Neha", "language_pref": "en"},
        "relationship": {"last_visit": "2025-10-20", "visits_total": 3}, "state": "lapsed_soft",
        "preferences": {"preferred_slots": "weekend_morning"}, "consent": {"scope": ["recall_reminders"]}}
call("POST", "/v1/context", {"scope": "customer", "context_id": "c_inj_neha", "version": 1, "payload": cust})
rc = {"id": "trg_inj_recall", "scope": "customer", "kind": "recall_due", "merchant_id": pm["merchant_id"], "customer_id": "c_inj_neha",
      "payload": {"service_due": "6_month_cleaning", "available_slots": [{"label": "Sat 2 May, 10am"}, {"label": "Sun 3 May, 11am"}]},
      "urgency": 3, "suppression_key": "recall:inj", "expires_at": "2026-12-31T00:00:00Z"}
call("POST", "/v1/context", {"scope": "trigger", "context_id": rc["id"], "version": 1, "payload": rc})
s, r, _ = call("POST", "/v1/tick", {"now": "2026-04-26T11:12:00Z", "available_triggers": [rc["id"]]})
a = r["actions"][0] if r["actions"] else {}
b = a.get("body", "")
check("surprise customer: merchant_on_behalf, name, slots, gap", a.get("send_as") == "merchant_on_behalf" and "Neha" in b
      and "Sat 2 May" in b and "months" in b, b)

# ---------------------------------------------------------------- 5. replay
print("\n=== PHASE 4: REPLAY SCENARIOS ===")
s, r, _ = call("POST", "/v1/tick", {"now": "2026-04-26T11:20:00Z", "available_triggers": ["trg_001_research_digest_dentists"]})
if not r["actions"]:
    call("POST", "/v1/context", {"scope": "trigger", "context_id": "trg_001_research_digest_dentists", "version": 1,
                                 "payload": TRG["trg_001_research_digest_dentists"]})
    s, r, _ = call("POST", "/v1/tick", {"now": "2026-04-26T11:20:00Z", "available_triggers": ["trg_001_research_digest_dentists"]})
conv = r["actions"][0]["conversation_id"] if r["actions"] else "conv_fallback"


def reply(cid, msg, turn, role="merchant", merchant=mid):
    s, r, lat = call("POST", "/v1/reply", {"conversation_id": cid, "merchant_id": merchant, "customer_id": None,
                                           "from_role": role, "message": msg, "received_at": NOW, "turn_number": turn})
    return r, lat


AUTO = "Thank you for contacting Dr. Meera's Dental Clinic! Our team will respond shortly."
acts = [reply("conv_auto_same", AUTO, i + 2)[0]["action"] for i in range(4)]
check(f"auto-reply hell (same conv) {acts}", acts[:3] == ["send", "wait", "end"] and acts[3] == "end")
acts = [reply(f"conv_auto_{i}", AUTO, 2, merchant="m_003_studio11_salon_hyderabad")[0]["action"] for i in range(4)]
check(f"auto-reply across convs (local judge style) ends {acts}", "end" in acts)

r2, _ = reply(conv, "What is this about exactly?", 2)
r3, _ = reply(conv, "Is it relevant for my patients?", 3)
r4, _ = reply(conv, "Ok let's do it", 4)
body = r4.get("body", "").lower()
qual = ["would you", "do you", "can you tell", "what if", "how about"]
check("intent transition: qualifying turns answered with send", r2["action"] == "send" and r3["action"] == "send")
check("intent transition: action mode, no re-qualifying", r4["action"] == "send" and not any(q in body for q in qual)
      and any(w in body for w in ["done", "here", "draft", "sending"]), r4)
r5, _ = reply(conv, "CONFIRM", 5)
check("confirm after draft -> executes + closes loop", r5["action"] == "send" and "done" in r5["body"].lower(), r5)

rh, _ = reply("conv_hostile", "You people are useless, total spam", 2, merchant="m_005_pizzajunction_restaurant_delhi")
rg, _ = reply("conv_hostile", "Can you also help me file my GST?", 3, merchant="m_005_pizzajunction_restaurant_delhi")
check("abuse -> polite apology (send, no hard sell)", rh["action"] == "send" and "sorry" in rh["body"].lower(), rh)
check("GST ask -> declined politely + back on mission", rg["action"] == "send" and "ca" in rg["body"].lower(), rg)
rs, _ = reply("conv_stop", "Stop messaging me. This is useless spam.", 2, merchant="m_010_sunrisepharm_pharmacy_lucknow")
check("explicit STOP -> end", rs["action"] == "end", rs)
t21 = "trg_021_unverified_gbp_sunrise"
call("POST", "/v1/context", {"scope": "trigger", "context_id": t21, "version": 1, "payload": TRG[t21]})
s, r, _ = call("POST", "/v1/tick", {"now": "2026-04-26T11:30:00Z", "available_triggers": [t21]})
check("opted-out merchant gets no further tick sends", r["actions"] == [], r)

cases = {
    "Yes stop the old offer and run the new one": "send",
    "No thanks": "end",
    "not now, maybe next week": "wait",
    "I am busy, call me later": "wait",
    "haan": "send",
    "ok": "send",
    "What's the price?": "send",
}
for msg, want in cases.items():
    rr, _ = reply(f"conv_case_{abs(hash(msg))}", msg, 2)
    check(f"'{msg}' -> {want}", rr["action"] == want, rr)
rr, _ = reply("conv_cust", "Yes book Wed", 2, role="customer")
check("customer reply gets customer-facing text (no 'patient list')", "patient list" not in rr.get("body", ""), rr)
s, r, _ = call("POST", "/v1/reply", raw=b'{"conversation_id":"c_form","from_role":"merchant","message":"Yes, send me the abstract","turn_number":2}',
               headers={"Content-Type": "application/x-www-form-urlencoded"})
check("reply works without JSON Content-Type (curl -d)", s == 200 and r["action"] == "send", (s, r))
s, r, _ = call("POST", "/v1/reply", {"conversation_id": "x"})
check("reply missing message -> 400, not 500", s == 400, (s, r))
for a in ["send", "wait", "end"]:
    pass
wait_ok = all(("wait_seconds" in x) for x in [reply("cw", "busy right now", 2)[0]])
check("wait responses carry wait_seconds", wait_ok)

# ---------------------------------------------------------------- 6. load
print("\n=== RATE: 10 req/s burst x 5s ===")


def hit(i):
    if i % 3 == 0:
        return call("GET", "/v1/healthz")
    if i % 3 == 1:
        return call("POST", "/v1/reply", {"conversation_id": f"load_{i}", "merchant_id": mid, "from_role": "merchant",
                                          "message": "ok", "turn_number": 2})
    return call("POST", "/v1/tick", {"now": NOW, "available_triggers": tids[:20]})


with ThreadPoolExecutor(10) as ex:
    out = list(ex.map(hit, range(50)))
check(f"50 concurrent requests all 200 (max {max(o[2] for o in out):.0f}ms)", all(o[0] == 200 for o in out))
s, h, _ = call("GET", "/v1/healthz")
check("healthz still ok after load", s == 200 and h["status"] == "ok")

print("\n=== SAMPLE MESSAGES ===")
for a in all_actions[:6]:
    print(f"- [{a['trigger_id']}] {a['body']}\n")
passed = sum(ok for _, ok in results)
print(f"\nRESULT: {passed}/{len(results)} checks passed")
sys.exit(0 if passed == len(results) else 1)
