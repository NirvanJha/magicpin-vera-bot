"""Conversation-quality checks on real contexts (offline, calls respond() directly).

Each scenario is a multi-turn script with assertions on what Vera actually says —
the behaviours the judge's replay phase scores (brief §8 "Replay test", testing brief §4 Phase 4).
    python tests/conversation_test.py
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from conversation_handlers import ConversationState, respond  # noqa: E402

DS = ROOT / "dataset"
cat = json.load(open(DS / "categories/dentists.json", encoding="utf-8"))
merchant = next(m for m in json.load(open(DS / "merchants_seed.json", encoding="utf-8"))["merchants"]
                if m["merchant_id"] == "m_001_drmeera_dentist_delhi")
trigger = next(t for t in json.load(open(DS / "triggers_seed.json", encoding="utf-8"))["triggers"]
               if t["id"] == "trg_001_research_digest_dentists")
results = []
QUAL = ("would you", "do you", "can you tell", "what if", "how about")


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"\n       -> {str(detail)[:260]}" if detail and not ok else ""))


def convo(script, role="merchant", ctx=True):
    st = ConversationState(conversation_id="t", merchant_id=merchant["merchant_id"], trigger_id=trigger["id"])
    st.turns.append({"from": "vera", "msg": "opening message"})
    out = []
    for msg in script:
        st.turns.append({"from": role, "msg": msg})
        r = respond(st, msg, merchant_context=merchant if ctx else None, category_context=cat if ctx else None,
                    trigger_context=trigger if ctx else None, from_role=role)
        if r["action"] == "send":
            st.turns.append({"from": "vera", "msg": r["body"]})
        out.append(r)
    return out


# 1. Intent transition (judge replay #2): 2 qualifying turns, then "ok let's do it"
r = convo(["What exactly did the study find?", "Is it relevant for my patients?", "ok let's do it"])
check("qualifying Q1 answered with the study's actual finding", "38%" in r[0].get("body", ""), r[0])
check("relevance Q answered with the patient segment, not generic stats", "high-risk" in r[1].get("body", "").lower(), r[1])
check("'let's do it' -> delivers the abstract immediately, no re-qualifying",
      r[2]["action"] == "send" and "JIDA" in r[2]["body"] and not any(q in r[2]["body"].lower() for q in QUAL), r[2])

# 2. Edit requests are reflected, CONFIRM executes, thanks closes
r = convo(["ok let's do it", "Can you change the message to mention evening slots?", "CONFIRM", "thanks!"])
check("edit request is reflected in the reply", "evening slots" in r[1].get("body", ""), r[1])
check("CONFIRM after the draft -> executed", r[2]["action"] == "send" and "live" in r[2]["body"].lower(), r[2])
check("'thanks' after execution -> graceful end (no more selling)", r[3]["action"] == "end", r[3])

# 3. Hostile then off-topic (judge replay #3)
r = convo(["You people are useless, total spam", "Can you also help me file my GST?"])
check("abuse -> apology with a clean opt-out, no pitch", r[0]["action"] == "send" and "sorry" in r[0]["body"].lower()
      and "STOP" in r[0]["body"], r[0])
check("GST -> declined politely and steered back on-mission", r[1]["action"] == "send" and "CA" in r[1]["body"]
      and "38%" in r[1]["body"], r[1])

# 4. Auto-reply hell with CHANGING wording (judge replay #1 variant)
r = convo(["Thank you for contacting Dr. Meera's Dental Clinic. We will get back to you shortly.",
           "Hello! We are currently closed. Our business hours are 10am-7pm.",
           "Namaste 🙏 Aapka message mil gaya hai, hum jaldi reply karenge.",
           "This is an automated response. For appointments call the front desk."])
check("varied auto-replies: nudge -> wait -> end", [x["action"] for x in r[:3]] == ["send", "wait", "end"], [x["action"] for x in r])

# 5. Identity, privacy, delegation, channel switch
r = convo(["Who are you? Is this a bot?"])
check("identity: says it is Vera, magicpin's assistant (honest, not a human)", "Vera" in r[0]["body"] and "magicpin" in r[0]["body"], r[0])
r = convo(["How did you get my number?"])
check("privacy: explains why it has the number + offers STOP", "STOP" in r[0]["body"] and "magicpin" in r[0]["body"], r[0])
r = convo(["my son handles this, talk to him"])
check("delegation: no request for a third party's phone number", "number" not in r[0]["body"].lower(), r[0])
r = convo(["Can you call me instead?"])
check("channel switch: stays on WhatsApp but still delivers the fact", "38%" in r[0]["body"], r[0])

# 6. Refusals are respected in every language
for msg in ["ok stop", "pls stop", "👎", "नहीं चाहिए, मत भेजो", "no thanks", "I said stop"]:
    r = convo([msg])
    check(f"refusal respected: {msg!r} -> end", r[0]["action"] == "end", r[0])
r = convo(["Stop messaging me.", "hello?"])
check("after opting out, noise doesn't restart the pitch", r[1]["action"] == "end", r[1])
r = convo(["Stop messaging me.", "wait, actually what was the study?"])
check("after opting out, a genuine question is still answered", r[1]["action"] == "send", r[1])

# 7. Language follows the merchant per turn
r = convo(["haan bhej do"])
check("Hindi reply -> Hinglish response", any(w in r[0]["body"] for w in ("kar", "hai", "karein", "dungi", "CONFIRM")), r[0])

# 8. Never repeats verbatim, even when asked the same thing 4 times
r = convo(["what is this?"] * 4)
bodies = [x.get("body") for x in r if x["action"] == "send"]
check("same question x4 -> no verbatim repeat", len(bodies) == len(set(bodies)), bodies)

# 9. Customer-facing role
st = ConversationState(conversation_id="c", merchant_id=merchant["merchant_id"], customer_id="c_001_priya_for_m001")
st.turns.append({"from": "customer", "msg": "yes book Wed"})
rc = respond(st, "yes book Wed", merchant_context=merchant, category_context=cat,
             trigger_context=next(t for t in json.load(open(DS / "triggers_seed.json", encoding="utf-8"))["triggers"]
                                  if t["id"] == "trg_003_recall_due_priya"), from_role="customer")
check("customer yes -> booking confirmation with the real slot, not merchant jargon",
      "Booked" in rc["body"] and "Wed 5 Nov" in rc["body"] and "patient list" not in rc["body"], rc)

# 10. Works with no context at all (brief's 2-arg respond)
r = convo(["ok let's do it", "what does it cost?"], ctx=False)
check("no-context conversation still sensible", all(x["action"] in ("send", "wait", "end") for x in r), r)

print(f"\nRESULT: {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
