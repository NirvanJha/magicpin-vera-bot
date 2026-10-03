# Score re-test and improvement list (2026-10-03)

## How it was tested

- `judge_simulator.py`, scenario `full_evaluation`, judged by local Ollama `gemma3` (4B), the same setup as the earlier 80% run.
- Bot v2.1.4 running locally. The live Render bot is also v2.1.4 but already holds contexts at version 52395, so the judge's `version: 1` pushes are rejected with 409. That is correct per the spec; it just means a fresh judge run needs a `/v1/teardown` first.
- `tests/run_all.py`: **22/22 suites pass**.
- The behaviour scenarios (auto-reply, intent switch to action, hostile → end) all pass, both live and local.

## Scores (19 messages scored)

| Dimension | Avg /10 |
|---|---|
| Merchant fit | 9.32 |
| Specificity | 8.74 |
| Category fit | 8.74 |
| Decision quality | 8.05 |
| **Engagement** | **7.68** ← weakest |
| **Total** | **42.5 / 50 (85%)**, 0 penalties |

The judge's summary prints **40/50 (80%)** because it rounds each dimension down before adding them. The real mean is 42.5.

Range: 40 to 45. Lowest was `dormant_with_vera` (40). Highest was `recall_due` (45).

Two bugs in the harness itself (not in the bot):
1. `DatasetLoader` calls `open()` with no encoding. On Windows, `₹` and `—` become `â‚¹` / `â€”` before they reach the bot, and the LLM then scores the garbled text. Run it with `PYTHONUTF8=1`. Scores barely moved (42.47 → 42.53), but the earlier logs were affected.
2. The `all` scenario never scores any messages. Use `full_evaluation`.

## What to improve, highest impact first

### 1. Fix the Hinglish CTA tail (engagement + category fit)
`composer.py:282` `ask()` returns `"Main … bhej doon? Reply YES."` whenever `self.hi` is true. Every merchant in the dataset lists `hi`, so **15 of 19 messages end with the same Hindi tag on an otherwise English body**. The judge called it "awkward", "abrupt" or "a direct translation" in several reasons, and it pulls engagement down to about 7 every time.
- Use the Hinglish ask only when the body itself is Hinglish, or when `hi` is the merchant's *first* language. Otherwise use English.
- Vary the wording by trigger kind so 15 messages don't share one closing line.

### 2. Say what the merchant gets in the CTA (engagement)
The most common hint was "add a benefit / quantify the impact". The ask names the deliverable ("2 Google posts") but not the outcome. Add one grounded clause before the ask, using only numbers already in context:
- perf_dip: "…to win back some of the 6 lost calls/week" (12 baseline × 50%).
- winback_eligible: "…aimed at the 24 lapsed customers."
- gbp_unverified: "…the ~30% lift starts once you're verified."
- Mention the effort: "takes 2 minutes to approve".

### 3. Use urgency where the trigger provides it (decision quality)
The judge noted that urgency was "not fully leveraged" for `competitor_opened`, `gbp_unverified`, `regulation_change` and `active_planning_intent`.
- competitor_opened: "they launched 8 Apr; first-week searchers are deciding now."
- regulation_change: say what non-compliance costs, using only facts from the circular payload.
- Scale the time pressure by `trigger.urgency`, firmer at urgency ≥ 3.

### 4. Fill in the `active_planning_intent` drafts (specificity + decision quality)
`trg_013` (thali) and `trg_016` (kids yoga) both got a blank scaffold ("Who: age band / segment it's for", "Format: sessions per week…"). That reads as a form, not a draft. Fill each field from context:
- kids yoga: segment from the customers' `child_7-12`, the price from the merchant's active offer ("First Month @ ₹499"), and a trial built on the existing trial offer.
- corporate thali: suggested tiers derived from the merchant's own thali price (which the composer already does for bulk pricing elsewhere).
- Keep "(edit anything)", but give something concrete to edit.

### 5. Rework `dormant_with_vera` (lowest score, 40)
- "since we last spoke (about subscription expiry)": drop the parenthetical, or rephrase it as "since your plan lapsed".
- "(magicpin internal, Apr 2026)" was flagged as looking like a placeholder source. Cite it the way the IPL message does ("magicpin order data, Apr 2026"), or leave the source out.
- Tie the 23% tip to this merchant ("you're not using the 'Walk-in available' tag yet") if the context supports it.

### 6. Personalise customer messages more (decision quality ~8)
- trial_followup: add the child's age band or goal ("for Karthik's age group, 7–12").
- customer_lapsed_hard: connect the offer to the goal ("3 free classes to restart your weight-loss plan").

### 7. Reword the clumsy sentences seen in unsent triggers
Six triggers weren't sent in the judge run. That's correct: the bot sends one message per recipient per tick. They will be sent on later ticks, so check them too:
- research_digest: "2,100-patient trial: Multi-center Indian trial shows…" repeats "trial". Merge it into one clause.
- renewal_due: shows "4 calls" as "what it delivered". Weak numbers make a poor renewal pitch. When calls or CTR are below peers, lead with views or direction requests, or frame it as "renew + let's fix the CTR".

### 8. Re-test against a stronger judge
gemma3-4B misreads things. For example, it praised mentions of a locality that wasn't in the message and called an English message Hindi. The real grader is probably a larger model. Re-run `full_evaluation` with a stronger model (OpenAI/Groq keys are configured in `GenAI/.env`) before trusting changes of ±1 point.

## Status (applied in `composer.py`, not yet deployed)

- Done: #1 (Hinglish only when Hindi is the merchant's first language, plus a "nothing goes live without your OK" reassurance on drafts), #2 (a context-only payoff clause on every ask), #3 (urgency for compliance, competitor price gap, Google verification, IPL), #4 (filled planning drafts), #5 (dormant rewrite), #6 (trial social proof from the merchant's own trial-to-paid rate; lapsed offer tied to the customer's goal), #7 (research trial wording, renewal reframed around the CTR gap), and a win-back scale line from `lapsed_90d_plus`.
- Not done: "quantify revenue". The dataset has no ticket-size or revenue fields, so any ₹ projection would be invented.
- Result (gemma3 judge): one full `full_evaluation` run went 42.5 → 43.2 /50, with engagement 7.68 → 8.32. Partial A/B with 3 repeats per message (4 triggers): +0.4 per message, 0 penalties. Judge noise is about ±2 per message.
- 22/22 test suites pass. `submission.jsonl` was regenerated.

## Re-run recipe
```bash
# local bot
uvicorn bot:app --port 8099
# set BOT_URL=http://127.0.0.1:8099, LLM_PROVIDER=ollama, LLM_MODEL=gemma3:latest, TEST_SCENARIO=full_evaluation
PYTHONUTF8=1 python judge_simulator.py
```
