"""Second blind held-out set, written after the generalising rules existed and never used to tune them.
Run: python tests/held_out_intents_2.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from held_out_intents import REFUSALS  # noqa: E402
from conversation_handlers import classify  # noqa: E402

HELD_OUT_2 = [
    ("Hello, you have reached Glamour Lounge. We will call you back.", {"AUTO_REPLY"}),
    ("Thanks for writing to Pizza Junction! Our staff will reply during working hours.", {"AUTO_REPLY"}),
    ("Currently unavailable. Will respond when back.", {"AUTO_REPLY"}),
    ("kindly do not send such messages", {"OPT_OUT"}),
    ("I said stop", {"OPT_OUT"}),
    ("enough with these texts", {"OPT_OUT"}),
    ("aap log message karna band karo", {"OPT_OUT"}),
    ("please remove my number from your list", {"OPT_OUT"}),
    ("not required thanks", {"DECLINE"}),
    ("no we are good", {"DECLINE"}),
    ("I'll pass", {"DECLINE", "HEDGE", "OTHER"}),
    ("we are not looking for this", {"DECLINE", "HEDGE"}),
    ("nahi bhai rehne do", {"DECLINE"}),
    ("after 15th we can talk", {"LATER"}),
    ("remind me on friday", {"LATER"}),
    ("not today", {"LATER", "DECLINE"}),
    ("busy with diwali rush, later", {"BUSY", "LATER"}),
    ("on leave this week", {"LATER", "BUSY", "OTHER"}),
    ("yes do it", {"COMMIT"}),
    ("go on", {"COMMIT", "OTHER"}),
    ("done, send", {"COMMIT"}),
    ("okay fine", {"COMMIT"}),
    ("theek hai bhej dijiye", {"COMMIT"}),
    ("haan chalo", {"COMMIT"}),
    ("sure, please share the abstract", {"COMMIT"}),
    ("yes, and add the fluoride offer in the post", {"EDIT", "COMMIT"}),
    ("what will it cost me?", {"PRICE"}),
    ("charges kitne hain?", {"PRICE"}),
    ("is there any fee", {"PRICE"}),
    ("what exactly is this recall about?", {"QUESTION"}),
    ("how does verification work", {"QUESTION"}),
    ("which batches?", {"QUESTION"}),
    ("is this vera from magicpin?", {"IDENTITY", "QUESTION"}),
    ("who am I talking to", {"IDENTITY", "QUESTION"}),
    ("how do you have my number", {"PRIVACY", "QUESTION"}),
    ("please talk to my manager Ravi", {"DELEGATE"}),
    ("already verified last month", {"ALREADY_DONE"}),
    ("we did this already", {"ALREADY_DONE"}),
    ("not sure if it's worth it", {"HEDGE", "DECLINE"}),
    ("let me discuss with my partner", {"HEDGE", "DELEGATE", "LATER"}),
    ("call karo", {"CHANNEL", "OTHER"}),
    ("change 'free' to '₹99' in the post", {"EDIT"}),
    ("can you help me with my shop's electricity bill", {"OUT_OF_SCOPE", "QUESTION"}),
    ("tell me cricket score", {"OUT_OF_SCOPE", "QUESTION"}),
    ("thanks a lot", {"THANKS"}),
    ("this is rubbish", {"ABUSE", "OTHER", "HEDGE"}),
    ("stop wasting my time", {"OPT_OUT", "ABUSE"}),
    ("हाँ भेजिए", {"COMMIT"}),
    ("मुझे नहीं चाहिए", {"DECLINE", "OPT_OUT"}),
    ("कल बात करेंगे", {"LATER", "BUSY"}),
    ("कितना खर्चा आएगा?", {"PRICE", "QUESTION"}),
]


def main() -> int:
    ok, critical = 0, []
    for msg, want in HELD_OUT_2:
        got = classify(msg, [{"from": "merchant", "msg": msg}])
        hit = got in want
        ok += hit
        if not hit:
            print(f"  MISS  want={'/'.join(sorted(want)):24} got={got:12} | {msg}")
        if (want & REFUSALS and got == "COMMIT") or ("OPT_OUT" in want and got not in ("OPT_OUT", "DECLINE", "ABUSE")) \
                or (want == {"AUTO_REPLY"} and not hit):
            critical.append(msg)
    print(f"\nRESULT: blind held-out #2 accuracy {ok}/{len(HELD_OUT_2)} = {ok / len(HELD_OUT_2):.1%}   safety-critical misses: {len(critical)}")
    for c in critical:
        print("   CRITICAL:", c)
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
