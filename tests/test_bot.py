"""
Local verification script for Vera Bot Endpoints
Tests:
1. Healthz & Metadata
2. Context Ingestion (idempotency, version replacement)
3. Auto-Reply Hell Scenario
4. Intent Transition Scenario
5. Hostile Scenario
6. Tick Scenario with Compositions
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"

def request(method, path, data=None):
    url = f"{BASE_URL}{path}"
    headers = {"Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

def run_tests():
    print("=" * 60)
    print("TESTING VERA BOT ENDPOINTS")
    print("=" * 60)
    
    # 1. Healthz
    hz = request("GET", "/v1/healthz")
    print("\n[1] GET /v1/healthz:", hz)
    assert hz["status"] == "ok"
    
    # 2. Metadata
    meta = request("GET", "/v1/metadata")
    print("\n[2] GET /v1/metadata:", meta)
    assert meta.get("model") and meta.get("team_name")
    
    # 3. Context Push (v1, same v1 again, then higher v2, then lower v1)
    ctx1 = request("POST", "/v1/context", {
        "scope": "category",
        "context_id": "test_cat",
        "version": 2,
        "payload": {"slug": "test_cat", "title": "Test Category v2"}
    })
    print("\n[3a] POST /v1/context (v2):", ctx1)
    assert ctx1["accepted"] is True

    # Same version check (idempotent)
    ctx_same = request("POST", "/v1/context", {
        "scope": "category",
        "context_id": "test_cat",
        "version": 2,
        "payload": {"slug": "test_cat", "title": "Test Category v2"}
    })
    print("[3b] POST /v1/context (same v2 again):", ctx_same)
    assert ctx_same["accepted"] is True

    # Lower version check (stale conflict 409)
    try:
        ctx_stale = request("POST", "/v1/context", {
            "scope": "category",
            "context_id": "test_cat",
            "version": 1,
            "payload": {"slug": "test_cat", "title": "Stale v1"}
        })
        print("[3c] POST /v1/context (lower v1):", ctx_stale)
        assert ctx_stale["accepted"] is False
    except urllib.error.HTTPError as e:
        assert e.code == 409
        stale_res = json.loads(e.read().decode("utf-8"))
        print("[3c] POST /v1/context (lower v1 returned HTTP 409 Conflict):", stale_res)
        assert stale_res["accepted"] is False
    
    # 4. Auto-Reply Hell
    print("\n[4] Scenario: Auto-Reply Hell")
    auto_reply_msg = "Thank you for contacting us! Our team will respond shortly."
    actions = []
    for turn in range(2, 6):  # judge sends the same canned text 4 times
        r = request("POST", "/v1/reply", {
            "conversation_id": "conv_auto_test_1",
            "merchant_id": "m_001_drmeera_dentist_delhi",
            "from_role": "merchant",
            "message": auto_reply_msg,
            "turn_number": turn
        })
        actions.append(r["action"])
        print(f"  Turn {turn} reply to auto-reply:", r)
        if r["action"] == "end":
            break
    assert actions[-1] == "end", f"Bot never ended after 4 auto-replies: {actions}"
    print("  --> PASS: Bot correctly ENDED conversation on canned auto-reply!")

    # 5. Intent Transition
    print("\n[5] Scenario: Intent Transition (Pitch -> Action)")
    intent_msg = "Ok lets do it. Whats next?"
    reply2 = request("POST", "/v1/reply", {
        "conversation_id": "conv_intent_test_1",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "from_role": "merchant",
        "message": intent_msg,
        "turn_number": 2
    })
    print("  Turn 1 reply to commitment:", reply2)
    body_lower = reply2.get("body", "").lower()
    actioning = ["done", "sending", "draft", "here", "confirm", "proceed", "next"]
    qualifying = ["would you", "do you", "can you tell", "what if", "how about"]
    has_action = any(w in body_lower for w in actioning)
    has_qual = any(w in body_lower for w in qualifying)
    assert has_action, f"Missing actioning keywords in body: {reply2.get('body')}"
    assert not has_qual, f"Still qualifying after commitment: {reply2.get('body')}"
    print("  --> PASS: Bot correctly switched to ACTION mode without qualifying!")

    # 6. Hostile / Stop
    print("\n[6] Scenario: Hostility / Stop")
    hostile_msg = "Stop messaging me. This is useless spam."
    reply3 = request("POST", "/v1/reply", {
        "conversation_id": "conv_hostile_test_1",
        "merchant_id": "m_001_drmeera_dentist_delhi",
        "from_role": "merchant",
        "message": hostile_msg,
        "turn_number": 2
    })
    print("  Turn 1 reply to hostility:", reply3)
    assert reply3["action"] == "end"
    print("  --> PASS: Bot correctly ENDED on hostile message!")

    # 7. Tick Scenario
    print("\n[7] Scenario: Tick with active triggers")
    tick_res = request("POST", "/v1/tick", {
        "now": datetime.utcnow().isoformat() + "Z",
        "available_triggers": [
            "trg_013_corporate_thali_planning",
            "trg_022_cde_webinar_dentists",
            "trg_019_chronic_refill_grandfather"
        ]
    })
    print(f"  Actions returned: {len(tick_res['actions'])}")
    for act in tick_res["actions"]:
        print(f"  - Trigger: {act['trigger_id']}, Send As: {act['send_as']}, CTA: {act['cta']}")
        print(f"    Body: {act['body'][:120]}...")
        print(f"    Rationale: {act['rationale']}")
    assert len(tick_res["actions"]) > 0
    print("  --> PASS: Tick generated proactive actions!")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED SUCCESSFULLY!")
    print("=" * 60)

if __name__ == "__main__":
    run_tests()
