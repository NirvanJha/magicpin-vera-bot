"""Simulated-judge soak test: full test-window lifecycle with scripted merchant personas.

Warmup (255 contexts) -> 12 ticks x 5 simulated minutes over all 100 triggers, with mid-test
injections -> every action answered by a persona for up to 5 turns -> invariants checked on
EVERY response. Seeded, so failures are reproducible.

    uvicorn bot:app --port 8080 &  python tests/soak_test.py http://127.0.0.1:8080 [seed]
"""
import glob
import json
import random
import re
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 7
rnd = random.Random(SEED)
DS = Path(__file__).resolve().parent.parent / "dataset"
violations = []
latencies = []


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json"})
    t = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            out = r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        out = e.code, json.loads(e.read() or b"{}")
    latencies.append(time.time() - t)
    return out


def bad(rule, detail):
    violations.append((rule, str(detail)[:220]))


def load(sub):
    return [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(str(DS / "expanded" / sub / "*.json")))]


cats = [json.load(open(f, encoding="utf-8")) for f in sorted(glob.glob(str(DS / "categories" / "*.json")))]
merchants, customers, triggers = load("merchants"), load("customers"), load("triggers")
M = {m["merchant_id"]: m for m in merchants}

PERSONAS = {
    "engaged": ["What exactly is this about?", "Is it relevant for us?", "ok let's do it", "CONFIRM", "thanks!"],
    "auto": ["Thank you for contacting us! Our team will respond shortly."] * 5,
    "hostile_then_offtopic": ["You people are useless", "Can you also help me file my GST?", "hmm", "ok fine", "thanks"],
    "stopper": ["Stop messaging me.", "hello?", "yes", "ok", "?"],
    "curious": ["how much does it cost?", "who are you?", "how did you get my number?", "maybe", "no thanks"],
    "busy": ["busy right now, in a meeting", "ok tell me", "haan kar do", "CONFIRM", "shukriya"],
    "hindi": ["kya hai ye?", "haan bhej do", "theek hai", "धन्यवाद", "ok"],
    "decliner": ["no", "no", "no", "no", "no"],
}
QUAL = ["would you", "do you", "can you tell", "what if", "how about"]
ALLOWED_CTA = {"open_ended", "binary", "none"}


def check_reply(r, status, persona, turn, prior_bodies, conv):
    if status != 200:
        return bad("reply_status", (status, r))
    a = r.get("action")
    if a not in ("send", "wait", "end"):
        return bad("reply_action", r)
    if not isinstance(r.get("rationale"), str) or not r["rationale"]:
        bad("reply_rationale", r)
    if a == "send":
        b = r.get("body")
        if not isinstance(b, str) or not b.strip():
            bad("send_empty_body", (conv, r))
        elif b in prior_bodies:
            bad("verbatim_repeat_in_conversation", (conv, persona, b[:80]))
        if r.get("cta") not in ALLOWED_CTA:
            bad("reply_cta", r)
        if isinstance(b, str) and re.search(r"\b(None|nan|undefined|null)\b|\{|\}", b):
            bad("reply_leak", b[:120])
    if a == "wait" and (not isinstance(r.get("wait_seconds"), int) or r["wait_seconds"] <= 0):
        bad("wait_seconds", r)
    return a


# ---------------------------------------------------------------- warmup
s, h = call("GET", "/v1/healthz")
if s != 200:
    print("bot unreachable")
    sys.exit(2)
for c in cats:
    call("POST", "/v1/context", {"scope": "category", "context_id": c["slug"], "version": 1, "payload": c})
for m in merchants:
    call("POST", "/v1/context", {"scope": "merchant", "context_id": m["merchant_id"], "version": 1, "payload": m})
for cu in customers:
    call("POST", "/v1/context", {"scope": "customer", "context_id": cu["customer_id"], "version": 1, "payload": cu})
s, h = call("GET", "/v1/healthz")
if h["contexts_loaded"] != {"category": 5, "merchant": 50, "customer": 200, "trigger": 0}:
    bad("warmup_counts", h)

# ---------------------------------------------------------------- test window
order = triggers[:]
rnd.shuffle(order)
pushed, conv_ids, sent_keys = set(), set(), set()
opted_out, ended_convs = set(), set()
persona_stats, action_count = Counter(), 0
intent_transition_ok = intent_transition_total = 0
auto_ended = auto_total = 0
for tick in range(12):
    now = f"2026-04-26T{10 + (tick * 5) // 60:02d}:{(tick * 5) % 60:02d}:00Z"
    # injections: new triggers each tick + perf updates for 2 merchants every 3 ticks
    for t in order[tick * 9:(tick + 1) * 9]:
        call("POST", "/v1/context", {"scope": "trigger", "context_id": t["id"], "version": 1, "payload": t})
        pushed.add(t["id"])
    if tick % 3 == 2:
        for mid in rnd.sample(sorted(M), 2):
            upd = json.loads(json.dumps(M[mid]))
            upd["performance"]["views"] = int(upd["performance"].get("views", 1000) * rnd.uniform(0.5, 1.6))
            call("POST", "/v1/context", {"scope": "merchant", "context_id": mid, "version": 2 + tick, "payload": upd})
    active = sorted(pushed)
    s, r = call("POST", "/v1/tick", {"now": now, "available_triggers": active})
    if s != 200:
        bad("tick_status", (s, r))
        continue
    acts = r.get("actions", [])
    if len(acts) > 20:
        bad("tick_cap", len(acts))
    recips = [a.get("customer_id") or a.get("merchant_id") for a in acts]
    if len(recips) != len(set(recips)):
        bad("tick_one_per_recipient", recips)
    for a in acts:
        action_count += 1
        for k in ("conversation_id", "merchant_id", "send_as", "trigger_id", "body", "cta", "suppression_key", "rationale"):
            if not a.get(k):
                bad("action_field_missing", (k, a.get("trigger_id")))
        if a["conversation_id"] in conv_ids:
            bad("conversation_id_reused", a["conversation_id"])
        conv_ids.add(a["conversation_id"])
        key = (a.get("customer_id") or a["merchant_id"], a["suppression_key"])
        if key in sent_keys:
            bad("suppression_violated", key)
        sent_keys.add(key)
        if a["merchant_id"] in opted_out and not a.get("customer_id"):
            bad("sent_to_opted_out_merchant", a["merchant_id"])
        if a["cta"] not in ALLOWED_CTA:
            bad("action_cta", a["cta"])
        if a["send_as"] != ("merchant_on_behalf" if a.get("customer_id") else "vera"):
            bad("send_as", a)
        if re.search(r"\b(None|nan|undefined|null)\b|\{|\}", a["body"]):
            bad("action_leak", a["body"][:120])

        # --- play the conversation
        persona = "customer" if a.get("customer_id") else rnd.choice(sorted(PERSONAS))
        script = ["yes please", "which time works?", "1", "thanks", "ok"] if persona == "customer" else PERSONAS[persona]
        persona_stats[persona] += 1
        prior = {a["body"]}
        conv = a["conversation_id"]
        for turn, msg in enumerate(script):
            s2, rr = call("POST", "/v1/reply", {
                "conversation_id": conv, "merchant_id": a["merchant_id"], "customer_id": a.get("customer_id"),
                "from_role": "customer" if a.get("customer_id") else "merchant", "message": msg,
                "received_at": now, "turn_number": turn + 2})
            act = check_reply(rr, s2, persona, turn, prior, conv)
            if act == "send":
                prior.add(rr["body"])
                if msg.lower().startswith("ok let's do it"):
                    intent_transition_total += 1
                    if not any(q in rr["body"].lower() for q in QUAL):
                        intent_transition_ok += 1
            if persona == "stopper" and turn == 0 and act != "end":
                bad("stop_not_ended", rr)
            if persona == "stopper" and turn == 0:
                opted_out.add(a["merchant_id"])
            if persona == "stopper" and turn > 0 and act == "send" and msg in ("hello?", "?"):
                bad("resumed_after_stop_on_noise", (msg, rr.get("body", "")[:80]))
            if persona == "decliner" and turn == 0 and act != "end":
                bad("no_not_ended", rr)
            if act in ("end", "wait"):
                if persona == "auto" and act == "end":
                    auto_ended += 1
                break
        if persona == "auto":
            auto_total += 1

# ---------------------------------------------------------------- teardown
s, r = call("POST", "/v1/teardown", {})
s2, h = call("GET", "/v1/healthz")
if s != 200 or sum(h["contexts_loaded"].values()) != 0:
    bad("teardown_did_not_wipe", (s, r, h))

lat = sorted(latencies)
print(f"seed {SEED}: {action_count} proactive actions, {len(latencies)} HTTP calls, personas {dict(persona_stats)}")
print(f"latency p50 {lat[len(lat) // 2] * 1000:.1f}ms  p99 {lat[int(len(lat) * .99)] * 1000:.1f}ms  max {lat[-1] * 1000:.1f}ms")
print(f"intent transitions actioned without re-qualifying: {intent_transition_ok}/{intent_transition_total}")
print(f"auto-reply personas that reached a graceful exit (wait/end): see ladder; ended={auto_ended}/{auto_total}")
if lat[-1] > 5:
    bad("latency", f"max {lat[-1]:.1f}s")
if intent_transition_ok != intent_transition_total:
    bad("intent_transition", f"{intent_transition_ok}/{intent_transition_total}")
for rule, n in Counter(v[0] for v in violations).items():
    print(f"  VIOLATION {rule} x{n}: {next(v[1] for v in violations if v[0] == rule)}")
print(f"\nRESULT: {'PASS' if not violations else 'FAIL'} — {len(violations)} invariant violations")
sys.exit(0 if not violations else 1)
