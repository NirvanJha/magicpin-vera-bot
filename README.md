# magicpin AI Challenge — Vera Merchant Assistant Submission

## 1. Overview & Architecture

This submission implements **Vera 2.0**, an AI engagement assistant for magicpin's network of local merchants across 5 core verticals (dentists, salons, gyms, restaurants, pharmacies) and their customers.

The system is built on a **4-Context Composition Architecture**:
$$\text{compose}(\text{CategoryContext}, \text{MerchantContext}, \text{TriggerContext}, \text{CustomerContext?}) \to \text{ComposedMessage}$$

### Core Modules
* **`bot.py`**:
  * `compose(...)`: High-specificity, zero-hallucination engagement composer adhering to vertical tones and Cialdini compulsion levers.
  * **HTTP Server (FastAPI)**: Exposes the 5 required endpoints (`POST /v1/context`, `POST /v1/tick`, `POST /v1/reply`, `GET /v1/healthz`, `GET /v1/metadata`).
* **`conversation_handlers.py`**: Multi-turn state machine managing auto-reply loop breaking, intent transitions, delay requests, and hostile opt-outs.
* **`submission.jsonl`**: Composed outputs for all 30 canonical evaluation pairs (`T01`–`T30`).

---

## 2. Problem & Solution Summary

### PROBLEM
magicpin needs an AI merchant assistant on WhatsApp that generates contextual, relevant, and compelling conversations without suffering from auto-reply loops, intent-handoff regressions, generic discount pitches, or low engagement frequency.

### OUR SOLUTION
Our system:
1. Receives structured context via `POST /v1/context`
2. Stores context state with strict version control (idempotent, 409 stale-version handling)
3. Interprets incoming triggers during periodic `POST /v1/tick`
4. Identifies the most relevant merchant/category signals
5. Applies category-specific communication rules and taboos
6. Generates high-specificity, zero-hallucination messages
7. Validates response schemas and single binary/open-ended CTAs
8. Applies suppression and state rules
9. Returns standard API actions (`send`, `wait`, `end`)
10. Handles subsequent multi-turn replies (`POST /v1/reply`) seamlessly

### Concrete Example (Dentist Vertical):
* **INPUT**:
  * Category: `dentists` (clinical peer tone, peer avg CTR: 3.0%)
  * Merchant: `m_001_drmeera_dentist_delhi` (Dr. Meera's Dental Clinic, Lajpat Nagar, CTR: 2.1%)
  * Trigger: `trg_001_research_digest_fluoride` (JIDA Oct issue: 3-mo fluoride recall cuts caries 38% better)
* **DECISION**: Select the research item because it directly anchors on Dr. Meera's high-risk adult cohort with verifiable clinical evidence.
* **OUTPUT MESSAGE**:
  > *"Dr. Meera, JIDA's Oct issue landed. One item relevant to your high-risk adult patients — 2,100-patient trial showed 3-month fluoride recall cuts caries recurrence 38% better than 6-month. I drafted a 2-minute WhatsApp recall template for your 78 lapsed patients. Want me to send the preview over?"*
* **EVALUATION BREAKDOWN**:
  * **Specificity**: Cites "JIDA Oct issue", "2,100-patient trial", "38% better", "78 lapsed patients".
  * **Category Fit**: Peer clinical tone for dental practice, avoiding retail discount hype.
  * **Merchant Fit**: Personalized to Dr. Meera in Lajpat Nagar.
  * **Trigger Relevance**: Directly communicates the newly released research digest.
  * **Engagement**: Reciprocity + effort externalization with a clear binary preview CTA.

---

## 3. Solutions to Production Vera's Key Failure Modes

| Production Failure Mode | Our Implementation & Solution |
| :--- | :--- |
| **1. Auto-Reply Pollution** (40–70% canned replies burning turns) | Rapid heuristic and regex matching on WhatsApp Business automated messages + repetition frequency tracking across turns. Immediately triggers `action: "end"` with polite rationale to conserve budget. |
| **2. Intent-Handoff Regressions** (Vera requalifying after merchant says yes) | Explicit intent classifier detecting commitment signals (*"Ok let's do it"*, *"I want to join"*). Switches immediately to execution mode (`action: "send"`) with concrete drafts and action verbs (*Done*, *Proceeding*, *Draft*), completely omitting qualification queries. |
| **3. Generic Discount Pitches** (*"Flat 20% off"* / *"Increase sales"*) | Strict extraction of canonical service + price offerings (*"Dental Cleaning @ ₹299"*, *"Haircut @ ₹99"*), verified performance numbers (views, calls, CTR), and cited scientific sources (*JIDA Oct 2026, p.14*). |
| **4. Low Engagement Cadence** (Over-reliance on repetitive profile reminders) | Diversified outreach portfolio leveraging curiosity, social proof benchmarks, research digests, local weather (42°C heatwaves), and events (IPL matches) with contrarian recommendations. |

---

## 4. API Endpoints Documentation

The service exposes exactly 5 required endpoints under a single public base URL:

### 1. `POST /v1/context`
* **Purpose**: Ingest context payloads (`category`, `merchant`, `customer`, `trigger`).
* **Request**: `{"scope": "category", "context_id": "dentists", "version": 1, "payload": {...}}`
* **Response (200)**: `{"accepted": true, "ack_id": "ack_dentists_v1", "stored_at": "..."}`
* **Conflict (409)**: `{"accepted": false, "reason": "stale_version", "current_version": 2}`

### 2. `POST /v1/tick`
* **Purpose**: Periodic wake-up to inspect context and generate proactive outreach actions.
* **Request**: `{"now": "2026-04-26T10:30:00Z", "available_triggers": ["trg_001"]}`
* **Response (200)**: `{"actions": [{"conversation_id": "conv_1", "send_as": "vera", "body": "...", "cta": "open_ended", "suppression_key": "...", "rationale": "..."}]}`

### 3. `POST /v1/reply`
* **Purpose**: Synchronously handle merchant/customer responses in multi-turn conversations.
* **Request**: `{"conversation_id": "conv_1", "merchant_id": "m_001", "from_role": "merchant", "message": "Ok lets do it", "turn_number": 2}`
* **Response (200)**: `{"action": "send", "body": "Done! Proceeding...", "cta": "binary", "rationale": "..."}`

### 4. `GET /v1/healthz`
* **Purpose**: Lightweight liveness probe (independent of external LLM APIs).
* **Response (200)**: `{"status": "ok", "uptime_seconds": 120, "contexts_loaded": {...}}`

### 5. `GET /v1/metadata`
* **Purpose**: Returns bot team and architecture metadata.
* **Response (200)**: `{"team_name": "magicpin-ai-mastery", "model": "vera-engagement-hybrid-v1", "version": "1.0.0"}`

---

## 5. Running & Deploying

### Running Locally
```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start Uvicorn dev server
uvicorn bot:app --host 0.0.0.0 --port 8080

# 3. Test API suite & Swagger UI
python test_bot.py
# Open Swagger UI at http://localhost:8080/docs
```

### Render Web Service Deployment
* **Build Command**: `pip install -r requirements.txt`
* **Start Command**: `uvicorn bot:app --host 0.0.0.0 --port $PORT`
* **Deployment Note**: Connect your GitHub repository to your Render Web Service dashboard to obtain your live public service URL.

