"""Restart-resilience + self-honoured waits.

Boots the bot with VERA_STATE_FILE, pushes context, hard-kills it (no graceful
shutdown), boots it again and checks nothing the judge pushed was lost.

    python tests/restart_test.py
"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(os.environ.get("RESTART_TEST_PORT", "8093"))
BASE = f"http://127.0.0.1:{PORT}"
STATE = Path(tempfile.gettempdir()) / f"vera_restart_test_{os.getpid()}.json"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {detail}" if detail and not ok else ""))


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # 4xx bodies are part of the contract (e.g. 409 stale_version)
        return json.loads(e.read())


def boot():
    env = dict(os.environ, VERA_STATE_FILE=str(STATE), VERA_DISABLE_SEED="1")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "bot:app", "--port", str(PORT)], cwd=ROOT, env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            call("GET", "/v1/healthz")
            return p
        except Exception:
            time.sleep(0.2)
    p.kill()
    raise SystemExit("bot did not start")


def load(name):
    return json.load(open(ROOT / "dataset" / name, encoding="utf-8"))


cat = json.load(open(ROOT / "dataset" / "categories" / "dentists.json", encoding="utf-8"))
merchant = next(m for m in load("merchants_seed.json")["merchants"] if m["merchant_id"] == "m_001_drmeera_dentist_delhi")
trigs = {t["id"]: t for t in load("triggers_seed.json")["triggers"]}
T1, T2 = "trg_001_research_digest_dentists", "trg_002_compliance_dci_radiograph"

STATE.unlink(missing_ok=True)
p = boot()
try:
    call("POST", "/v1/context", {"scope": "category", "context_id": "dentists", "version": 1, "payload": cat})
    call("POST", "/v1/context", {"scope": "merchant", "context_id": merchant["merchant_id"], "version": 4, "payload": merchant})
    for t in (T1, T2):
        call("POST", "/v1/context", {"scope": "trigger", "context_id": t, "version": 1, "payload": trigs[t]})
    a = call("POST", "/v1/tick", {"now": "2026-04-26T10:00:00Z", "available_triggers": [T1]})["actions"]
    check("first tick sends", len(a) == 1)
    r = call("POST", "/v1/reply", {"conversation_id": a[0]["conversation_id"], "merchant_id": merchant["merchant_id"],
                                   "from_role": "merchant", "message": "I'm busy right now, in a meeting", "turn_number": 2})
    check("busy -> wait 1800s", r["action"] == "wait" and r["wait_seconds"] == 1800, r)
    a = call("POST", "/v1/tick", {"now": "2026-04-26T10:05:00Z", "available_triggers": [T2]})["actions"]
    check("bot honours its own wait: no new send 5 min later", a == [], a)
    time.sleep(2.5)  # let the background saver flush
finally:
    p.kill()  # hard kill: no shutdown hook runs
    p.wait()

p = boot()
try:
    h = call("GET", "/v1/healthz")["contexts_loaded"]
    check("contexts survive a hard restart", h == {"category": 1, "merchant": 1, "customer": 0, "trigger": 2}, h)
    r = call("POST", "/v1/context", {"scope": "merchant", "context_id": merchant["merchant_id"], "version": 3, "payload": merchant})
    check("version history survives (v3 < v4 still stale)", r.get("reason") == "stale_version", r)
    a = call("POST", "/v1/tick", {"now": "2026-04-26T10:10:00Z", "available_triggers": [T1]})["actions"]
    check("suppression survives (T1 not re-sent)", a == [], a)
    a = call("POST", "/v1/tick", {"now": "2026-04-26T10:35:00Z", "available_triggers": [T2]})["actions"]
    check("after the 30-min wait expires, T2 is sent", len(a) == 1, a)
    time.sleep(2.5)
    check("state file exists before teardown", STATE.is_file())
    r = call("POST", "/v1/teardown", {})
    h = call("GET", "/v1/healthz")["contexts_loaded"]
    check("teardown wipes memory (brief §11)", r.get("wiped") is True and sum(h.values()) == 0, (r, h))
    check("teardown deletes the on-disk snapshot (no context persists after the test)", not STATE.exists())
finally:
    p.kill()
    p.wait()
    STATE.unlink(missing_ok=True)

print(f"\nRESULT: {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
