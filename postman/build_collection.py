"""Generates the Postman collection + environments from the real dataset.

    python postman/build_collection.py
Import postman/Vera.postman_collection.json and one environment into Postman, then Run collection.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "dataset"
OUT = ROOT / "postman"

cat = json.load(open(D / "categories" / "dentists.json", encoding="utf-8"))
merchant = next(m for m in json.load(open(D / "merchants_seed.json", encoding="utf-8"))["merchants"]
                if m["merchant_id"] == "m_001_drmeera_dentist_delhi")
customer = next(c for c in json.load(open(D / "customers_seed.json", encoding="utf-8"))["customers"]
                if c["customer_id"] == "c_001_priya_for_m001")
trig = {t["id"]: t for t in json.load(open(D / "triggers_seed.json", encoding="utf-8"))["triggers"]}
T_DIGEST, T_RECALL = "trg_001_research_digest_dentists", "trg_003_recall_due_priya"
MID, CID = merchant["merchant_id"], customer["customer_id"]

OK_200 = "pm.test('HTTP 200', () => pm.response.to.have.status(200));"
FAST = "pm.test('responds well under the 30s judge timeout', () => pm.expect(pm.response.responseTime).to.be.below(3000));"


def ctx(scope, cid, payload, version=1):
    return {"scope": scope, "context_id": cid, "version": version, "payload": payload, "delivered_at": "2026-04-26T10:00:00Z"}


def reply(conv, msg, turn, role="merchant", cust=None):
    return {"conversation_id": conv, "merchant_id": MID, "customer_id": cust, "from_role": role, "message": msg,
            "received_at": "2026-04-26T10:45:00Z", "turn_number": turn}


# (folder, name, method, path, body | raw-string, tests[], expected_status, description)
R = []


def add(folder, name, method, path, body=None, tests=(), status=200, desc=""):
    R.append((folder, name, method, path, body, list(tests), status, desc))


F0, F1, F2, F3, F4, F5 = ("0 · Reset", "1 · Warmup (judge phase 1)", "2 · Context contract", "3 · Tick (judge phase 2)",
                          "4 · Replay scenarios (judge phase 4)", "5 · Teardown")
add(F0, "Teardown (start clean)", "POST", "/v1/teardown", {}, [OK_200, "pm.test('wiped', () => pm.expect(pm.response.json().wiped).to.eql(true));"],
    desc="Wipes all bot state so the run is repeatable. Against the LIVE bot this is fine before evaluation — never during it.")
add(F1, "GET /v1/healthz", "GET", "/v1/healthz", None, [OK_200, FAST,
    "const j = pm.response.json();",
    "pm.test('status ok', () => pm.expect(j.status).to.eql('ok'));",
    "pm.test('contexts_loaded has all 4 scopes', () => pm.expect(j.contexts_loaded).to.have.all.keys('category','merchant','customer','trigger'));",
    "pm.test('starts empty', () => pm.expect(Object.values(j.contexts_loaded).reduce((a,b)=>a+b,0)).to.eql(0));"])
add(F1, "GET /v1/metadata", "GET", "/v1/metadata", None, [OK_200,
    "const j = pm.response.json();",
    "['team_name','team_members','model','approach','contact_email','version','submitted_at'].forEach(k => pm.test('has ' + k, () => pm.expect(j).to.have.property(k)));"])
ACK = ["const j = pm.response.json();", "pm.test('accepted', () => pm.expect(j.accepted).to.eql(true));",
       "pm.test('ack_id format', () => pm.expect(j.ack_id).to.match(/^ack_.+_v\\d+$/));"]
add(F2, "Push category (dentists)", "POST", "/v1/context", ctx("category", "dentists", cat), [OK_200] + ACK)
add(F2, "Push merchant (Dr. Meera)", "POST", "/v1/context", ctx("merchant", MID, merchant), [OK_200] + ACK)
add(F2, "Push customer (Priya)", "POST", "/v1/context", ctx("customer", CID, customer), [OK_200] + ACK)
add(F2, "Push trigger (research digest)", "POST", "/v1/context", ctx("trigger", T_DIGEST, trig[T_DIGEST]), [OK_200] + ACK)
add(F2, "Push trigger (recall due)", "POST", "/v1/context", ctx("trigger", T_RECALL, trig[T_RECALL]), [OK_200] + ACK)
add(F2, "Same version again → idempotent no-op", "POST", "/v1/context", ctx("merchant", MID, {"identity": {"name": "HIJACK"}}),
    [OK_200, "pm.test('accepted (no-op)', () => pm.expect(pm.response.json().accepted).to.eql(true));"],
    desc="Spec: re-posting the same version is a no-op. The payload must NOT change (the next tick still says Dr. Meera).")
add(F2, "Older version → 409 stale_version", "POST", "/v1/context", ctx("merchant", MID, merchant, version=0),
    ["pm.test('HTTP 409', () => pm.response.to.have.status(409));",
     "pm.test('reason stale_version', () => pm.expect(pm.response.json().reason).to.eql('stale_version'));"], status=409)
add(F2, "Invalid scope → 400", "POST", "/v1/context", ctx("banana", "x", {}),
    ["pm.test('HTTP 400', () => pm.response.to.have.status(400));",
     "pm.test('reason invalid_scope', () => pm.expect(pm.response.json().reason).to.eql('invalid_scope'));"], status=400)
add(F2, "Malformed JSON → 400", "POST", "/v1/context", '{"scope": "merchant", broken',
    ["pm.test('HTTP 400', () => pm.response.to.have.status(400));",
     "pm.test('accepted false', () => pm.expect(pm.response.json().accepted).to.eql(false));"], status=400)
ACTION = [OK_200, FAST, "const acts = pm.response.json().actions;", "pm.test('at most 20 actions', () => pm.expect(acts.length).to.be.at.most(20));"]
add(F3, "Tick → Vera composes the research digest", "POST", "/v1/tick",
    {"now": "2026-04-26T10:30:00Z", "available_triggers": [T_DIGEST]}, ACTION + [
        "pm.test('one action', () => pm.expect(acts.length).to.eql(1));",
        "const a = acts[0];",
        "['conversation_id','merchant_id','send_as','trigger_id','template_name','template_params','body','cta','suppression_key','rationale'].forEach(k => pm.test('action has ' + k, () => pm.expect(a).to.have.property(k)));",
        "pm.test('merchant-facing → send_as vera', () => pm.expect(a.send_as).to.eql('vera'));",
        "pm.test('cta is valid', () => pm.expect(['open_ended','binary','none']).to.include(a.cta));",
        "pm.test('grounded: cites the real source + number', () => { pm.expect(a.body).to.include('JIDA'); pm.expect(a.body).to.include('2,100'); });",
        "pm.test('same-version no-op kept the real name', () => pm.expect(a.body).to.include('Dr. Meera'));",
        "pm.collectionVariables.set('convId', a.conversation_id);"])
add(F3, "Tick again → suppressed (no repeat send)", "POST", "/v1/tick",
    {"now": "2026-04-26T10:35:00Z", "available_triggers": [T_DIGEST]}, ACTION + [
        "pm.test('suppression_key respected: nothing re-sent', () => pm.expect(acts.length).to.eql(0));"])
add(F3, "Tick → customer recall (on merchant's behalf)", "POST", "/v1/tick",
    {"now": "2026-04-26T10:40:00Z", "available_triggers": [T_RECALL]}, ACTION + [
        "pm.test('one action', () => pm.expect(acts.length).to.eql(1));",
        "pm.test('customer-facing → merchant_on_behalf', () => pm.expect(acts[0].send_as).to.eql('merchant_on_behalf'));",
        "pm.test('uses the real slots', () => pm.expect(acts[0].body).to.include('Wed 5 Nov'));",
        "pm.collectionVariables.set('custConvId', acts[0].conversation_id);"])
SEND = [OK_200, FAST, "const j = pm.response.json();", "pm.test('action send', () => pm.expect(j.action).to.eql('send'));",
        "pm.test('non-empty body', () => pm.expect(j.body).to.be.a('string').and.not.empty);"]
add(F4, "Question → grounded answer", "POST", "/v1/reply", reply("{{convId}}", "What exactly did the study find?", 2),
    SEND + ["pm.test('answers with the actual finding', () => pm.expect(j.body).to.include('38%'));"])
add(F4, "Intent transition: 'let's do it' → action, no re-qualifying", "POST", "/v1/reply",
    reply("{{convId}}", "Ok lets do it. Whats next?", 3),
    SEND + ["pm.test('no qualifying questions', () => ['would you','do you','can you tell','what if','how about'].forEach(q => pm.expect(j.body.toLowerCase()).to.not.include(q)));"])
add(F4, "CONFIRM → executed", "POST", "/v1/reply", reply("{{convId}}", "CONFIRM", 4), SEND + [
    "pm.test('says it is live', () => pm.expect(j.body.toLowerCase()).to.include('live'));"])
AUTO = "Thank you for contacting us! Our team will respond shortly."
add(F4, "Auto-reply #1 → one nudge", "POST", "/v1/reply", reply("pm_auto", AUTO, 2), SEND)
add(F4, "Auto-reply #2 → wait 24h", "POST", "/v1/reply", reply("pm_auto", AUTO, 3), [OK_200,
    "const j = pm.response.json();", "pm.test('action wait', () => pm.expect(j.action).to.eql('wait'));",
    "pm.test('wait_seconds 86400', () => pm.expect(j.wait_seconds).to.eql(86400));"])
add(F4, "Auto-reply #3 → graceful end", "POST", "/v1/reply", reply("pm_auto", AUTO, 4), [OK_200,
    "pm.test('action end', () => pm.expect(pm.response.json().action).to.eql('end'));"])
add(F4, "Off-topic (GST) → polite decline, back on mission", "POST", "/v1/reply",
    reply("pm_gst", "Can you also help me file my GST?", 2), SEND + ["pm.test('points to a CA', () => pm.expect(j.body).to.include('CA'));"])
add(F4, "Busy → wait 30 min", "POST", "/v1/reply", reply("pm_busy", "I'm busy right now, in a meeting", 2), [OK_200,
    "const j = pm.response.json();", "pm.test('action wait', () => pm.expect(j.action).to.eql('wait'));",
    "pm.test('wait_seconds 1800', () => pm.expect(j.wait_seconds).to.eql(1800));"])
add(F4, "Hostile opt-out → end", "POST", "/v1/reply", reply("pm_stop", "Stop messaging me. This is useless spam.", 2), [OK_200,
    "pm.test('action end', () => pm.expect(pm.response.json().action).to.eql('end'));"])
add(F4, "Customer books slot 1 → confirmation", "POST", "/v1/reply", reply("{{custConvId}}", "Yes please, 1", 2, "customer", CID),
    SEND + ["pm.test('booked the real slot', () => { pm.expect(j.body).to.include('Booked'); pm.expect(j.body).to.include('Wed 5 Nov'); });"])
add(F5, "Teardown → wipe", "POST", "/v1/teardown", {}, [OK_200, "pm.test('wiped', () => pm.expect(pm.response.json().wiped).to.eql(true));"])
add(F5, "Healthz after teardown → all zeros", "GET", "/v1/healthz", None, [OK_200,
    "pm.test('empty again', () => pm.expect(Object.values(pm.response.json().contexts_loaded).reduce((a,b)=>a+b,0)).to.eql(0));"])


def item(name, method, path, body, tests, desc):
    req = {"method": method, "header": [], "url": {"raw": "{{baseUrl}}" + path, "host": ["{{baseUrl}}"],
                                                  "path": [p for p in path.split("/") if p]}}
    if desc:
        req["description"] = desc
    if body is not None:
        req["header"] = [{"key": "Content-Type", "value": "application/json"}]
        raw = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False, indent=2)
        req["body"] = {"mode": "raw", "raw": raw, "options": {"raw": {"language": "json"}}}
    return {"name": name, "event": [{"listen": "test", "script": {"type": "text/javascript", "exec": tests}}], "request": req}


folders = {}
for folder, name, method, path, body, tests, status, desc in R:
    folders.setdefault(folder, []).append(item(name, method, path, body, tests, desc))
collection = {
    "info": {"name": "Vera Bot — magicpin judge flow",
             "description": "The judge harness lifecycle as runnable requests with test assertions: warmup, context contract, "
                            "tick, replay scenarios, teardown. Pick the Local or Live environment, then Run collection.",
             "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
    "item": [{"name": f, "item": items} for f, items in folders.items()],
    "variable": [{"key": "convId", "value": ""}, {"key": "custConvId", "value": ""}],
}
OUT.mkdir(exist_ok=True)
(OUT / "Vera.postman_collection.json").write_text(json.dumps(collection, ensure_ascii=False, indent=2), encoding="utf-8")
for env, url in (("Local", "http://127.0.0.1:8080"), ("Live", "https://magicpin-vera-bot-vtz2.onrender.com")):
    (OUT / f"{env}.postman_environment.json").write_text(json.dumps({
        "name": f"Vera {env}", "values": [{"key": "baseUrl", "value": url, "type": "default", "enabled": True}],
        "_postman_variable_scope": "environment"}, indent=2), encoding="utf-8")

print(f"{len(R)} requests in {len(folders)} folders -> postman/")
