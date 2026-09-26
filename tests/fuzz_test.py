"""Seeded structural fuzzer for all POST endpoints.

Invariants: no 5xx, JSON always parseable, response shapes follow the spec, and no raw
Python/JSON artefacts (None, nan, {, 1e+308 ...) ever appear in a message body.

    uvicorn bot:app --port 8080 &  python tests/fuzz_test.py http://127.0.0.1:8080 [iterations] [seed]
"""
import json
import random
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 3000
rnd = random.Random(int(sys.argv[3]) if len(sys.argv) > 3 else 1)
DS = Path(__file__).resolve().parent.parent / "dataset"
fails = []

WEIRD_STR = ["", " ", "None", "null", "NaN", "‮evil", "💥" * 50, "A" * 3000, "<script>alert(1)</script>",
             "Dr. Dr. Dr.", "0", "-1", "2026-13-45", "tonight", "₹", "\n\n\t", "'; DROP TABLE x;--", "मीरा", "{{1}}"]
WEIRD_NUM = [0, -1, 1e308, -1e308, 2 ** 63, 0.0000001, 3.5, 10 ** 12, 99999999999]
KINDS = ["research_digest", "regulation_change", "recall_due", "perf_dip", "perf_spike", "renewal_due", "festival_upcoming",
         "curious_ask_due", "winback_eligible", "ipl_match_today", "review_theme_emerged", "milestone_reached",
         "active_planning_intent", "seasonal_perf_dip", "customer_lapsed_hard", "customer_lapsed_soft", "trial_followup",
         "supply_alert", "chronic_refill_due", "category_seasonal", "gbp_unverified", "cde_opportunity", "competitor_opened",
         "dormant_with_vera", "appointment_tomorrow", "wedding_package_followup", "made_up_kind"]


def junk(depth=0):
    r = rnd.random()
    if depth > 3 or r < 0.3:
        return rnd.choice(WEIRD_STR + WEIRD_NUM + [None, True, False])
    if r < 0.6:
        return [junk(depth + 1) for _ in range(rnd.randint(0, 4))]
    return {rnd.choice(["views", "name", "title", "label", "iso", "delta_pct", "metric", "offers", "x"]): junk(depth + 1)
            for _ in range(rnd.randint(0, 5))}


def mutate(obj, rate=0.25):
    """Keep the real shape but corrupt random leaves/branches."""
    if isinstance(obj, dict):
        return {k: (junk() if rnd.random() < rate else mutate(v, rate)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [junk() if rnd.random() < rate else mutate(v, rate) for v in obj]
    return junk() if rnd.random() < rate else obj


def post(path, raw):
    req = urllib.request.Request(BASE + path, data=raw, method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def check(path, status, body, note):
    if status >= 500:
        fails.append((path, status, note))
        return None
    try:
        return json.loads(body)
    except Exception:
        fails.append((path, "non-json", note))
        return None


LEAK = re.compile(r"\b(None|nan|inf|True|False|undefined)\b|[{}]|\d\.\d+e[+-]\d+|e\+\d{2,}")
seeds = {
    "category": [json.load(open(f, encoding="utf-8")) for f in (DS / "categories").glob("*.json")],
    "merchant": json.load(open(DS / "merchants_seed.json", encoding="utf-8"))["merchants"],
    "customer": json.load(open(DS / "customers_seed.json", encoding="utf-8"))["customers"],
    "trigger": json.load(open(DS / "triggers_seed.json", encoding="utf-8"))["triggers"],
}
id_key = {"category": "slug", "merchant": "merchant_id", "customer": "customer_id", "trigger": "id"}
leaks, actions, trig_ids = [], 0, []

for i in range(N):
    scope = rnd.choice(list(seeds))
    base = rnd.choice(seeds[scope])
    payload = mutate(base)
    cid = f"fz_{scope}_{i}" if scope != "category" else rnd.choice(["dentists", "salons", "gyms", "pharmacies", "restaurants"])
    if scope == "trigger" and isinstance(payload, dict):
        payload["id"], payload["kind"] = cid, rnd.choice(KINDS)
        payload["merchant_id"] = rnd.choice([m["merchant_id"] for m in seeds["merchant"]] + [f"fz_merchant_{i - 1}", None, 5])
        trig_ids.append(cid)
    if scope == "merchant" and isinstance(payload, dict):
        payload["category_slug"] = rnd.choice(["dentists", "salons", "gyms", "pharmacies", "restaurants", None, "xyz"])
    envelope = {"scope": scope, "context_id": cid, "version": rnd.choice([i + 10, 1, "7", None, -3, 2.5]), "payload": payload}
    if rnd.random() < 0.1:
        envelope = mutate(envelope, 0.4)
    s, b = post("/v1/context", json.dumps(envelope, allow_nan=True).encode())
    r = check("/v1/context", s, b, str(envelope)[:120])
    if r is not None and not isinstance(r.get("accepted"), bool):
        fails.append(("/v1/context", "shape", r))

    if i % 25 == 24:  # tick over a random batch of fuzz + real triggers
        batch = rnd.sample(trig_ids, min(20, len(trig_ids))) + [t["id"] for t in rnd.sample(seeds["trigger"], 5)]
        body = {"now": rnd.choice(["2026-04-26T10:00:00Z", "garbage", None, 5]), "available_triggers": batch}
        s, b = post("/v1/tick", json.dumps(body).encode())
        r = check("/v1/tick", s, b, batch[:3])
        if r is not None:
            if not isinstance(r.get("actions"), list):
                fails.append(("/v1/tick", "shape", r))
            elif "error" in r:
                fails.append(("/v1/tick", "swallowed-exception", r["error"]))
            for a in r.get("actions", []) if isinstance(r.get("actions"), list) else []:
                actions += 1
                if not isinstance(a.get("body"), str) or not a["body"].strip():
                    fails.append(("/v1/tick", "empty-body", a.get("trigger_id")))
                elif LEAK.search(a["body"]):
                    leaks.append((a["trigger_id"], LEAK.search(a["body"]).group(0), a["body"][:140]))

    if i % 10 == 9:  # reply fuzz
        msg = rnd.choice(WEIRD_STR + ["ok", "stop", "yes", "who are you?", "haan"]) if rnd.random() < 0.7 else junk()
        body = {"conversation_id": rnd.choice([f"fzc_{i}", "", None, 7]), "merchant_id": rnd.choice([None, "m_001_drmeera_dentist_delhi", 3]),
                "from_role": rnd.choice(["merchant", "customer", None, "judge"]), "message": msg,
                "turn_number": rnd.choice([2, "two", None, -1, 1e9])}
        s, b = post("/v1/reply", json.dumps(body).encode())
        r = check("/v1/reply", s, b, body)
        if r is not None and s == 200:
            if r.get("action") not in ("send", "wait", "end"):
                fails.append(("/v1/reply", "shape", r))
            if r.get("action") == "send" and not (isinstance(r.get("body"), str) and r["body"].strip()):
                fails.append(("/v1/reply", "empty-send", r))
            if "internal fallback" in str(r.get("rationale")):
                fails.append(("/v1/reply", "swallowed-exception", r["rationale"]))

print(f"{N} context pushes, {N // 25} ticks ({actions} actions composed from corrupted data), {N // 10} replies")
for f in fails[:10]:
    print("  FAIL", f)
for l in leaks[:10]:
    print("  LEAK", l)
print(f"\nRESULT: {'PASS' if not fails and not leaks else 'FAIL'} — {len(fails)} failures, {len(leaks)} leaked artefacts")
sys.exit(0 if not fails and not leaks else 1)
