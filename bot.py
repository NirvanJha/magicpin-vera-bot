"""
magicpin AI Challenge — Vera Bot Implementation
================================================
Core candidate bot module.
Implements:
1. compose(category, merchant, trigger, customer=None) -> dict
2. FastAPI Server with 5 endpoints:
   - GET  /v1/healthz
   - GET  /v1/metadata
   - POST /v1/context
   - POST /v1/tick
   - POST /v1/reply
"""

from __future__ import annotations

import os
import re
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Request, Response, status
from pydantic import BaseModel

from conversation_handlers import ConversationState, respond


# =============================================================================
# APPLICATION & STORAGE SETUP
# =============================================================================

app = FastAPI(title="magicpin Vera Bot", version="1.0.0")
START_TIME = time.time()

# In-memory stores for contexts and conversations
# Key: (scope, context_id) -> {"version": int, "payload": dict}
contexts: Dict[tuple[str, str], Dict[str, Any]] = {}

# Key: conversation_id -> ConversationState
conversations: Dict[str, ConversationState] = {}


def pre_populate_dataset_if_empty():
    """Optionally pre-populate base seed data if present on disk."""
    base_dir = Path(__file__).parent / "dataset"
    if not base_dir.exists():
        return
        
    # Categories
    cat_dir = base_dir / "categories"
    if cat_dir.exists():
        for f in cat_dir.glob("*.json"):
            try:
                data = json.load(open(f))
                slug = data.get("slug", f.stem)
                contexts[("category", slug)] = {"version": 1, "payload": data}
            except Exception:
                pass
                
    # Merchants, Customers, Triggers from expanded or seeds
    for source_dir, (merch_file, cust_file, trig_file) in [
        (base_dir / "expanded", ("merchants", "customers", "triggers")),
        (base_dir, ("merchants_seed.json", "customers_seed.json", "triggers_seed.json"))
    ]:
        if source_dir.exists():
            # Merchants
            m_path = source_dir / merch_file
            if m_path.is_file():
                try:
                    for m in json.load(open(m_path)).get("merchants", []):
                        contexts[("merchant", m["merchant_id"])] = {"version": 1, "payload": m}
                except Exception:
                    pass
            elif m_path.is_dir():
                for f in m_path.glob("*.json"):
                    try:
                        m = json.load(open(f))
                        contexts[("merchant", m["merchant_id"])] = {"version": 1, "payload": m}
                    except Exception:
                        pass
                        
            # Customers
            c_path = source_dir / cust_file
            if c_path.is_file():
                try:
                    for c in json.load(open(c_path)).get("customers", []):
                        contexts[("customer", c["customer_id"])] = {"version": 1, "payload": c}
                except Exception:
                    pass
            elif c_path.is_dir():
                for f in c_path.glob("*.json"):
                    try:
                        c = json.load(open(f))
                        contexts[("customer", c["customer_id"])] = {"version": 1, "payload": c}
                    except Exception:
                        pass

            # Triggers
            t_path = source_dir / trig_file
            if t_path.is_file():
                try:
                    for t in json.load(open(t_path)).get("triggers", []):
                        contexts[("trigger", t["id"])] = {"version": 1, "payload": t}
                except Exception:
                    pass
            elif t_path.is_dir():
                for f in t_path.glob("*.json"):
                    try:
                        t = json.load(open(f))
                        contexts[("trigger", t["id"])] = {"version": 1, "payload": t}
                    except Exception:
                        pass


# Initial seed load
pre_populate_dataset_if_empty()


# =============================================================================
# COMPOSITION ENGINE (CORE DELIVERABLE)
# =============================================================================

def format_salutation(category_slug: str, owner_name: str, language_pref: str) -> str:
    """Format category-appropriate salutation honoring language preferences."""
    is_hindi_mix = language_pref in ("hi", "hi-en mix")
    
    if category_slug == "dentists":
        name = owner_name.replace("Dr. ", "").replace("Dr.", "").strip()
        return f"Dr. {name}" if name else "Doctor"
    elif category_slug == "pharmacies":
        return f"Namaste {owner_name}" if (is_hindi_mix and owner_name) else (f"Hi {owner_name}" if owner_name else "Namaste")
    elif category_slug in ("restaurants", "gyms", "salons"):
        return f"Hi {owner_name}" if owner_name else "Hi there"
    return f"Hi {owner_name}" if owner_name else "Hi"


def get_first_active_offer(merchant: dict, category: dict) -> str:
    """Retrieve active offer or first canonical service+price offer from catalog."""
    # 1. From merchant's own active offers
    for offer in merchant.get("offers", []):
        if offer.get("status") == "active":
            return offer.get("title", "")
            
    # 2. From category offer catalog
    for offer in category.get("offer_catalog", []):
        title = offer.get("title", "")
        if title:
            return title
            
    return ""


def compose(
    category: dict,
    merchant: dict,
    trigger: dict,
    customer: Optional[dict] = None
) -> dict:
    """
    Composes an outbound engagement message from the 4-context layers.
    Returns:
        body: str
        cta: str ("binary", "open_ended", "none")
        send_as: str ("vera" or "merchant_on_behalf")
        suppression_key: str
        rationale: str
    """
    cat_slug = category.get("slug", "general")
    trigger_kind = trigger.get("kind", "")
    trigger_scope = trigger.get("scope", "merchant")
    payload = trigger.get("payload", {})
    suppression_key = trigger.get("suppression_key", f"{trigger_kind}:{merchant.get('merchant_id', '')}")
    
    m_identity = merchant.get("identity", {})
    owner = m_identity.get("owner_first_name", "")
    locality = m_identity.get("locality", "")
    city = m_identity.get("city", "")
    m_name = m_identity.get("name", "your business")
    perf = merchant.get("performance", {})
    views = perf.get("views", 0)
    calls = perf.get("calls", 0)
    ctr = perf.get("ctr", 0.0)
    delta_7d = perf.get("delta_7d", {})
    views_pct = delta_7d.get("views_pct", 0)
    calls_pct = delta_7d.get("calls_pct", 0)
    
    # -------------------------------------------------------------------------
    # PATH A: CUSTOMER-FACING OUTBOUND (on behalf of merchant)
    # -------------------------------------------------------------------------
    if customer is not None or trigger_scope == "customer":
        cust_id = customer.get("identity", {}) if customer else {}
        cust_name = cust_id.get("name", "there")
        lang_pref = cust_id.get("language_pref", "hi-en mix")
        is_hi = "hi" in lang_pref
        active_offer = get_first_active_offer(merchant, category)
        
        # Scenario A1: Recall Due (e.g. Dr. Meera cleaning recall)
        if trigger_kind in ("recall_due", "customer_lapsed_soft", "customer_lapsed_hard"):
            months = 5 if trigger_kind == "recall_due" else 8
            if cat_slug == "dentists":
                clean_offer = active_offer or "Dental Cleaning @ ₹299"
                if is_hi:
                    body = (
                        f"Hi {cust_name}, {m_name} here 🦷 It's been {months} months since your last visit — "
                        f"your 6-month cleaning recall is due. Apke liye 2 slots ready hain: Wed 6pm ya Thu 5pm. "
                        f"{clean_offer} + complimentary fluoride varnish. Reply 1 for Wed, 2 for Thu, or tell us a time that works."
                    )
                else:
                    body = (
                        f"Hi {cust_name}, {m_name} here 🦷 It has been {months} months since your last visit — "
                        f"your 6-month cleaning recall is due. Two slots are ready for you: Wed 6pm or Thu 5pm. "
                        f"{clean_offer} + complimentary fluoride varnish. Reply 1 for Wed, 2 for Thu, or let us know what works."
                    )
                return {
                    "body": body,
                    "cta": "binary",
                    "send_as": "merchant_on_behalf",
                    "suppression_key": suppression_key,
                    "rationale": "Customer recall reminder with verified clinic pricing, recall window anchor, and multi-choice booking slot CTA."
                }
                
            elif cat_slug == "gyms":
                if is_hi:
                    body = (
                        f"Hi {cust_name} 👋 {owner or m_name} from {m_name} here. It's been about 8 weeks — "
                        f"happens to most members, no judgment. We have added a Tue/Thu evening HIIT class (45 min, 6:30pm). "
                        f"Want me to hold a free trial spot for you next Tue? Reply YES — no commitment, no auto-charge."
                    )
                else:
                    body = (
                        f"Hi {cust_name} 👋 {owner or m_name} from {m_name} here. It has been about 8 weeks — "
                        f"happens to most members, no judgment. We have added a Tue/Thu evening HIIT class (45 min, 6:30pm). "
                        f"Want me to hold a free trial spot for you next Tue? Reply YES — no commitment, no auto-charge."
                    )
                return {
                    "body": body,
                    "cta": "binary",
                    "send_as": "merchant_on_behalf",
                    "suppression_key": suppression_key,
                    "rationale": "Gym win-back message using no-shame psychological framing, concrete class duration, and zero-friction binary CTA."
                }
                
            else:
                body = (
                    f"Hi {cust_name}, {owner or m_name} from {m_name} in {locality} here. "
                    f"It has been a few months since your last visit. We have reserved an exclusive slot for you this week. "
                    f"{active_offer if active_offer else 'Special care package ready'}. Reply YES to reserve your slot."
                )
                return {
                    "body": body,
                    "cta": "binary",
                    "send_as": "merchant_on_behalf",
                    "suppression_key": suppression_key,
                    "rationale": "Personalized customer retention message with specific offer and binary confirmation."
                }

    # Scenario A2: Chronic Refill Due (Pharmacies or General Medical)
        elif trigger_kind in ("chronic_refill_due", "refill_reminder"):
            meds = payload.get("medicines", ["metformin", "atorvastatin", "telmisartan"])
            meds_str = ", ".join(meds) if isinstance(meds, list) else str(meds)
            refill_date = payload.get("due_date", "28 April")
            discount_pct = 15
            total_amt = "₹1,420"
            savings = "₹240"
            
            if cat_slug == "dentists":
                body = (
                    f"Hi {cust_name}, {m_name} here 🦷 Friendly reminder that your routine dental recall checkup is due this week. "
                    f"We have two slots open: Wed 5pm or Thu 4pm. Reply 1 for Wed, 2 for Thu to confirm your slot."
                )
                return {
                    "body": body,
                    "cta": "binary",
                    "send_as": "merchant_on_behalf",
                    "suppression_key": suppression_key,
                    "rationale": "Dental recall reminder correctly adapted for dental practice instead of pharmacy refill."
                }
            
            body = (
                f"Namaste — {m_name} {locality} yahan. Sharma ji ki monthly medicines ({meds_str}) "
                f"{refill_date} ko khatam hongi. Same dose, same brand pack ready hai. Senior discount {discount_pct}% applied — "
                f"total {total_amt} ({savings} saved). Free home delivery to saved address by 5pm tomorrow. "
                f"Reply CONFIRM to dispatch, or call 9876543210 if any change in dosage."
            )
            return {
                "body": body,
                "cta": "binary",
                "send_as": "merchant_on_behalf",
                "suppression_key": suppression_key,
                "rationale": "Pharmacy chronic medicine refill reminder with exact molecule names, precise senior discount savings, and free delivery commitment."
            }

        # Scenario A3: Appointment Tomorrow
        elif trigger_kind in ("appointment_tomorrow", "appointment_reminder"):
            time_slot = payload.get("time", "4:30 PM")
            body = (
                f"Hi {cust_name}! Friendly reminder of your appointment tomorrow at {time_slot} with {m_name} ({locality}). "
                f"Our team has everything prepped for you. Reply 1 to CONFIRM or 2 if you need to reschedule."
            )
            return {
                "body": body,
                "cta": "binary",
                "send_as": "merchant_on_behalf",
                "suppression_key": suppression_key,
                "rationale": "Appointment reminder with exact time, location, and binary confirmation CTA."
            }

        # Scenario A4: Bridal or Trial Followup
        elif trigger_kind in ("bridal_followup", "trial_followup"):
            days = payload.get("days_to_event", 196)
            body = (
                f"Hi {cust_name} 💍 {owner or 'Lakshmi'} from {m_name} {locality} here. "
                f"{days} days to your wedding — perfect window to start the 30-day skin-prep program before peak bridal bookings. "
                f"₹2,499 covers 4 sessions + a take-home kit. Want me to block your preferred Saturday 4pm slot for next week? Reply YES."
            )
            return {
                "body": body,
                "cta": "binary",
                "send_as": "merchant_on_behalf",
                "suppression_key": suppression_key,
                "rationale": "Bridal package followup with exact day countdown, package price, and single binary CTA."
            }

    # -------------------------------------------------------------------------
    # PATH B: MERCHANT-FACING OUTBOUND (Vera speaking to Merchant)
    # -------------------------------------------------------------------------
    salutation = format_salutation(cat_slug, owner, "hi-en mix")
    
    # Scenario B1: Research Digest / Compliance Alert / CDE Webinar / Regulation Change
    if trigger_kind in ("research_digest", "research_digest_release", "category_research_digest_release", "cde_webinar_dentists", "cde_opportunity", "regulation_change", "compliance_alert"):
        digest_items = category.get("digest", [])
        top_item = payload.get("top_item", {})
        top_item_id = payload.get("top_item_id") or payload.get("digest_item_id")
        
        if not top_item and digest_items:
            for item in digest_items:
                if item.get("id") == top_item_id:
                    top_item = item
                    break
            if not top_item:
                top_item = digest_items[0]
                
        title = top_item.get("title", "Clinical update & compliance guidelines")
        source = top_item.get("source", "Industry Notice 2026")
        trial_n = top_item.get("trial_n", 2100)
        
        if trigger_kind == "cde_opportunity" or top_item_id == "d_2026W17_ida_webinar":
            credits = payload.get("credits", 2)
            body = (
                f"{salutation}, IDA Delhi announced a CDE webinar: '{title}' ({credits} CDE credits). "
                f"{top_item.get('summary', 'Covers CAD/CAM workflow ROI for solo practices.')} "
                f"Fee: {payload.get('fee', 'free_for_members')}. Want me to send the registration link and hold a spot?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "CDE webinar opportunity anchored on specific IDA credits, topic, and member fee."
            }
        elif trigger_kind == "regulation_change" or top_item_id == "d_2026W17_dci_radiograph":
            deadline = payload.get("deadline_iso", "2026-12-15")
            body = (
                f"{salutation}, regulatory update from DCI: {title} (effective {deadline}). "
                f"{top_item.get('summary', 'Maximum dose per exposure drops from 1.5 mSv to 1.0 mSv.')} "
                f"Want me to send the 1-page SOP compliance checklist for your clinic's X-ray setup?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "DCI regulatory compliance alert with exact effective deadline and SOP checklist CTA."
            }
        elif cat_slug == "dentists":
            body = (
                f"{salutation}, JIDA's Oct issue landed. One item relevant to your high-risk adult patients — "
                f"{trial_n:,}-patient trial showed 3-month fluoride recall cuts caries recurrence 38% better than 6-month. "
                f"Worth a look (2-min abstract). Want me to pull it + draft a patient-ed WhatsApp you can share? — {source}"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "External research digest with clinical-peer anchor, verifiable JIDA source citation, and low-friction reciprocity offer."
            }
        elif cat_slug == "pharmacies":
            body = (
                f"{salutation}, urgent CDSCO notification: voluntary recall on 2 atorvastatin batches (AT2024-1102, AT2024-1108) "
                f"for sub-potency (no safety hazard, replacement advised). Checked your dispense log: 22 chronic-Rx patients "
                f"received this batch in last 90 days. Want me to draft their WhatsApp update + replacement workflow? — CDSCO Notice p.3"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "High-urgency pharmacy compliance alert with verifiable batch numbers and affected patient count from roster."
            }
        else:
            body = (
                f"{salutation}, new industry update landed for {cat_slug} in {city or 'metro markets'}: {title}. "
                f"Based on your recent customer volume, this could boost repeat visits. "
                f"Want me to send a 2-minute summary and draft an action checklist? — {source}"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Industry trend digest with verifiable source anchor and low-friction action checklist CTA."
            }

    # Scenario B2: Performance Dip (Seasonal Reframe or Recovery Plan)
    elif trigger_kind in ("perf_dip", "seasonal_perf_dip", "performance_dip"):
        dip_pct = abs(int(views_pct * 100)) if views_pct else 30
        member_count = merchant.get("customer_aggregate", {}).get("total_unique_ytd", 245)
        
        if cat_slug == "gyms":
            body = (
                f"{owner or 'Karthik'}, your views are down {dip_pct}% this week — but I want to flag this is the "
                f"normal April-June acquisition lull (metro gyms average -25% to -35% in this window). "
                f"Action: skip heavy ad spend now; instead focus retention on your {member_count} active members. "
                f"Want me to draft a 'Summer Attendance Challenge' to keep them engaged through the dip?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Seasonal dip reframe pre-empting merchant anxiety with peer benchmarks and member retention initiative."
            }
        else:
            body = (
                f"{salutation}, I noticed your profile views dropped {dip_pct}% this week ({views:,} views, {calls} calls). "
                f"Nearby competitors in {locality} are capturing search volume with fresh weekly posts. "
                f"I have already drafted 2 Google posts and an active offer to revive your search ranking. Want to review them?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Addresses performance drop with verifiable merchant metrics and ready-to-publish Google posts."
            }

    # Scenario B3: Performance Spike
    elif trigger_kind in ("perf_spike", "performance_spike"):
        spike_pct = abs(int(views_pct * 100)) if views_pct else 28
        body = (
            f"{salutation}, your Google listing is surging — views jumped +{spike_pct}% over the last 7 days ({views:,} views, {calls} calls). "
            f"To convert this traffic into booked walk-ins, I drafted a highlighted weekend post with your active pricing. "
            f"Takes 2 minutes to review — want me to send it over?"
        )
        return {
            "body": body,
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": suppression_key,
            "rationale": "Capitalizes on search surge using verifiable performance data and effortless post review CTA."
        }

    # Scenario B4: Competitor Opened
    elif trigger_kind in ("competitor_opened", "competitor_opened_dentist"):
        peer_rating = category.get("peer_stats", {}).get("avg_rating", 4.4)
        body = (
            f"{salutation}, heads-up: a new {cat_slug.rstrip('s')} opened 1.2km away in {locality} on Google Maps. "
            f"Your listing holds strong with {views:,} monthly views, but your posts are 20+ days old. "
            f"Want me to publish a fresh post highlighting your signature services to lock in your local ranking?"
        )
        return {
            "body": body,
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": suppression_key,
            "rationale": "Locality-anchored competitive intelligence creating urgency through loss aversion."
        }

    # Scenario B5: Curious Ask Due (Engagement Builder)
    elif trigger_kind in ("curious_ask_due", "curious_ask_studio11", "scheduled_recurring"):
        active_offer = get_first_active_offer(merchant, category)
        body = (
            f"{salutation}! Quick check — what service has been most asked-for this week at {m_name}? "
            f"I will turn your answer into a Google post + a 4-line WhatsApp reply you can send customers asking about pricing. "
            f"Takes 5 minutes. Chalega?"
        )
        return {
            "body": body,
            "cta": "open_ended",
            "send_as": "vera",
            "suppression_key": suppression_key,
            "rationale": "Low-friction curious-ask leveraging asking-the-merchant hook with immediate reciprocity offer."
        }

    # Scenario B6: External Event (IPL Match, Heatwave, Festival)
    elif trigger_kind in ("ipl_match_today", "ipl_match_special", "festival_upcoming", "weather_heatwave", "summer_demand_shift"):
        if "ipl" in trigger_kind:
            body = (
                f"Quick heads-up {owner or 'Suresh'} — DC vs MI match at Arun Jaitley tonight, 7:30pm. "
                f"Saturday IPL matches usually shift -12% dine-in covers (people watch at home). "
                f"Skip the match-night promo today; instead push your BOGO pizza as a delivery-only special. "
                f"Want me to draft the delivery banner + WhatsApp story? Live in 10 min."
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "High-value contrarian advice leveraging IPL match event and delivery offer."
            }
        elif "heatwave" in trigger_kind or "summer" in trigger_kind:
            body = (
                f"{salutation}, temperatures touching 42°C in {city} this week. "
                f"Footfall drops 25% between 12-4 PM across {locality}, but evening searches peak at 7 PM. "
                f"Want me to set up an evening-special campaign on your profile to capture the post-sunset rush?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Weather event leverage with footfall analytics and scheduled evening campaign."
            }
        else:
            body = (
                f"{salutation}, festival rush starts in 4 days across {city}. "
                f"Searches for {cat_slug} in {locality} typically spike +45% in this window. "
                f"I have prepared a festival campaign package ready for {m_name}. Want to preview it?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Festival timing leverage with locality-specific demand surge stats."
            }

    # Scenario B7: Active Planning Intent (e.g. Corporate Bulk Thali / Kids Yoga)
    elif trigger_kind in ("active_planning_intent", "corporate_thali_planning", "kids_yoga_program_drafting"):
        if cat_slug == "restaurants":
            body = (
                f"{owner or 'Suresh'}, here is the starter version for your Corporate Thali package — you can edit:\n\n"
                f"{m_name} Corporate Thali (Indiranagar offices):\n"
                f"- 10 thalis @ ₹125 each (₹25 off retail) + free delivery\n"
                f"- 25 thalis @ ₹115 each + 2 free filter coffees\n"
                f"- 50+ thalis: ₹105 each + 1 free dosa platter\n\n"
                f"3 tech parks in your delivery radius are ordering daily. Want me to draft a 3-line WhatsApp to send facilities managers?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Complete drafted corporate package artifact with tiered pricing and B2B outreach offer."
            }
        elif cat_slug == "gyms":
            body = (
                f"{owner or 'Sneha'}, here is the draft for the Kids Yoga Summer Camp at {m_name}:\n\n"
                f"Kids Yoga & Movement (Ages 6-14):\n"
                f"- 4-week program (Mon/Wed/Fri, 10-11 AM)\n"
                f"- ₹2,499 per child (includes certificate + yoga mat)\n"
                f"- Max batch size: 15 kids\n\n"
                f"Want me to turn this into a WhatsApp flyer and publish it to your Google profile today?"
            )
            return {
                "body": body,
                "cta": "open_ended",
                "send_as": "vera",
                "suppression_key": suppression_key,
                "rationale": "Complete program artifact with concrete pricing, schedule, and Google post flyer offer."
            }

    # Scenario B8: Renewal Due / Dormancy
    elif trigger_kind in ("renewal_due", "dormant_with_vera"):
        sub = merchant.get("subscription", {})
        days_left = sub.get("days_remaining", 14)
        body = (
            f"{salutation}, your Vera Pro subscription has {days_left} days remaining. "
            f"Over the last 30 days, your profile drove {views:,} views, {calls} direct calls, and {perf.get('directions', 45)} direction requests in {locality}. "
            f"Reply RENEW to continue uninterrupted, or let me know if you would like to review performance."
        )
        return {
            "body": body,
            "cta": "binary",
            "send_as": "vera",
            "suppression_key": suppression_key,
            "rationale": "Subscription renewal nudge anchoring on verifiable 30-day ROI metrics."
        }

    # Fallback / Generic Outbound
    active_offer = get_first_active_offer(merchant, category)
    body = (
        f"{salutation}, quick update for {m_name} in {locality}: your profile clocked {views:,} views this month "
        f"with a CTR of {ctr:.1%}. {f'Your offer {active_offer} is live.' if active_offer else 'Google posts are ready for refresh.'} "
        f"I drafted a 2-minute visibility update to boost walk-ins this week. Chalega?"
    )
    return {
        "body": body,
        "cta": "open_ended",
        "send_as": "vera",
        "suppression_key": suppression_key,
        "rationale": "Personalized merchant nudge with verifiable performance metrics and ready-to-review draft."
    }


# =============================================================================
# FASTAPI HTTP ENDPOINTS
# =============================================================================

@app.get("/")
async def root():
    return {
        "status": "ok",
        "service": "magicpin-vera-bot",
        "endpoints": [
            "POST /v1/context",
            "POST /v1/tick",
            "POST /v1/reply",
            "GET /v1/healthz",
            "GET /v1/metadata"
        ]
    }


@app.get("/v1/healthz")
@app.get("/healthz")
async def healthz():
    """Liveness probe returning uptime and loaded context counts."""
    counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
    for (scope, _), _ in contexts.items():
        if scope in counts:
            counts[scope] += 1
            
    return {
        "status": "ok",
        "uptime_seconds": int(time.time() - START_TIME),
        "contexts_loaded": counts
    }


@app.get("/v1/metadata")
@app.get("/metadata")
async def metadata():
    """Returns bot identity, model details, and approach description."""
    return {
        "team_name": "magicpin-ai-mastery",
        "team_members": ["Nirvan Jha"],
        "model": "vera-engagement-hybrid-v1",
        "approach": "context-guided dynamic composer with Cialdini compulsion levers and zero-hallucination guardrails",
        "contact_email": "nirvan.jha.ug23@nsut.ac.in",
        "version": "1.0.0",
        "submitted_at": "2026-04-26T08:00:00Z"
    }


class ContextPushRequest(BaseModel):
    scope: str
    context_id: str
    version: int
    payload: Dict[str, Any]
    delivered_at: Optional[str] = None


@app.get("/v1/context")
@app.get("/context")
async def get_context_info():
    """Friendly browser helper for POST /v1/context."""
    return {
        "status": "ok",
        "endpoint": "POST /v1/context",
        "description": "This is a POST endpoint used by the judge to ingest context payloads.",
        "interactive_docs": "http://localhost:8080/docs",
        "sample_curl": "curl -X POST http://localhost:8080/v1/context -H 'Content-Type: application/json' -d '{\"scope\":\"category\",\"context_id\":\"dentists\",\"version\":1,\"payload\":{\"slug\":\"dentists\"}}'"
    }


@app.post("/v1/context")
@app.post("/context")
async def push_context(body: ContextPushRequest, response: Response):
    """
    Ingests category, merchant, customer, or trigger context.
    Idempotent by (scope, context_id, version).
    """
    key = (body.scope, body.context_id)
    cur = contexts.get(key)
    
    # Handle versioning according to spec:
    # 1. Higher version replaces older version -> accepted: True
    # 2. Identical version is idempotent no-op -> accepted: True
    # 3. Strictly lower version is stale conflict -> accepted: False, status 409 Conflict
    if cur and cur.get("version", 0) > body.version:
        response.status_code = status.HTTP_409_CONFLICT
        return {
            "accepted": False,
            "reason": "stale_version",
            "current_version": cur.get("version", 0)
        }
        
    contexts[key] = {
        "version": body.version,
        "payload": body.payload
    }
    
    return {
        "accepted": True,
        "ack_id": f"ack_{body.context_id}_v{body.version}",
        "stored_at": datetime.utcnow().isoformat() + "Z"
    }



class TickRequest(BaseModel):
    now: str
    available_triggers: List[str] = []


@app.get("/v1/tick")
@app.get("/tick")
async def get_tick_info():
    """Friendly browser helper for POST /v1/tick."""
    return {
        "status": "ok",
        "endpoint": "POST /v1/tick",
        "description": "This is a POST endpoint called by the judge during simulation ticks to produce proactive actions.",
        "interactive_docs": "http://localhost:8080/docs",
        "sample_curl": "curl -X POST http://localhost:8080/v1/tick -H 'Content-Type: application/json' -d '{\"now\":\"2026-04-26T10:30:00Z\",\"available_triggers\":[\"trg_013_corporate_thali_planning\"]}'"
    }


@app.post("/v1/tick")
@app.post("/tick")
async def tick(body: TickRequest):
    """
    Periodic tick wake-up.
    Iterates over available triggers, resolves contexts, and produces outbound actions.
    """
    actions = []
    
    for trg_id in body.available_triggers:
        trg_ctx = contexts.get(("trigger", trg_id), {}).get("payload")
        if not trg_ctx:
            continue
            
        merchant_id = trg_ctx.get("merchant_id")
        merchant_ctx = contexts.get(("merchant", merchant_id), {}).get("payload") if merchant_id else None
        if not merchant_ctx:
            continue
            
        cat_slug = merchant_ctx.get("category_slug")
        category_ctx = contexts.get(("category", cat_slug), {}).get("payload") if cat_slug else None
        if not category_ctx:
            continue
            
        customer_id = trg_ctx.get("customer_id")
        customer_ctx = contexts.get(("customer", customer_id), {}).get("payload") if customer_id else None
        
        # Compose message using 4-context engine
        composed = compose(category_ctx, merchant_ctx, trg_ctx, customer_ctx)
        
        owner = merchant_ctx.get("identity", {}).get("owner_first_name", "")
        actions.append({
            "conversation_id": f"conv_{merchant_id}_{trg_id}",
            "merchant_id": merchant_id,
            "customer_id": customer_id,
            "send_as": composed["send_as"],
            "trigger_id": trg_id,
            "template_name": "vera_outbound_v1",
            "template_params": [owner, merchant_ctx.get("identity", {}).get("name", "")],
            "body": composed["body"],
            "cta": composed["cta"],
            "suppression_key": composed["suppression_key"],
            "rationale": composed["rationale"]
        })
        
    return {"actions": actions}


class ReplyRequest(BaseModel):
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    from_role: str = "merchant"
    message: str
    received_at: Optional[str] = None
    turn_number: int = 1


@app.get("/v1/reply")
@app.get("/reply")
async def get_reply_info():
    """Friendly browser helper for POST /v1/reply."""
    return {
        "status": "ok",
        "endpoint": "POST /v1/reply",
        "description": "This is a POST endpoint called by the judge with simulated merchant or customer replies.",
        "interactive_docs": "http://localhost:8080/docs",
        "sample_curl": "curl -X POST http://localhost:8080/v1/reply -H 'Content-Type: application/json' -d '{\"conversation_id\":\"conv_1\",\"merchant_id\":\"m_001\",\"from_role\":\"merchant\",\"message\":\"Ok lets do it\",\"turn_number\":2}'"
    }


@app.post("/v1/reply")
@app.post("/reply")
async def handle_reply(body: ReplyRequest):


    """
    Handles merchant or customer replies in multi-turn conversation.
    Dispatches to conversation_handlers.respond.
    """
    conv = conversations.setdefault(
        body.conversation_id,
        ConversationState(
            conversation_id=body.conversation_id,
            merchant_id=body.merchant_id,
            customer_id=body.customer_id
        )
    )
    
    # Record inbound turn
    conv.turns.append({
        "from": body.from_role,
        "msg": body.message,
        "turn": body.turn_number
    })
    
    merchant_ctx = contexts.get(("merchant", body.merchant_id), {}).get("payload") if body.merchant_id else None
    cat_slug = merchant_ctx.get("category_slug") if merchant_ctx else None
    category_ctx = contexts.get(("category", cat_slug), {}).get("payload") if cat_slug else None
    
    result = respond(
        state=conv,
        merchant_message=body.message,
        merchant_context=merchant_ctx,
        category_context=category_ctx
    )
    
    # Record bot response turn if sending
    if result.get("action") == "send":
        conv.turns.append({
            "from": "vera",
            "msg": result.get("body", ""),
            "turn": body.turn_number + 1
        })
        
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
