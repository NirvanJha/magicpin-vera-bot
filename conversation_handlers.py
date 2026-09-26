"""
magicpin AI Challenge — Conversation Handlers
==============================================
Multi-turn reply engine for Vera. Classifies each inbound turn and returns
send / wait / end, grounded in the conversation's trigger + contexts.

Priority order (first match wins):
  1. empty            -> wait
  2. auto-reply       -> send nudge once, then wait 24h, then end
  3. explicit opt-out -> end (merchant suppressed by caller)
  4. abuse            -> apologise once, keep door open
  5. out-of-scope     -> decline politely, steer back to the trigger
  6. busy / later     -> wait
  7. soft decline     -> end politely
  8. commitment       -> switch to ACTION mode immediately (no more qualifying)
  9. question         -> answer from context
 10. anything else    -> acknowledge + one concrete next step
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from composer import Ctx, _l, first_sentence, fmt_int, human


# =============================================================================
# PATTERNS
# =============================================================================

AUTO_REPLY_PATTERNS = [
    r"thank you for contacting", r"thanks for contacting", r"thanks for reaching out",
    r"our team will (respond|get back|contact)", r"will get back to you", r"we are currently (away|unavailable|closed)",
    r"automated (assistant|message|reply|response)", r"auto-?reply", r"business hours are",
    r"reply (you )?shortly", r"out of (the )?office", r"connect with you soon",
    r"aapki jaankari ke liye", r"automated assistant hoon", r"team tak pahuncha", r"sujhaav hamari team",
    r"madad ke liye shukriya",
]

OPT_OUT_PATTERNS = [
    r"\bstop (messaging|texting|sending|contacting|this)\b", r"\bunsubscribe\b", r"\bopt[ -]?out\b",
    r"\bdon'?t (message|text|contact|send)\b", r"\bdo not (message|text|contact|send)\b",
    r"\bleave me alone\b", r"\bnot interested\b", r"\bno interest\b", r"\bremove (me|my number)\b",
    r"\bband karo\b", r"\bmat (bhejo|karo)\b", r"\bmessage mat\b", r"\bblock (you|kar)\b",
    r"^\s*stop\s*[.!]*\s*$",
]

ABUSE_PATTERNS = [
    r"\bspam\b", r"\buseless\b", r"\bwaste of time\b", r"\bfraud\b", r"\bscam\b", r"\bidiot\b",
    r"\bstupid\b", r"\bbakwas\b", r"\bbekaar\b", r"\bpathetic\b", r"\bnonsense\b", r"\bshut up\b",
    r"\bbloody\b", r"\bdamn\b",
]

OOS_PATTERNS = [
    r"\bgst\b", r"\btax(es)?\b", r"\bitr\b", r"\blegal\b", r"\blawyer\b", r"\bloan\b", r"\binsurance\b",
    r"\baccounting\b", r"\bca\b", r"\bchartered accountant\b", r"\bvisa\b", r"\bpassport\b", r"\bcricket score\b",
    r"\bweather\b", r"\bstock market\b", r"\bshare price\b",
]

BUSY_PATTERNS = [
    r"\bbusy\b", r"\bin a meeting\b", r"\bcall (me )?later\b", r"\btalk later\b", r"\bafter ?some ?time\b",
    r"\bthodi der\b", r"\bbaad mein\b", r"\bkal baat\b", r"\bdriving\b", r"\bwith (a )?(patient|customer)s?\b",
]

LATER_PATTERNS = [
    r"\bnot now\b", r"\bmaybe later\b", r"\blater\b", r"\bnext (week|month)\b", r"\babhi nahi\b",
    r"\bsome other time\b", r"\bafter (diwali|the season|this week)\b",
]

DECLINE_PATTERNS = [
    r"^\s*(no|nope|nah|nahi|nahin|na)\s*[.!]*\s*$", r"\bno thanks?\b", r"\bnahi chahiye\b", r"\bdon'?t need\b",
    r"\bnot needed\b", r"\bnot required\b", r"\bzaroorat nahi\b", r"\bno need\b",
]

COMMIT_PATTERNS = [
    r"\bok(ay)?\b", r"\byes\b", r"\byeah\b", r"\byup\b", r"\bsure\b", r"\bhaa?n\b", r"\bha\b", r"\bji\b",
    r"\blet'?s do it\b", r"\bdo it\b", r"\bgo ahead\b", r"\bproceed\b", r"\bwhat'?s next\b", r"\bwhats next\b",
    r"\bsend (it|me|the|over)\b", r"\bplease send\b", r"\bdraft it\b", r"\bsign me up\b", r"\bi want to (join|do|start)\b",
    r"\bchalega\b", r"\bkar do\b", r"\bbhej do\b", r"\btheek hai\b", r"\bthik hai\b", r"\bdone\b", r"\bconfirm(ed)?\b",
    r"\binterested\b", r"\bbook( it| me)?\b", r"\brenew\b", r"\bpublish\b", r"\bstart\b",
    r"^\s*[12]\s*$", r"^\s*👍+\s*$",
]

QUESTION_WORDS = r"\b(what|how|why|when|where|which|who|kya|kaise|kab|kitna|kitne|kaun|price|cost|charges?|fees?|rate|details?|explain)\b"
PRICE_WORDS = r"\b(price|cost|charges?|fees?|rate|kitna|kitne|how much|amount)\b"
TIME_WORDS = r"\b(when|time|slot|kab|timing|date|tomorrow|today)\b"
HINDI_WORDS = r"\b(haan|nahi|kya|karo|kar|hai|hain|chahiye|theek|thik|acha|accha|ji|bhej|kaise|kab|kitna|mujhe|aap|abhi|baad)\b"


def _any(patterns: List[str], text: str) -> bool:
    return any(re.search(p, text) for p in patterns)


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    trigger_kind: Optional[str] = None
    turns: List[Dict[str, Any]] = field(default_factory=list)
    auto_reply_count: int = 0
    stage: str = "initial"           # initial, action_mode, paused, closed
    closed_reason: Optional[str] = None
    apologized: bool = False
    action_count: int = 0


# =============================================================================
# CLASSIFIERS
# =============================================================================

def is_auto_reply(message: str, history: List[Dict[str, Any]]) -> bool:
    msg = message.lower().strip()
    if _any(AUTO_REPLY_PATTERNS, msg):
        return True
    # Same inbound text repeated verbatim (history already includes this turn).
    same = sum(1 for t in history if t.get("from") in ("merchant", "customer")
               and str(t.get("msg", "")).strip().lower() == msg)
    return same >= (2 if len(msg) > 25 else 3)


def is_hostile(message: str) -> bool:
    return _any(OPT_OUT_PATTERNS, message.lower())


def is_abuse(message: str) -> bool:
    return _any(ABUSE_PATTERNS, message.lower())


def is_commitment(message: str) -> bool:
    return _any(COMMIT_PATTERNS, message.lower())


def is_wait(message: str) -> bool:
    return _any(BUSY_PATTERNS, message.lower())


def speaks_hindi(message: str) -> bool:
    return bool(re.search(HINDI_WORDS, message.lower()) or re.search(r"[ऀ-ॿ]", message))


# =============================================================================
# CONTENT BUILDERS (grounded in the conversation's contexts)
# =============================================================================

def _item(c: Ctx) -> Optional[dict]:
    p = c.payload
    return c.digest(p.get("top_item_id") or p.get("digest_item_id") or p.get("alert_id"))


def core_fact(c: Optional[Ctx]) -> str:
    """One verifiable sentence about why we reached out."""
    if not c:
        return ""
    it = _item(c)
    if it:
        return first_sentence(it.get("summary")) or f"{it.get('title')}."
    p = c.payload
    if p.get("molecule"):
        return f"The recall covers {p.get('molecule')} batches {', '.join(map(str, _l(p.get('affected_batches'))))}."
    if p.get("theme"):
        return f"{p.get('occurrences_30d', 'Several')} recent reviews mention {human(p.get('theme'))}."
    if p.get("festival"):
        return f"{p.get('festival')} is {p.get('days_until', 'a few')} days out."
    line = c.perf_line()
    if line:
        return line
    offers = c.active_offers()
    return f"Your live offer is '{offers[0]}'." if offers else ""


def action_body(c: Optional[Ctx], hi: bool, from_role: str) -> str:
    """What Vera actually delivers after a yes. Never asks another qualifying question."""
    if c is None:
        return ("Done — kaam shuru kar diya. Draft 2 minute mein yahin bhejti hoon; publish karne ke liye CONFIRM reply karein."
                if hi else "Done — I'm on it. The draft lands here in 2 minutes; reply CONFIRM and it goes live.")

    kind = str(c.trigger.get("kind") or "")
    if from_role == "customer":
        slots = c.slot_labels() or c.slot_labels("next_session_options")
        if kind in ("chronic_refill_due", "refill_reminder"):
            return "Done — your order is confirmed and will be dispatched to your saved address. We'll message you when it's out for delivery."
        slot = slots[0] if slots else ""
        return (f"Booked ✅ {slot + ' at ' if slot else ''}{c.m_name}. We'll send a reminder the day before. See you then!").strip()

    name = c.owner or ""
    lead = f"Done{', ' + name if name else ''} — "
    aud = c.audience()
    offers = c.active_offers()
    it = _item(c)

    if kind in ("research_digest", "research_digest_release", "category_research_digest_release") and it:
        body = (f"{lead}here's the gist of {it.get('source', 'the paper')}: {it.get('summary', it.get('title', ''))} "
                f"Takeaway: {it.get('actionable', 'worth reviewing for your case-mix')}\n\n"
                f"{aud.capitalize()} WhatsApp draft: \"New research on {human(it.get('patient_segment')) or 'your care'}: "
                f"{first_sentence(it.get('title'))} Reply to book a check.\"\n\n"
                f"Reply CONFIRM and I'll schedule it to your {aud} list.")
        return body
    if kind in ("regulation_change", "compliance_alert") and it:
        return (f"{lead}your checklist for '{it.get('title')}':\n"
                f"1. {it.get('actionable', 'Audit current setup against the new rule')}\n"
                f"2. Note the change in your SOP file with today's date\n"
                f"3. Brief staff in the next team huddle\n"
                f"Reply CONFIRM and I'll set a reminder 2 weeks before the deadline.")
    if kind in ("cde_opportunity", "cde_webinar_dentists") and it:
        return (f"{lead}noted your interest in '{it.get('title')}'. {first_sentence(it.get('actionable'))} "
                f"I'll send the registration details here and a reminder 1 day before.")
    if kind in ("supply_alert", "supply_recall"):
        mol = c.payload.get("molecule", "the affected medicine")
        batches = ", ".join(map(str, _l(c.payload.get("affected_batches"))))
        return (f"{lead}filtering your customer list for {mol} now.\n\nCustomer message draft: \"Important: batches {batches} of "
                f"{mol} are under a voluntary recall. Please bring your strip to {c.m_name} for a free replacement.\"\n\n"
                f"Reply CONFIRM and I'll send it to the affected customers.")
    if kind == "renewal_due":
        amt = fmt_int(c.payload.get("renewal_amount"))
        return f"{lead}renewal link{' for ₹' + amt if amt else ''} is on its way here. Your listing stays live with no gap."
    if kind == "active_planning_intent":
        return (f"{lead}turning the draft into a Google post + WhatsApp flyer now. "
                f"Both land here in 2 minutes — reply CONFIRM and they go live.")
    if kind in ("curious_ask_due", "scheduled_recurring"):
        return f"{lead}got it. Drafting the Google post + price-enquiry reply now; both land here in 2 minutes."

    # Post-style actions (perf dip/spike, competitor, festival, milestone, review theme, winback, ...)
    offer = f" featuring '{offers[0]}'" if offers else ""
    where = f" in {c.locality}" if c.locality else ""
    body = (f"{lead}drafting it now{offer}.\n\nPost draft: \"{c.m_name}{where}"
            f"{' — ' + offers[0] if offers else ''}. Walk in or message us to book.\"\n\n"
            f"Reply CONFIRM and it goes live today.")
    if hi:
        body = body.replace("Reply CONFIRM and it goes live today.", "CONFIRM reply karein, aaj hi live kar dungi.")
    return body


def answer_question(msg: str, c: Optional[Ctx], from_role: str) -> str:
    m = msg.lower()
    if c is not None:
        offers = c.active_offers()
        if re.search(PRICE_WORDS, m):
            amt = fmt_int(c.payload.get("renewal_amount"))
            if amt and c.trigger.get("kind") == "renewal_due":
                return f"Renewal is ₹{amt} for the {c.payload.get('plan', 'current')} plan. Reply YES and I'll send the link."
            if offers:
                return f"Current pricing: {'; '.join(offers[:3])}. Reply YES to go ahead."
            it = _item(c)
            if it and re.search(r"₹", it.get("summary", "") + it.get("actionable", "")):
                return f"{first_sentence(it.get('actionable')) or first_sentence(it.get('summary'))} Reply YES to go ahead."
            return "There's no charge for this — it's part of your Vera support. Reply YES and I'll get started."
        if re.search(TIME_WORDS, m):
            slots = c.slot_labels() or c.slot_labels("next_session_options")
            if slots:
                return f"Available: {' or '.join(slots)}. Reply 1 or 2 to pick one."
            return "I can have it ready within 2 hours. Reply YES and I'll start now."
        fact = core_fact(c)
        if fact:
            return f"Short version: {fact} Reply YES and I'll take care of the rest."
    return "Good question — I'll keep it short: I prepare it, you approve it, nothing goes live without your OK. Reply YES to start."


# =============================================================================
# MAIN ENTRY
# =============================================================================

def respond(
    state: Optional[ConversationState],
    merchant_message: str,
    merchant_context: Optional[dict] = None,
    category_context: Optional[dict] = None,
    trigger_context: Optional[dict] = None,
    customer_context: Optional[dict] = None,
    from_role: str = "merchant",
    merchant_auto_count: int = 0,
    now=None,
) -> dict:
    """Return {"action": send|wait|end, ...} for one inbound turn."""
    history = state.turns if state else []
    msg = (merchant_message or "").strip()
    low = msg.lower()
    hi = speaks_hindi(msg)
    c = Ctx(category_context or {}, merchant_context or {}, trigger_context or {}, customer_context, now) \
        if (merchant_context or trigger_context) else None
    owner = c.owner if c else ""

    def send(body: str, cta: str, why: str) -> dict:
        return {"action": "send", "body": body, "cta": cta, "rationale": why}

    if not msg:
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Empty message; waiting for a real reply."}

    # 1. Auto-reply ladder: nudge once -> wait 24h -> end
    if is_auto_reply(msg, history):
        count = max(state.auto_reply_count if state else 0, merchant_auto_count) + 1
        if state:
            state.auto_reply_count = count
        if count == 1:
            body = ("Lagta hai yeh auto-reply hai. Jab owner dekhein, bas 'Yes' reply kar dein — baaki main sambhal lungi."
                    if hi or (c and c.hi) else
                    "Looks like an auto-reply. When the owner sees this, just reply 'Yes' and I'll handle the rest.")
            return send(body, "binary", "First auto-reply detected; one short prompt to reach the owner.")
        if count == 2:
            if state:
                state.stage = "paused"
            return {"action": "wait", "wait_seconds": 86400,
                    "rationale": "Auto-reply again — owner not at phone. Backing off 24h instead of burning turns."}
        if state:
            state.stage, state.closed_reason = "closed", "auto_reply"
        return {"action": "end", "rationale": f"Auto-reply {count}x with no human response; exiting gracefully."}

    # Real human reply: if we previously closed on auto-reply / opt-out, respect it unless they re-engage.
    if state and state.stage == "closed":
        if state.closed_reason == "opt_out" and not (is_commitment(msg) or "?" in msg or re.search(QUESTION_WORDS, low)):
            return {"action": "end", "rationale": "Merchant opted out earlier; staying silent."}
        state.stage = "initial"
    if state:
        state.auto_reply_count = 0

    # 2. Explicit opt-out
    if is_hostile(msg):
        if state:
            state.stage, state.closed_reason = "closed", "opt_out"
        return {"action": "end",
                "rationale": "Merchant asked us to stop / not interested; ending immediately and suppressing further sends."}

    # 3. Abuse without an explicit stop -> apologise once, keep door open
    if is_abuse(msg) and not (state and state.apologized):
        if state:
            state.apologized = True
        body = (f"Sorry{', ' + owner if owner else ''} — I'll only message when there's something concrete for "
                f"{c.m_name if c and c.m_name else 'your business'}. Reply STOP anytime and I'll stop completely.")
        return send(body, "none", "Hostile tone; apologised, offered a clean opt-out, no hard sell.")
    if is_abuse(msg):
        if state:
            state.stage, state.closed_reason = "closed", "opt_out"
        return {"action": "end", "rationale": "Repeated hostility after apology; exiting gracefully."}

    # 4. Out-of-scope ask -> polite decline + steer back
    if re.search(r"\b(gst|tax|itr|ca|legal|loan|insurance|accounting|visa|passport)\b", low) and _any(OOS_PATTERNS, low):
        fact = core_fact(c)
        back = f" Coming back to what I flagged: {fact}" if fact else ""
        body = (f"That one's outside what I can do — your CA is the right person for it.{back} "
                f"Reply YES and I'll take that forward.").replace("  ", " ")
        return send(body.strip(), "binary", "Out-of-scope ask declined politely; steered back to the original trigger.")

    # 5. Busy -> short wait; not-now -> long wait
    if is_wait(msg):
        if state:
            state.stage = "paused"
        return {"action": "wait", "wait_seconds": 1800, "rationale": "Merchant is busy; backing off 30 min."}
    if _any(LATER_PATTERNS, low) and not re.search(r"\b(yes|haan|ok)\b", low):
        if state:
            state.stage = "paused"
        return {"action": "wait", "wait_seconds": 86400, "rationale": "Merchant said not now; backing off 24h."}

    # 6. Soft decline -> polite end
    if _any(DECLINE_PATTERNS, low):
        if state:
            state.stage, state.closed_reason = "closed", "declined"
        return {"action": "end", "rationale": "Merchant declined; exiting without pushing."}

    # 7. Commitment -> action mode immediately (price questions get answered inline)
    if is_commitment(msg):
        if state:
            state.stage = "action_mode"
            state.action_count += 1
        if re.search(PRICE_WORDS, low):
            return send(answer_question(msg, c, from_role), "binary", "Commitment + price question; answered with real pricing.")
        if state and state.action_count > 1:
            body = ("Done — CONFIRM mil gaya, live kar diya. Kuch aur chahiye to yahin message karein."
                    if hi else "Done — it's live. I'll share how it performs in 7 days.")
            return send(body, "none", "Merchant confirmed the delivered draft; executed and closed the loop.")
        return send(action_body(c, hi, from_role), "binary" if from_role != "customer" else "none",
                    "Merchant committed; switched from pitching to delivering the concrete artifact.")

    # 8. Question -> answer from context
    if "?" in msg or re.search(QUESTION_WORDS, low):
        return send(answer_question(msg, c, from_role), "binary", "Answered the question from pushed context, then one CTA.")

    # 9. Default -> acknowledge + one concrete next step
    if state and state.stage == "action_mode":
        return send("Noted — updating the draft with that. The revised version lands here in 2 minutes.", "none",
                    "Merchant gave edits while in action mode; applying them.")
    fact = core_fact(c)
    body = f"Got it. {fact + ' ' if fact else ''}Reply YES and I'll prepare it for you — nothing goes live without your OK."
    return send(body, "binary", "Acknowledged and restated one concrete, grounded next step.")
