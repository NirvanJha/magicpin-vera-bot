# magicpin AI Challenge — Complete Codebase Analysis & Requirements Guide

This comprehensive reference document outlines the current state of the repository, explains every file and directory in detail, and defines the exact requirements, technical contracts, and deliverables required to complete the challenge.

---

## Table of Contents
1. [Challenge Overview & The Problem Statement](#1-challenge-overview--the-problem-statement)
2. [Current Codebase File Structure & Asset Inventory](#2-current-codebase-file-structure--asset-inventory)
3. [The 4-Context Composition Framework](#3-the-4-context-composition-framework)
4. [What Needs to Be Done (Required Deliverables)](#4-what-needs-to-be-done-required-deliverables)
5. [Evaluation Methodology & Scoring Rubric](#5-evaluation-methodology--scoring-rubric)
6. [Interactive Replay Scenarios & Behavioral Edge Cases](#6-interactive-replay-scenarios--behavioral-edge-cases)
7. [Decisions & Next Steps (Pending Finalization)](#7-decisions--next-steps-pending-finalization)

---

## 1. Challenge Overview & The Problem Statement

### 1.1 Context
**magicpin** operates India's largest local-commerce network, connecting over 100,000 merchant partners (restaurants, salons, gyms, dental clinics, pharmacies, fashion, etc.) across 50+ Indian cities. 

magicpin built **Vera**, an automated AI assistant that communicates with merchants and their end customers over **WhatsApp**. Vera:
- Reaches out to 6,000–10,000 merchants daily.
- Assists merchants in optimizing their Google Business Profiles (GBP) (photos, posts, reviews, operating hours).
- Proactively recommends pricing strategies, marketing campaigns (festivals, sports events, weather changes).
- Communicates with end customers on behalf of merchants (service recall reminders, booking inquiries, chronic refill notifications).

### 1.2 The Production Problems to Solve
Production Vera currently suffers from four major weaknesses that submissions must overcome:
1. **Auto-Reply Pollution**: 40%–70% of inbound merchant messages are canned WhatsApp Business automated replies (*"Thank you for contacting us, we will respond shortly"*). Vera repeatedly burns 2–3 conversational turns attempting to talk to the auto-responder. **Required: Rapid detection of canned auto-replies and graceful conversation termination.**
2. **Intent-Handoff Failure**: When an engaged merchant signals clear buying/action intent (*"I want to join"* / *"Ok let's do it"*), Vera frequently regresses to qualifying questions (*"Would you like 10-15 customers?"*) rather than initiating the action. **Required: Immediate transition from pitch/qualification mode to execution/confirmation mode.**
3. **Generic Promotional Copy**: Vague offers (*"Flat 20% off"* or *"Grow your sales"*) fail with Indian local merchants. **Required: Service + price specificity (*"Haircut @ ₹99"*, *"Dental Cleaning @ ₹299"*), verified local data, and cited authorities.**
4. **Low Engagement Frequency**: Reminder-only nudges (*"Update your profile"*, *"Subscription expiring"*) feel repetitive. **Required: A diversified conversation portfolio driven by curiosity, clinical/industry research digests, social proof, and local events (IPL matches, heatwaves, festivals).**

---

## 2. Current Codebase File Structure & Asset Inventory

Here is the exhaustive inventory of all files present in the repository and their exact purpose:

```
magicpin-ai-challenge/
├── challenge-brief.md              # The core master brief: problem, 4 contexts, rubric, benchmarks
├── challenge-testing-brief.md      # The evaluation API contract: endpoints, schemas, timeouts
├── engagement-design.md            # Technical design specification for the 4-context architecture
├── engagement-research.md          # Internal audit of production Vera's MCP servers & data pipelines
├── judge_simulator.py              # LLM-powered test harness that acts as judge, merchant, & scorer
│
├── dataset/                        # Base data layer
│   ├── categories/                 # CategoryContext files for all 5 target business verticals
│   │   ├── dentists.json           # Clinical peer voice, JIDA digest, strict taboo guidelines
│   │   ├── gyms.json               # Coach/encouraging voice, seasonal lull metrics, fitness catalog
│   │   ├── pharmacies.json         # Precise/trustworthy voice, CDSCO compliance, molecule catalog
│   │   ├── restaurants.json        # Operator voice, food cost & cover benchmarks, delivery catalog
│   │   └── salons.json             # Consultative/warm voice, wedding season beats, beauty catalog
│   ├── merchants_seed.json         # 10 rich merchant seed profiles (2 per vertical)
│   ├── customers_seed.json         # 15 customer seed profiles with visit history & preferences
│   ├── triggers_seed.json          # 25 trigger seed events (internal metrics & external news)
│   └── generate_dataset.py         # Deterministic script to expand seeds into full benchmark set
│
├── examples/                       # Reference materials & golden baselines
│   ├── api-call-examples.md        # Exact HTTP curl and JSON payloads for every test phase
│   └── case-studies.md             # 10 gold-standard (50/50 scored) examples across all categories
│
└── CHALLENGE_GUIDE_AND_REQUIREMENTS.md  # THIS FILE: Master reference & requirements specification
```

### Detailed Breakdown of Every File

| File / Folder | Lines / Size | Core Purpose & Contents |
| :--- | :--- | :--- |
| `challenge-brief.md` | 545 lines | Master specification. Explains Vera, the 4-context inputs, the `compose()` Python contract, the 5-dimension rubric (0–50), compulsion levers, anti-patterns, and golden message examples. |
| `challenge-testing-brief.md` | 558 lines | Technical testing specification. Defines the required HTTP API (`/v1/healthz`, `/v1/metadata`, `/v1/context`, `/v1/tick`, `/v1/reply`), rate limits, phase lifecycle (warmup, tick tests, context injection, replays), and error penalties. |
| `engagement-design.md` | 326 lines | Engineering design for context separation. Explains why functional nudges have low frequency and how Category, Merchant, Trigger, and Customer contexts feed into a single LLM composer. |
| `engagement-research.md` | 199 lines | Deep dive into production Vera internals (`vera-mcp`, `merchant-support-mcp`, Redis caching, and aryan API mapping). Documents what exists vs. what this challenge creates. |
| `judge_simulator.py` | 963 lines | The offline and online evaluator. Supports multiple LLM providers (OpenAI, Anthropic, Gemini, Groq, Ollama) and simulates warmup, tick evaluation, auto-reply looping, intent handoffs, and hostile merchant behavior. |
| `dataset/categories/*.json` | 5 files | Vertical knowledge packs containing `slug`, `display_name`, `voice` (tone, register, code-mix, allowed vocab, taboo vocab, salutations), `offer_catalog`, `peer_stats`, `digest` (research papers, compliance notices), `patient_content_library`, `seasonal_beats`, and `trend_signals`. |
| `dataset/merchants_seed.json` | 315 lines | 10 fully populated merchants containing `identity` (locality, city, languages, owner name), `subscription`, `performance` (views, calls, CTR, 7d deltas), active/expired `offers`, `conversation_history`, `customer_aggregate`, `signals`, and `review_themes`. |
| `dataset/customers_seed.json` | 15 profiles | Customers linked to merchants. Details `identity`, `relationship` (first visit, last visit, lifetime value, services received), `state` (active, lapsed_soft, lapsed_hard), `preferences`, and `consent`. |
| `dataset/triggers_seed.json` | 25 triggers | Events driving outbound sends: `research_digest`, `perf_dip`, `perf_spike`, `competitor_opened`, `festival_upcoming`, `recall_due`, `curious_ask_due`, etc. |
| `dataset/generate_dataset.py` | 313 lines | Python script with fixed random seed (`20260426`). Expands seeds into: **50 merchants, 200 customers, 100 triggers, and the 30 canonical evaluation pairs (`test_pairs.json`)**. |
| `examples/api-call-examples.md`| 616 lines | Step-by-step raw HTTP request/response payloads showing how the judge interacts with the candidate bot server. |
| `examples/case-studies.md` | 339 lines | 10 annotated case studies scoring 44–50/50. Teaches exact voice matching, Hinglish mixing, Cialdini compulsion levers, and structural formatting. |

---

## 3. The 4-Context Composition Framework

Every single message produced by the bot is built from the following four context layers:

$$\mathbf{Message} = \text{compose}(\text{CategoryContext}, \text{MerchantContext}, \text{TriggerContext}, \text{CustomerContext?})$$

```
┌────────────────────────────────────────────────────────┐
│                   CategoryContext                      │
│ - Domain rules, allowed/taboo vocab, peer benchmarks   │
│ - Weekly research digest, seasonal beats, catalog      │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────┼─────────────────────────────┐
│                   MerchantContext                      │
│ - Identity (owner name, locality, languages, verified) │
│ - Performance (views, calls, CTR, 7d/30d deltas)       │
│ - Active offers, customer roster aggregates, signals   │
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────┼─────────────────────────────┐
│                    TriggerContext                      │
│ - "Why message right now?"                             │
│ - External: festival, IPL match, research digest, news │
│ - Internal: perf spike/dip, recall due, dormant, refill│
└──────────────────────────┬─────────────────────────────┘
                           │
┌──────────────────────────┴─────────────────────────────┐
│             CustomerContext (Optional)                 │
│ - Populated only for customer-facing messages          │
│ - Relationship history, preferred time slots, language │
└──────────────────────────┬─────────────────────────────┘
                           ▼
              ┌──────────────────────────┐
              │    Engagement Composer   │
              └────────────┬─────────────┘
                           ▼
                 Composed Message Object
       { body, cta, send_as, suppression_key, rationale }
```

### 3.1 Layer Responsibilities

1. **CategoryContext** (Slow-changing, shared across vertical):
   - Informs *how* to talk to this vertical.
   - Enforces vocabulary rules and **taboos** (e.g. dentists strictly forbid *"guaranteed"*, *"cure"*, *"miracle"*, or *"doctor approved"*).
   - Provides industry peer benchmarks (e.g. median CTR = 0.030).
   - Contains published research citations (`digest`) and seasonal beats.

2. **MerchantContext** (Medium-changing, specific to one merchant):
   - Identifies the business owner by name (`Meera`, `Suresh`, `Lakshmi`, `Karthik`, `Ramesh`).
   - Ground truth for performance metrics: views, calls, directions, CTR, 7-day delta percentages.
   - Lists active service offers (e.g. *"Dental Cleaning @ ₹299"*).
   - Summarizes customer base aggregates (e.g. 78 lapsed patients, 124 high-risk adults).

3. **TriggerContext** (Fast-changing, per-event):
   - Directly answers: **"Why message right now?"**
   - External triggers: Heatwaves, IPL matches, festivals, research papers, new competitor openings.
   - Internal triggers: Performance dips/spikes, milestone reached (100 reviews), customer recall due, weekly curious-ask cadence.

4. **CustomerContext** (Optional, populated when `scope == "customer"`):
   - Used when Vera sends a message *on behalf of the merchant* to one of their customers.
   - Ground truth for customer name, visit history, lapse status (`lapsed_soft`, `lapsed_hard`), preferred booking windows (weekday evening, Saturday afternoon), and language preference (`hi-en mix`).

---

## 4. What Needs to Be Done (Required Deliverables)

To complete the challenge, the following components must be built and validated:

```
Required Deliverables:
├── expanded/ (generated data)      # Dataset expansion output
│   ├── categories/                 # 5 Category JSON files
│   ├── merchants/                  # 50 Merchant JSON files
│   ├── customers/                  # 200 Customer JSON files
│   ├── triggers/                   # 100 Trigger JSON files
│   └── test_pairs.json             # 30 Canonical evaluation pairs
│
├── bot.py                          # Primary code deliverable
│   ├── def compose(...)            # Direct composition function
│   └── FastAPI Server              # 5 HTTP endpoints for judge harness
│
├── conversation_handlers.py        # Multi-turn conversational intelligence
│   ├── def respond(...)            # Turn-by-turn reply logic
│   ├── Auto-Reply Loop Breaker    # Canned reply detection & exit
│   ├── Intent Transition Router   # Pitch -> Action switcher
│   └── Hostile Handler            # De-escalation & opt-out
│
├── submission.jsonl                # 30 Composed messages for T01 - T30
│
└── README.md                       # Architectural report & trade-off analysis
```

### 4.1 Deliverable 1: Dataset Generation
- **Action**: Run `python3 dataset/generate_dataset.py --seed-dir dataset --out dataset/expanded`.
- **Output**: Generates 50 merchants, 200 customers, 100 triggers, and the 30 canonical `test_pairs.json` required for `submission.jsonl`.

### 4.2 Deliverable 2: `bot.py`
The primary module containing two required interfaces:

#### A. Standalone Python Function:
```python
def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: dict | None = None
) -> dict:
    """
    Returns:
        body: str              # WhatsApp message body
        cta: str               # "binary" (YES/STOP), "open_ended", or "none"
        send_as: str           # "vera" or "merchant_on_behalf"
        suppression_key: str   # Dedup key from trigger
        rationale: str         # Clear explanation of compulsion levers & data used
    """
```

#### B. HTTP REST Service (FastAPI):
Exposes 5 endpoints consumed by `judge_simulator.py`:
1. `GET /v1/healthz`: Returns server status, uptime, and count of loaded contexts.
2. `GET /v1/metadata`: Returns team information, model used, approach description, and version.
3. `POST /v1/context`: Idempotent ingestion of `category`, `merchant`, `customer`, and `trigger` payloads. Must handle version replacement and reject stale versions.
4. `POST /v1/tick`: Receives simulated clock tick and `available_triggers`. Evaluates conditions and returns proactive outbound `actions[]`.
5. `POST /v1/reply`: Receives inbound messages from merchants or customers and returns next action (`send`, `wait`, or `end`).

### 4.3 Deliverable 3: `conversation_handlers.py`
Handles multi-turn conversational dynamics:
- **Auto-Reply Detector**: Identifies canned business replies (frequency tracking, verbatim duplication) and terminates the conversation with `action: "end"` rather than looping.
- **Intent Switcher**: Identifies commitment signals (*"yes", "let's do it", "send me"*). Halts qualification questions and triggers immediate execution artifacts (*"Done! Here is the draft..."*).
- **Hostile / Opt-out Handler**: Identifies negative sentiment or stop requests (*"Stop messaging me"*, *"spam"*), gracefully de-escalates, and terminates with `action: "end"`.

### 4.4 Deliverable 4: `submission.jsonl`
A 30-line JSONL file mapping directly to the 30 canonical evaluation pairs in `test_pairs.json` (T01 through T30):
```json
{"test_id": "T01", "body": "...", "cta": "open_ended", "send_as": "vera", "suppression_key": "...", "rationale": "..."}
```

### 4.5 Deliverable 5: `README.md`
A concise technical report covering:
- System Architecture (Composer, Guardrails, State Machine).
- Prompt engineering design & context formatting.
- Deterministic guardrails against hallucination.
- Trade-offs made (latency vs. reasoning depth).

---

## 5. Evaluation Methodology & Scoring Rubric

Each composed message is scored across **5 Dimensions (0–10 points each, 50 points maximum)** by an LLM Judge:

```
┌────────────────────────────────────────────────────────┐
│                   TOTAL SCORE (0-50)                   │
├────────────────────────┬───────────────────────────────┤
│ Specificity (0-10)     │ Concrete numbers, dates, cite │
│ Category Fit (0-10)    │ Voice, allowed/taboo vocab    │
│ Merchant Fit (0-10)    │ Name, locality, metric match  │
│ Trigger Relevance(0-10)│ Direct "why now" connection   │
│ Engagement (0-10)      │ Cialdini levers, single CTA   │
└────────────────────────┴───────────────────────────────┘
```

### 5.1 Rubric Details

| Dimension | Max | What Judges Look For | Fatal Errors (Scored 0–3) |
| :--- | :---: | :--- | :--- |
| **Specificity** | 10 | Real numbers from contexts, verifiable research citations (*"JIDA Oct 2026, p.14"*), specific catalog prices (*"₹299"*), batch numbers. | Vague discounts (*"10% off"*), generic growth promises (*"increase footfall"*), fabricated stats. |
| **Category Fit** | 10 | Matches vertical tone (clinical peer for dentists, operator for restaurants, coach for gyms, precision for pharmacies). Taboo words excluded. | Using forbidden hype words (*"100% guaranteed"*, *"cure"*, *"miracle"*), overly casual tone with doctors. |
| **Merchant Fit** | 10 | Personalizes to owner first name (*"Dr. Meera"*, *"Suresh"*), locality (*"Lajpat Nagar"*), real performance metrics, and language mix (*"hi-en mix"*). | Addressing as *"Dear Merchant"*, ignoring merchant's active offers, sending pure English to Hindi-mix profiles. |
| **Trigger Relevance** | 10 | Explicitly communicates *why now* linked to the specific trigger (e.g. today's IPL match, 5-month cleaning window, 38% caries study). | Sending a generic profile nudge when triggered by a research digest or external event. |
| **Engagement Compulsion** | 10 | Incorporates Cialdini levers: curiosity, loss aversion, social proof, effort externalization (*"I've drafted X — takes 5 min"*), single binary CTA. | Burying the CTA, providing 3 competing calls to action, open-ended high-friction asks. |

### 5.2 Penalties & Disqualifications
- Non-200 `/healthz` (3 consecutive checks) = Disqualification (-10).
- Latency > 30 seconds per call = Timeout penalty (-1 per timeout).
- Malformed JSON responses = Zero score for the turn (-2 operational).
- Repetition of verbatim previous message = Anti-repetition penalty (-2).
- Fabricating data (unverifiable papers, fake competitors) = Score capped at 5/dimension.

---

## 6. Interactive Replay Scenarios & Behavioral Edge Cases

The judge tests multi-turn behavior using three automated challenge scenarios:

### Scenario 1: Auto-Reply Hell
- **Behavior**: The simulated merchant is running WhatsApp Business auto-replies, sending:
  > *"Thank you for contacting us! Our team will respond shortly."*
- **Success Criteria**: The bot detects the repetitive canned reply on Turn 1 or 2 and responds with `action: "end"` or a graceful polite exit. It **must not** loop 4 times.

### Scenario 2: Intent Transition (Pitch $\to$ Action)
- **Behavior**: After an initial message, the merchant says:
  > *"Ok lets do it. Whats next?"*
- **Success Criteria**: 
  - **Pass**: Bot immediately confirms and presents the completed artifact or execution step (*"Done! Here is the draft..."* or *"Proceeding now..."*).
  - **Fail**: Bot asks further qualification questions (*"Would you like to get more customers?"*).

### Scenario 3: Hostile / Opt-Out Handling
- **Behavior**: The merchant replies aggressively:
  > *"Stop messaging me. This is useless spam."*
- **Success Criteria**: Bot recognizes the unsubscribe / hostile intent, de-escalates with an apology, and terminates the conversation with `action: "end"`.

---

## 7. Strict Problem Statement Scope & Deliverables

As defined strictly in **Section 7 of `challenge-brief.md`** and the test harness specification in **`challenge-testing-brief.md`**, our exact scope consists of:

### 1. `bot.py` (Core Deliverable)
- **`compose(category: dict, merchant: dict, trigger: dict, customer: dict | None) -> dict`**:
  - Deterministic function returning: `body`, `cta`, `send_as`, `suppression_key`, and `rationale`.
  - Responds within the < 30s budget per call.
- **FastAPI Service (5 endpoints)**:
  - `GET /v1/healthz`: Liveness probe and context counter.
  - `GET /v1/metadata`: Team and model metadata.
  - `POST /v1/context`: Idempotent ingestion for `(scope, context_id, version)`.
  - `POST /v1/tick`: Receives active triggers, calls `compose`, returns `actions[]`.
  - `POST /v1/reply`: Receives inbound messages, returns `action` (`send`, `wait`, `end`).

### 2. `conversation_handlers.py` (Multi-Turn Tiebreaker)
- **`respond(state, merchant_message: str) -> dict`**:
  - Detects canned auto-replies $\to$ exits gracefully (`action: "end"`).
  - Detects merchant buying/action intent $\to$ transitions immediately to action mode.
  - Detects hostility/stop requests $\to$ de-escalates and ends conversation.

### 3. `dataset/generate_dataset.py` (Benchmark Generation)
- Run `generate_dataset.py` to create the 30 canonical evaluation pairs (`test_pairs.json`).

### 4. `submission.jsonl` (Official Benchmark Output)
- 30 lines (one per test pair T01–T30) containing `{test_id, body, cta, send_as, suppression_key, rationale}`.

### 5. `README.md` (Max 1 Page)
- Brief summary of approach, trade-offs made, and additional context that would have helped.

---

## 8. Exact Execution Steps

1. **Step 1**: Run `dataset/generate_dataset.py` to produce the official `test_pairs.json`.
2. **Step 2**: Implement `bot.py` (`compose()` + FastAPI server) and `conversation_handlers.py`.
3. **Step 3**: Validate locally using `judge_simulator.py`.
4. **Step 4**: Generate `submission.jsonl` for all 30 test pairs.
5. **Step 5**: Write `README.md` (1 page max).

