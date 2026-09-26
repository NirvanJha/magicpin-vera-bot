"""Held-out reply set: written AFTER the classifier and never used to tune it.

Reports honest generalisation accuracy. Only safety-critical misses fail the run:
  * a refusal (opt-out / decline / later / busy / abuse) classified as COMMIT
  * an opt-out not recognised as OPT_OUT or DECLINE (both end the conversation)
  * an auto-reply not recognised
Run: python tests/held_out_intents.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from conversation_handlers import classify  # noqa: E402

HELD_OUT = [
    # (message, acceptable intents)
    ("Hi, thanks for your message! We're closed on Sundays, we'll revert Monday.", {"AUTO_REPLY"}),
    ("Greetings from Sunrise Medicos. Our executive will contact you soon.", {"AUTO_REPLY"}),
    ("We are away at the moment. Please leave a message.", {"AUTO_REPLY"}),
    ("Thank you for contacting us. Your query is important to us.", {"AUTO_REPLY"}),
    ("please stop sending these", {"OPT_OUT"}),
    ("Stop spamming", {"OPT_OUT", "ABUSE"}),
    ("I don't want these messages", {"OPT_OUT", "DECLINE"}),
    ("remove me from this list", {"OPT_OUT"}),
    ("mujhe ye messages nahi chahiye", {"OPT_OUT", "DECLINE"}),
    ("kripya message na bheje", {"OPT_OUT", "DECLINE"}),
    ("NO. NOT INTERESTED.", {"OPT_OUT", "DECLINE"}),
    ("nah not for us", {"DECLINE"}),
    ("we don't need any of this", {"DECLINE", "OPT_OUT"}),
    ("abhi zarurat nahi hai", {"DECLINE", "LATER"}),
    ("maybe after the wedding season", {"LATER", "HEDGE"}),
    ("ping me next monday", {"LATER"}),
    ("can we do this tomorrow", {"LATER", "TIME", "QUESTION"}),
    ("I'm with a customer, give me 10 mins", {"BUSY"}),
    ("clinic is full right now, will check later", {"BUSY", "LATER"}),
    ("haan ji kar dijiye", {"COMMIT"}),
    ("yes go for it", {"COMMIT"}),
    ("alright, send the draft", {"COMMIT"}),
    ("sure thing", {"COMMIT"}),
    ("okk", {"COMMIT"}),
    ("yess", {"COMMIT"}),
    ("👍👍", {"COMMIT"}),
    ("Let's go with it", {"COMMIT"}),
    ("book the wednesday one", {"COMMIT", "TIME"}),
    ("ok but what does it cost?", {"PRICE"}),
    ("what's the fee for this?", {"PRICE"}),
    ("how much do I have to pay", {"PRICE"}),
    ("what is DCI?", {"QUESTION"}),
    ("send more details", {"COMMIT", "QUESTION"}),
    ("who's this?", {"IDENTITY"}),
    ("am I talking to a human?", {"IDENTITY", "QUESTION"}),
    ("where did you find my contact", {"PRIVACY", "QUESTION"}),
    ("my brother runs the shop, ask him", {"DELEGATE"}),
    ("already done this myself", {"ALREADY_DONE"}),
    ("I will think about it", {"HEDGE"}),
    ("hmm let me see", {"HEDGE"}),
    ("can you phone me", {"CHANNEL"}),
    ("make it 20% off instead", {"EDIT"}),
    ("add our Sunday hours to the post", {"EDIT"}),
    ("can you help me register my company?", {"OUT_OF_SCOPE", "QUESTION"}),
    ("what's the tax on this", {"OUT_OF_SCOPE", "PRICE"}),
    ("thank you so much", {"THANKS"}),
    ("tysm", {"THANKS", "OTHER"}),
    ("you guys are a fraud", {"ABUSE"}),
    ("fed up of your messages", {"OPT_OUT", "ABUSE", "DECLINE"}),
    ("haan theek hai, bhej do", {"COMMIT"}),
    ("नहीं भाई", {"DECLINE"}),
    ("अभी व्यस्त हूँ", {"BUSY"}),
    ("हाँ जी, कर दो", {"COMMIT"}),
    ("बाद में बात करते हैं", {"LATER", "BUSY"}),
]
REFUSALS = {"OPT_OUT", "DECLINE", "LATER", "BUSY", "ABUSE"}


def main() -> int:
    ok = 0
    critical = []
    for msg, want in HELD_OUT:
        got = classify(msg, [{"from": "merchant", "msg": msg}])
        hit = got in want
        ok += hit
        if not hit:
            print(f"  MISS  want={'/'.join(sorted(want)):22} got={got:12} | {msg}")
        if want & REFUSALS and got == "COMMIT":
            critical.append(msg)
        if "OPT_OUT" in want and got not in ("OPT_OUT", "DECLINE", "ABUSE"):
            critical.append(msg)
        if want == {"AUTO_REPLY"} and not hit:
            critical.append(msg)
    print(f"\nRESULT: held-out accuracy {ok}/{len(HELD_OUT)} = {ok / len(HELD_OUT):.1%}   safety-critical misses: {len(critical)}")
    for c in critical:
        print("   CRITICAL:", c)
    return 1 if critical else 0


if __name__ == "__main__":
    sys.exit(main())
