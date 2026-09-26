"""Labelled corpus for the reply classifier. Run: python tests/test_reply_intents.py

Safety-critical labels (OPT_OUT, AUTO_REPLY, and never COMMIT on a refusal) must be 100%;
overall accuracy must stay >= 95%.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conversation_handlers import classify  # noqa: E402

CORPUS = {
    "AUTO_REPLY": [
        "Thank you for contacting us! Our team will respond shortly.",
        "Thank you for contacting Dr. Meera's Dental Clinic. We will get back to you shortly.",
        "Hello! We are currently closed. Our business hours are 10am-7pm.",
        "Namaste 🙏 Aapka message mil gaya hai, hum jaldi reply karenge.",
        "This is an automated response. For appointments call the front desk.",
        "Aapki jaankari ke liye bahut-bahut shukriya. Main aapki yeh sabhi baatein aur sujhaav hamari team tak pahuncha deti hoon.",
        "Aapki madad ke liye shukriya, lekin main ek automated assistant hoon",
        "Thanks for reaching out! We'll connect with you soon.",
        "I am out of office till Monday.",
        "We have received your message and will reply shortly.",
        "आपका संदेश हमें मिल गया है, हम जल्द ही संपर्क करेंगे।",
        "Auto-reply: currently unavailable",
    ],
    "OPT_OUT": [
        "Stop messaging me. This is useless spam.", "STOP", "stop", "Stop.", "pls stop", "please stop",
        "ok stop", "Okay stop now", "just stop", "stop it", "unsubscribe", "unsubscribe me", "Not interested",
        "not interested, stop", "don't message me again", "Do not contact me", "leave me alone", "remove my number",
        "band karo ye sab", "bakwas band karo yaar", "message mat bhejo", "mat bhejo", "नहीं चाहिए, मत भेजो", "बंद करो", "please don't disturb",
        "I want to opt out",
    ],
    "ABUSE": ["You people are useless", "this is a scam", "bakwas hai ye sab", "what nonsense", "get lost", "pathetic service"],
    "OUT_OF_SCOPE": ["Can you also help me file my GST?", "Can you help with my income tax return?", "I need a business loan",
                     "Do you know a good CA?", "help me with insurance for my shop"],
    "BUSY": ["I'm busy right now", "In a meeting, talk later", "call me later", "with a patient", "driving", "thodi der baad"],
    "LATER": ["not now", "maybe later", "next week", "after diwali", "sure but after diwali",
              "Not interested right now but maybe next month", "abhi nahi", "baad mein dekhenge", "some other time"],
    "DECLINE": ["no", "No.", "nope", "nahi", "No thanks", "no thank you", "don't need this", "not needed", "nahi chahiye",
                "👎", "no need", "zaroorat nahi hai", "नहीं", "not for me"],
    "THANKS": ["thanks", "Thank you!", "shukriya", "thanks 🙏", "dhanyavad", "धन्यवाद"],
    "IDENTITY": ["Who are you?", "Who are you? Is this a bot?", "are you a bot", "is this a real person", "aap kaun ho",
                 "who is this"],
    "PRIVACY": ["How did you get my number?", "where did you get my number from", "who gave you my number", "number kaha se mila"],
    "EDIT": ["Can you change the message to mention evening slots?", "mention the Sunday timing too",
             "use ₹249 instead", "change the price to 249", "add my phone number to the post", "remove the discount from the post"],
    "COMMIT": [
        "Ok lets do it. Whats next?", "ok let's do it", "Yes", "yes please", "YES", "haan", "haan kar do", "ha bhej do", "go ahead",
        "Sure, go ahead", "sounds good", "ok", "k", "👍", "Yes, send me the abstract", "send it", "please send", "chalega",
        "theek hai", "done", "confirm", "CONFIRM", "Yes stop the old offer and run the new one", "No, go ahead",
        "हाँ, भेज दीजिए", "हाँ", "ठीक है", "जी", "1", "2", "yes book Wed", "renew", "I want to join", "perfect", "great, thanks",
    ],
    "DELEGATE": ["my son handles this, talk to him", "my manager looks after this", "he handles this"],
    "ALREADY_DONE": ["I already did this last week", "already posted", "already renewed", "pehle hi kar diya"],
    "CHANNEL": ["Can you call me instead?", "call me", "email me the details"],
    "RELEVANCE": ["Is it relevant for my patients?", "how does this help me", "why should I care about this"],
    "PRICE": ["What's the price?", "how much will it cost", "Sounds interesting but what does it cost me", "kitna lagega?", "is it free?"],
    "TIME": ["When is it?", "what time?", "kab hai?"],
    "QUESTION": ["What is this about exactly?", "What exactly did the study find?", "tell me more", "kya hai ye?", "explain please"],
    "HEDGE": ["maybe", "not sure", "let me think", "hmm", "dekhte hain"],
}

REFUSALS = {"OPT_OUT", "DECLINE", "LATER", "BUSY", "ABUSE"}


def main() -> int:
    total = correct = 0
    critical_fail = []
    per = {}
    for want, msgs in CORPUS.items():
        for msg in msgs:
            got = classify(msg, [{"from": "merchant", "msg": msg}])
            total += 1
            ok = got == want
            correct += ok
            per.setdefault(want, [0, 0])
            per[want][0] += ok
            per[want][1] += 1
            if not ok:
                print(f"  MISS  want={want:12} got={got:12} | {msg[:80]}")
            if want in ("OPT_OUT", "AUTO_REPLY") and not ok:
                critical_fail.append((msg, want, got))
            if want in REFUSALS and got == "COMMIT":
                critical_fail.append((msg, want, got))
    acc = correct / total
    print("\nper-intent:", ", ".join(f"{k} {a}/{n}" for k, (a, n) in per.items()))
    print(f"\nRESULT: {correct}/{total} = {acc:.1%}   critical failures: {len(critical_fail)}")
    return 0 if (acc >= 0.95 and not critical_fail) else 1


if __name__ == "__main__":
    sys.exit(main())
