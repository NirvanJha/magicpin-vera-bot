# Vera — magicpin AI Challenge submission

[![CI](https://github.com/NirvanJha/magicpin-vera-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/NirvanJha/magicpin-vera-bot/actions/workflows/ci.yml)

**Live bot:** `https://magicpin-vera-bot-vtz2.onrender.com/v1/*` · **Deliverables:** [`bot.py`](bot.py) · [`submission.jsonl`](submission.jsonl) · [`conversation_handlers.py`](conversation_handlers.py) · **Design notes:** [`docs/DESIGN.md`](docs/DESIGN.md)

## Results

| Measure | Result |
|---|---|
| Official `judge_simulator.py` (local LLM judge), 19 messages scored | **43.2 / 50 (86%)**, every message 41–45, **0 fabrication penalties** |
| Judge dimensions (out of 10) | Merchant fit 9.4 · Category fit 8.8 · Specificity 8.7 · Engagement 8.3 · Decision quality 8.0 |
| Judge behaviour scenarios | **4 / 4 pass** |
| Invented facts, audit of all 100 dataset triggers | **0** |
| Test suites (CI on every push, bare Python + Docker) | **22 / 22 pass** |
| Judge lifecycle end-to-end | 49 / 49 checks |
| Reply intent accuracy | 100% tuning (166) · 100% held-out (54) · **90% blind held-out (51)** · 0 safety-critical misses |
| Robustness | 9,000 fuzzed payloads: 0 failures, 0 leaked artefacts · 10 simulated-judge soaks: 0 violations · never returns a 500 |
| Originality vs the 10 official case studies | max similarity 0.41, 0 near-duplicates |
| Latency | median about 1 ms per message (no LLM at runtime) |
| Longest uptime | 5.7 days on Render, no restart |

The judge's summary prints 40/50 because it rounds each dimension down; 43.2 is the actual mean.

## Approach

**Every fact in a message comes from pushed context.** `compose()` ([`composer.py`](composer.py)) routes each of 26 trigger kinds to a dedicated composer. Names, numbers, prices, dates and sources are read only from the category, merchant, trigger and customer contexts. A missing value drops its sentence; nothing is defaulted. The only numbers not copied verbatim are computed from context: days to a deadline, a competitor's price gap, calls lost against baseline, and bulk-price tiers derived from the merchant's own price. A final validator blocks any raw data artefact.

**Every message is built to get a reply.** It gives the reason for messaging now, then the merchant's own numbers, plus a peer benchmark where one exists. It ends with one binary CTA that states the payoff ("to win back the ~6 calls/week you've lost") and, for drafts, confirms nothing goes live without approval. Voice follows the category, and messages switch to Hinglish when that is the recipient's language.

**The server** ([`bot.py`](bot.py)) implements the testing brief:
- one message per recipient per tick, ordered by urgency, `suppression_key` never reused;
- versioned context with 409 on stale versions;
- a 30-day STOP honour that also covers the merchant's customers;
- `/v1/teardown` to wipe all state.

**Replies** ([`conversation_handlers.py`](conversation_handlers.py)) go through a 22-intent classifier. Safety intents come first: auto-reply (nudge, wait 24h, end), opt-out, abuse, and off-topic asks. A commitment gets the deliverable immediately, and the bot never repeats itself.

## Tradeoffs

- **Templates over a runtime LLM:** deterministic, fast and unable to hallucinate; wording varies less.
- **Rules over a model for replies:** fully explainable and testable. Blind accuracy is 90%, and unclear negatives default to a no-pressure reply, never a pitch.
- **Conservative consent:** a merchant's STOP also pauses messages to their customers, and opted-out customers are skipped.
- **Memory-only state,** per the brief; opt-in restart persistence (`VERA_STATE_FILE`) is wiped by teardown.

## What additional context would have helped most

1. **Consent scope per trigger kind.** Most customers consent only to `promotional_offers`, leaving recalls a grey area.
2. **Real appointment times and open slots.** Many `appointment_tomorrow` and `trial_followup` payloads are placeholders.
3. **Review text and counts.** 5 of the 6 `review_theme_emerged` triggers carry no theme.
4. **Average ticket size.** With it, the bot could quantify the revenue at stake, the judge's most frequent suggestion, without inventing numbers.

## Verify it yourself

```bash
pip install -r requirements.txt && uvicorn bot:app --port 8080
python tests/run_all.py            # boots its own bot and runs all 22 suites
# manual: http://127.0.0.1:8080/tester  ·  Postman: postman/Vera.postman_collection.json
```

Judge re-test method and improvement log: [`docs/SCORE_IMPROVEMENTS.md`](docs/SCORE_IMPROVEMENTS.md).
