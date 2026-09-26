# Vera — magicpin AI Challenge submission

[![CI](https://github.com/NirvanJha/magicpin-vera-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/NirvanJha/magicpin-vera-bot/actions/workflows/ci.yml)
**Live bot:** `https://magicpin-vera-bot-vtz2.onrender.com/v1/*` · deliverables: [`bot.py`](bot.py) · [`submission.jsonl`](submission.jsonl) · [`conversation_handlers.py`](conversation_handlers.py) · full design notes: [`docs/DESIGN.md`](docs/DESIGN.md)

## Approach

**One rule above all: every fact in a message must come from pushed context.** `compose()` ([`composer.py`](composer.py)) routes each of the 26 trigger kinds to a dedicated composer. Each composer reads names, numbers, prices, dates and sources only from the category, merchant, trigger and customer contexts. If a value is missing, the sentence that needed it is dropped; nothing is filled in with a default. Voice follows the category, messages are in Hinglish when the recipient speaks Hindi, and each ends with a single CTA. A final validator refuses to send any message containing raw data artefacts (`None`, `{`, `1e+308`). An audit of all 100 dataset triggers found **zero invented facts**. The only numbers not copied from context are computed values, such as days until a deadline and "suggested" bulk-price tiers derived from the merchant's own price.

**The server** ([`bot.py`](bot.py)) follows the testing brief's rules:
- **Tick:** one message per recipient, `suppression_key` never reused, ordered by urgency, capped at 20.
- **Context:** versioned updates (409 for an older version).
- **Opt-outs:** STOP from a merchant is honoured for 30 days, and the bot keeps to its own earlier wait/end decisions.
- **Teardown:** `/v1/teardown` wipes all state.

It never returns a 500, and every trigger is processed in isolation.

**Replies** ([`conversation_handlers.py`](conversation_handlers.py)) are labelled by a 22-intent classifier. The safety checks come first: auto-reply (nudge once, wait 24h, then end), opt-out, abuse, and off-topic asks such as GST (declined, then back to the original topic). After those, a commitment gets the actual deliverable straight away, with no further qualifying questions. Price/timing questions, "who are you?", "how did you get my number?", edits and hand-offs are answered from the conversation's own trigger. It never repeats itself within a conversation.

## Tradeoffs

- **Templates instead of an LLM at runtime.** Messages are fast (median about 1 ms), deterministic, and can't invent facts, at the cost of less varied wording. The official `judge_simulator.py`, scoring with a local LLM, gave every one of the 19 messages it scored between 41 and 45 out of 50 (average 80%).
- **Rules instead of a model for replies.** Easy to explain and to test. The classifier scores 100% on its 166-message training set, but **90% on a blind held-out set**, with 0 safety-critical misses. Unclear negatives default to a no-pressure reply, never a pitch.
- **Conservative by default.** A STOP from a merchant also pauses messages sent to their customers on their behalf. Customers who explicitly opted out are skipped.
- **Memory-only state.** This follows the brief ("must not persist context after the test"). Restart resilience is available as an opt-in (`VERA_STATE_FILE`), and `/v1/teardown` wipes it.

## What additional context would have helped most

1. **Consent scope matched to trigger kinds.** Most dataset customers only consent to `promotional_offers`, which makes recalls a grey area.
2. **Real appointment times and open slots** for `appointment_tomorrow` and `trial_followup` triggers. Many trigger payloads are placeholders.
3. **Review text and counts.** 5 of the 6 `review_theme_emerged` triggers carry no theme at all.
4. **What Vera said before, per merchant.** Fuller `conversation_history` would let the bot avoid repeating a topic across sessions.

## Verify it yourself

```bash
pip install -r requirements.txt && uvicorn bot:app --port 8080
python tests/run_all.py            # boots its own bot, runs every suite below
```

The suites cover the §7 contract, the full judge lifecycle (48 checks), a seeded simulated-judge soak with 8 merchant personas, a structural fuzzer, restart and teardown, and intent accuracy. CI runs all of them on each push, on bare Python and against the Docker image. Details are in [`docs/DESIGN.md`](docs/DESIGN.md#tests).
