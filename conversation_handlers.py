"""
magicpin AI Challenge — Conversation Handlers
==============================================
Multi-turn reply engine for Vera.

    respond(state, merchant_message, ...) -> {"action": "send" | "wait" | "end", ...}

Every inbound turn is labelled by `classify()` (one intent, first match wins in
the order below), then answered from the conversation's own trigger + contexts.
Safety intents (auto-reply, opt-out, abuse) are always checked first.

    EMPTY, AUTO_REPLY, OPT_OUT, ABUSE, OUT_OF_SCOPE, BUSY, LATER, DECLINE, THANKS,
    IDENTITY, PRIVACY, EDIT, COMMIT, DELEGATE, CHANNEL, ALREADY_DONE, RELEVANCE,
    PRICE, TIME, QUESTION, HEDGE, OTHER
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from composer import Ctx, _l, first_sentence, fmt_int, human


# =============================================================================
# PATTERNS  (lower-cased input; Devanagari matched as plain substrings)
# =============================================================================

AUTO_REPLY_PATTERNS = [
    r"thank(s| you) for (contacting|reaching out)", r"our team will (respond|get back|contact|reply)",
    r"will get back to you", r"we('| a)re (currently )?(away|unavailable|closed)", r"we are closed",
    r"automated (assistant|message|reply|response)", r"\bauto-?(reply|response|generated)\b", r"business hours",
    r"reply (you )?shortly", r"will (respond|reply) (soon|shortly|within)", r"out of (the )?office",
    r"connect with you soon", r"we have received your (message|query|enquiry)", r"do not reply to this",
    r"for (appointments?|bookings?|orders?),? (please )?(call|visit|contact)", r"this is an auto",
    r"aapki jaankari ke liye", r"automated assistant hoon", r"team tak pahuncha", r"sujhaav hamari team",
    r"madad ke liye shukriya", r"message mil gaya", r"jaldi (hi )?(reply|jawab|sampark|contact)",
    r"abhi (available|uplabdh) nahi", "आपका संदेश", "जल्द ही", "अभी उपलब्ध नहीं", "स्वचालित",
]

OPT_OUT_PATTERNS = [
    r"\bstop (messaging|texting|sending|contacting|this|it|now|pls|please)\b", r"\b(pls|plz|please|ok|okay|just|kindly) stop\b",
    r"^\W*stop\W*$", r"\bunsubscribe\b", r"\bopt[ -]?out\b",
    r"\bdon'?t (message|text|contact|send|disturb|bother)\b", r"\bdo not (message|text|contact|send|disturb)\b",
    r"\bleave me alone\b", r"\bnot interested\b", r"\bno interest\b", r"\bremove (me|my number)\b",
    r"\bband karo\b", r"\bmat (bhejo|karo)\b", r"\bmessage mat\b", r"\bblock (you|kar|this)\b", r"\bpareshan mat\b",
    "मत भेज", "बंद करो", "बंद कीजिए", "परेशान मत", "मैसेज मत",
]

ABUSE_PATTERNS = [
    r"\bspam\b", r"\buseless\b", r"\bwaste of (my )?time\b", r"\bfraud\b", r"\bscam\b", r"\bidiot\b",
    r"\bstupid\b", r"\bbakwas\b", r"\bbekaar\b", r"\bpathetic\b", r"\bnonsense\b", r"\bshut up\b",
    r"\bbloody\b", r"\bdamn\b", r"\bget lost\b", "बकवास", "बेकार",
]

OOS_PATTERNS = [
    r"\bgst\b", r"\btax(es)?\b", r"\bitr\b", r"\blegal\b", r"\blawyer\b", r"\bloan\b", r"\binsurance\b",
    r"\baccounting\b", r"\bca\b", r"\bchartered accountant\b", r"\bvisa\b", r"\bpassport\b",
    r"\bcricket score\b", r"\bstock market\b", r"\bshare price\b", r"\bbitcoin\b", r"\bcrypto\b",
]

BUSY_PATTERNS = [
    r"\bbusy\b", r"\bin a meeting\b", r"\bcall (me )?later\b", r"\btalk later\b", r"\bafter ?some ?time\b",
    r"\bthodi der\b", r"\bkal baat\b", r"\bdriving\b", r"\bwith (a )?(patient|customer|client)s?\b",
    r"\bin (surgery|clinic hours|the middle)\b", "व्यस्त",
]

LATER_PATTERNS = [
    r"\bnot now\b", r"\bmaybe later\b", r"\blater\b", r"\bnext (week|month|year)\b", r"\babhi nahi\b",
    r"\bsome other time\b", r"\bafter (diwali|holi|eid|the season|this week|the festival|exams?)\b",
    r"\bright now\b", r"\bbaad mein\b", r"\bkuch din baad\b", "बाद में", "अभी नहीं",
]

DECLINE_PATTERNS = [
    r"^\W*(no|nope|nah|nahi|nahin|na|nai)\W*$", r"\bno,? thanks?\b", r"\bnahi chahiye\b", r"\bdon'?t need\b",
    r"\bdon'?t want\b", r"\bnot needed\b", r"\bnot required\b", r"\bzaroorat nahi\b", r"\bno need\b",
    r"\bnot for me\b", r"\bnot useful\b", r"^\W*(👎)+\W*$", "नहीं चाहिए", "ज़रूरत नहीं", "जरूरत नहीं",
]
DEVANAGARI_NO = ["नहीं", "नही"]

STRONG_COMMIT = [
    r"\blet'?s do it\b", r"\bdo it\b", r"\bgo ahead\b", r"\bproceed\b", r"\bsend (it|me|the|over|now)\b",
    r"\bplease send\b", r"\bdraft it\b", r"\bsign me up\b", r"\bi want to (join|do|start)\b", r"\bkar do\b",
    r"\bbhej do\b", r"\bbhejo\b", r"\bpublish\b", r"\bbook (it|me)\b", r"\bconfirm(ed)?\b", r"\bstart (it|now)\b",
    "भेज दो", "भेज दीजिए", "कर दो", "कर दीजिए",
]
COMMIT_PATTERNS = STRONG_COMMIT + [
    r"\bok(ay)?\b", r"\byes\b", r"\byeah\b", r"\byup\b", r"\bsure\b", r"\bhaa?n\b", r"\bha\b", r"\bchalega\b",
    r"\btheek hai\b", r"\bthik hai\b", r"\bdone\b", r"\binterested\b", r"\brenew\b", r"\bwhat'?s next\b",
    r"\bwhats next\b", r"\bsounds good\b", r"\bperfect\b", r"\bgreat\b", r"\bagreed\b",
    r"^\s*k+\s*$", r"^\s*[12]\s*$", r"^\s*(👍|✅|👌)+\s*$", "हाँ", "हां", "ठीक है", "जी हाँ", "जी हां", r"^\W*जी\W*$",
]

THANKS_PATTERNS = [r"\bthanks?\b", r"\bthank you\b", r"\bthx\b", r"\bshukriya\b", r"\bdhanya?vaa?d\b", "धन्यवाद", "शुक्रिया", "🙏"]
IDENTITY_PATTERNS = [r"\bwho (are|is) (you|this)\b", r"\bis this a (bot|robot|human|real person)\b", r"\bare you (a )?(bot|robot|human|real|ai)\b",
                     r"\bkaun (ho|hai|bol)\b", r"\baap kaun\b", "आप कौन"]
PRIVACY_PATTERNS = [r"\b(how|where) did you get my (number|contact|details)\b", r"\bwho gave you my (number|contact)\b",
                    r"\bnumber kaha(n)? se\b", r"\bnumber kaise mila\b"]
EDIT_PATTERNS = [r"\b(change|edit|modify|tweak|update|reword)\b.*\b(message|post|draft|text|line|wording|caption)\b",
                 r"\b(mention|highlight)\b", r"\binstead\b", r"\bchange\b.*\bto\b",
                 r"\b(add|include|remove)\b.*\b(to|in|from|into) (the |my )?(message|post|draft|text|flyer)\b",
                 r"\bbadal (do|dijiye)\b"]
DELEGATE_PATTERNS = [r"\bmy (son|daughter|brother|sister|husband|wife|manager|partner|staff|accountant|receptionist)\b",
                     r"\btalk to (him|her|them)\b", r"\b(he|she) (handles|manages|looks after)\b", r"\bhandles this\b"]
CHANNEL_PATTERNS = [r"\bcall me\b", r"\bgive me a call\b", r"\bphone (me|call)\b", r"\bemail me\b", r"\bcan you call\b"]
ALREADY_PATTERNS = [r"\balready (did|done|have|posted|sent|updated|verified|renewed|registered)\b", r"\bdid (it|this) (already|last)\b",
                    r"\bpehle (hi|se)\b", r"\bkar (chuka|chuki|diya hai)\b"]
RELEVANCE_PATTERNS = [r"\brelevant\b", r"\bapply to (me|my|us)\b", r"\buseful for\b", r"\bhow does (this|it) help\b",
                      r"\bwhy (should|does|would) (i|this)\b", r"\bmere (liye|kaam)\b", r"\bwhat'?s in it for\b"]
HEDGE_PATTERNS = [r"^\W*maybe\W*$", r"\bnot sure\b", r"\blet me think\b", r"\bi'?ll think\b", r"\bdekhte\b", r"\bsochta\b", r"\bsochti\b",
                  r"\bhmm+\b", r"\bpata nahi\b"]
PRICE_WORDS = r"\b(price|cost|costs|charges?|fees?|rate|kitna|kitne|how much|amount|paisa|paise|free)\b"
TIME_WORDS = r"\b(when|time|slot|kab|timing|date|tomorrow|today|kitne baje)\b"
QUESTION_WORDS = r"\b(what|how|why|when|where|which|who|kya|kaise|kab|kitna|kitne|kaun|details?|explain|tell me)\b"
HINDI_WORDS = r"\b(haan|nahi|kya|karo|kar|hai|hain|chahiye|theek|thik|acha|accha|bhej|kaise|kab|kitna|mujhe|aap|abhi|baad|mera|meri)\b"


def _any(patterns: List[str], text: str) -> bool:
    # Devanagari words are matched as substrings (\b is unreliable across matras); anchored patterns stay regex.
    return any((re.search(p, text) if (p.isascii() or p.startswith("^")) else p in text) for p in patterns)


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    trigger_id: Optional[str] = None
    trigger_kind: Optional[str] = None
    turns: List[Dict[str, Any]] = field(default_factory=list)
    auto_reply_count: int = 0
    stage: str = "initial"           # initial -> action_mode (draft delivered) -> done (executed) | paused | closed
    closed_reason: Optional[str] = None
    apologized: bool = False
    action_count: int = 0
    edits: List[str] = field(default_factory=list)


# =============================================================================
# CLASSIFIER
# =============================================================================

def speaks_hindi(message: str) -> bool:
    return bool(re.search(HINDI_WORDS, message.lower()) or re.search(r"[ऀ-ॿ]", message))


def is_auto_reply(message: str, history: List[Dict[str, Any]]) -> bool:
    msg = message.lower().strip()
    if _any(AUTO_REPLY_PATTERNS, msg):
        return True
    same = sum(1 for t in history if t.get("from") in ("merchant", "customer")
               and str(t.get("msg", "")).strip().lower() == msg)   # history already includes this turn
    return same >= (2 if len(msg) > 25 else 3)


# ---- generalising layer: normalisation + word-combination rules -------------
_TOKEN_MAP = {
    "okk": "ok", "okie": "ok", "okey": "ok", "kk": "ok", "okkay": "ok", "yess": "yes", "yea": "yes", "yep": "yes",
    "yeah": "yes", "yup": "yes", "pls": "please", "plz": "please", "msg": "message", "msgs": "messages",
    "u": "you", "ur": "your", "nhi": "nahi", "nai": "nahi", "nahin": "nahi", "thx": "thanks", "thnx": "thanks",
    "ty": "thanks", "tysm": "thanks", "dont": "don't", "wont": "won't", "cant": "can't", "whos": "who's",
}


def normalize(text: str) -> str:
    t = (text or "").lower().strip()
    t = re.sub(r"([a-z])\1{2,}", r"\1", t)                      # "yesss" -> "yes", "okkkk" -> "ok"
    t = re.sub(r"[a-z']+", lambda mt: _TOKEN_MAP.get(mt.group(0), mt.group(0)), t)
    t = re.sub(r"\b(who|what|where|how)'s\b", r"\1 is", t)
    return t


NEGATION = r"\b(no|not|nah|nahi|never|don't|do not|won't)\b"
NEED_WORDS = r"\b(need|want|require|required|chahiye|zarurat|zaroorat|interested|useful|for (me|us))\b"
MESSAGE_WORDS = r"\b(messages?|texts?|texting|sending|spam|spamming|whatsapps?|bhej\w*)\b"
POSITIVE_NEGATION = r"\b(no (problem|issue|issues|worries)|not bad|why not|no doubt)\b"
TIME_REF = (r"\b(tomorrow|kal|parso|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next|after|season|"
            r"weekend|week|month|tonight|evening)\b")
DEFER_VERBS = r"\b(ping|remind|talk|discuss|revert|get back|come back|check|do (this|it)|message me|contact me|maybe)\b"
GENERIC_OPT_OUT = [
    r"\b(stop|quit|enough|fed up|tired of)\b.*" + MESSAGE_WORDS, MESSAGE_WORDS + r".*\b(stop|band)\b",
    r"\b(na|mat|nahi)\s+(bhej\w*|message|karo message)", r"\bmessages?\s+(na|mat|nahi)\b",
]
GENERIC_COMMIT = [r"\bgo (with|for) it\b", r"\blet'?s go\b", r"\bbook (the|it|me|that|this)\b", r"\bsure thing\b",
                  r"\bsend (the|over|across)\b", r"\bkar dijiye\b"]
GENERIC_HEDGE = [r"\bthink (about|over) it\b", r"\bwill think\b", r"\blet me (see|check|think)\b", r"\bconsider\b"]
GENERIC_AUTO = [r"\bgreetings from\b", r"\bour (executive|representative|team|staff|agent)s? (will|shall)\b",
                r"\bwill revert\b", r"\bplease leave (a|your) message\b", r"\bwe('| a)re away\b",
                r"\byou('ve| have) reached\b", r"\b(we|i) (will|'ll) (call|get|reach) (you )?back\b",
                r"\b(currently|presently) (unavailable|not available|away)\b", r"\bwill (respond|reply|revert) when\b"]
# "stop" is an opt-out unless it is clearly about stopping a business thing ("stop the old offer")
BARE_STOP = r"\bstop\b(?!\s+(the|this|that|old|running|my|our|current)\b)(?!\s+\w*\s*(offer|promo|campaign|discount|ad|ads|post|sale)\b)"


def classify(message: str, history: Optional[List[Dict[str, Any]]] = None) -> str:
    raw = (message or "").strip()
    m = normalize(raw)
    if not raw:
        return "EMPTY"
    if is_auto_reply(raw, history or []) or _any(GENERIC_AUTO, m):
        return "AUTO_REPLY"
    strong = _any(STRONG_COMMIT, m) or _any(GENERIC_COMMIT, m)
    later = _any(LATER_PATTERNS, m) or (bool(re.search(TIME_REF, m)) and bool(re.search(DEFER_VERBS, m)) and not strong)
    negated = bool(re.search(NEGATION, m)) and not re.search(POSITIVE_NEGATION, m)
    if _any(GENERIC_OPT_OUT, m) or (negated and re.search(NEED_WORDS, m) and re.search(MESSAGE_WORDS, m)) \
            or (re.search(BARE_STOP, m) and not strong):
        return "OPT_OUT"
    if _any(OPT_OUT_PATTERNS, m):
        # "not interested right now, maybe next month" is a deferral, not an opt-out
        if later and re.search(r"\bnot interested\b", m) and not re.search(r"\bstop|unsubscribe|don'?t message\b", m):
            return "LATER"
        return "OPT_OUT"
    if _any(ABUSE_PATTERNS, m):
        return "ABUSE"
    if _any(OOS_PATTERNS, m) and re.search(r"\b(gst|tax|itr|ca|legal|lawyer|loan|insurance|accounting|visa|passport|"
                                           r"cricket|stock|share|bitcoin|crypto)\b", m):
        return "OUT_OF_SCOPE"
    if _any(BUSY_PATTERNS, m):
        return "BUSY"
    if later and not strong:
        return "LATER"
    if (_any(DECLINE_PATTERNS, m) or (any(n in raw for n in DEVANAGARI_NO) and not _any(COMMIT_PATTERNS, m))
            or (negated and re.search(NEED_WORDS, m))) and not strong:
        return "DECLINE"
    if _any(IDENTITY_PATTERNS, m):
        return "IDENTITY"
    if _any(PRIVACY_PATTERNS, m):
        return "PRIVACY"
    if _any(THANKS_PATTERNS, m) and not _any(COMMIT_PATTERNS, m) and "?" not in m and len(m.split()) <= 6:
        return "THANKS"
    if _any(CHANNEL_PATTERNS, m):
        return "CHANNEL"
    if _any(EDIT_PATTERNS, m) and not re.fullmatch(r"\W*(yes|ok|okay|haan)\W*", m):
        return "EDIT"
    if _any(DELEGATE_PATTERNS, m):
        return "DELEGATE"
    if _any(ALREADY_PATTERNS, m):
        return "ALREADY_DONE"
    if (_any(HEDGE_PATTERNS, m) or _any(GENERIC_HEDGE, m)) and not strong:
        return "HEDGE"
    if (strong or _any(COMMIT_PATTERNS, m)) and not (re.search(PRICE_WORDS, m) and "?" in m and not strong):
        return "COMMIT"
    if _any(RELEVANCE_PATTERNS, m):
        return "RELEVANCE"
    if re.search(PRICE_WORDS, m):
        return "PRICE"
    if re.search(TIME_WORDS, m) and ("?" in m or re.search(QUESTION_WORDS, m)):
        return "TIME"
    if "?" in m or re.search(QUESTION_WORDS, m):
        return "QUESTION"
    if _any(HEDGE_PATTERNS, m) or negated:
        return "HEDGE"  # safety net: never answer an unclear negative with a sales pitch
    return "OTHER"


# Back-compat helpers
def is_hostile(message: str) -> bool:
    return classify(message) == "OPT_OUT"


def is_commitment(message: str) -> bool:
    return classify(message) == "COMMIT"


def is_wait(message: str) -> bool:
    return classify(message) in ("BUSY", "LATER")


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


def alt_fact(c: Optional[Ctx], avoid: str) -> str:
    if not c:
        return ""
    for cand in (c.perf_line(), c.peer_line(),
                 f"Your live offer is '{c.active_offers()[0]}'." if c.active_offers() else ""):
        if cand and cand != avoid:
            return cand
    return ""


def what_i_do(c: Optional[Ctx]) -> str:
    kind = str((c.trigger if c else {}).get("kind") or "")
    return {
        "research_digest": "send the 2-min abstract + a patient WhatsApp draft",
        "regulation_change": "send a 1-page compliance checklist",
        "cde_opportunity": "send the registration details",
        "supply_alert": "pull the affected-customer list + a replacement message",
        "renewal_due": "send the renewal link",
        "curious_ask_due": "turn your answer into a Google post",
        "active_planning_intent": "turn the draft into a post + WhatsApp flyer",
    }.get(kind, "prepare the draft")


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
        return f"Booked ✅ {slot + ' at ' if slot else ''}{c.m_name}. We'll send a reminder the day before. See you then!".replace("  ", " ")

    name = c.owner or ""
    lead = f"Done{', ' + name if name else ''} — "
    aud = c.audience()
    offers = c.active_offers()
    it = _item(c)

    if kind in ("research_digest", "research_digest_release", "category_research_digest_release") and it:
        return (f"{lead}here's the gist of {it.get('source', 'the paper')}: {it.get('summary', it.get('title', ''))} "
                f"Takeaway: {it.get('actionable', 'worth reviewing for your case-mix')}\n\n"
                f"{aud.capitalize()} WhatsApp draft: \"New research on {human(it.get('patient_segment')) or 'your care'}: "
                f"{first_sentence(it.get('title'))} Reply to book a check.\"\n\n"
                f"Reply CONFIRM and I'll schedule it to your {aud} list.")
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

    where = f" in {c.locality}" if c.locality else ""
    draft = f"{c.m_name}{where}{' — ' + offers[0] if offers else ''}. Walk in or message us to book."
    body = (f"{lead}drafting it now{' featuring ' + repr(offers[0]) if offers else ''}.\n\n"
            f"Post draft: \"{draft}\"\n\n")
    body += "CONFIRM reply karein, aaj hi live kar dungi." if hi else "Reply CONFIRM and it goes live today."
    return body


def answer_question(msg: str, c: Optional[Ctx], from_role: str) -> str:
    m = msg.lower()
    if c is not None:
        offers = c.active_offers()
        if re.search(PRICE_WORDS, m):
            amt = fmt_int(c.payload.get("renewal_amount"))
            if amt and c.trigger.get("kind") == "renewal_due":
                return f"Renewal is ₹{amt} for the {c.payload.get('plan', 'current')} plan. Reply YES and I'll send the link."
            if from_role == "merchant" and c.trigger.get("kind") not in ("renewal_due",):
                extra = f" Your current offers: {'; '.join(offers[:3])}." if offers else ""
                return f"This costs you nothing — it's part of your magicpin listing support.{extra} Reply YES and I'll get started."
            if offers:
                return f"Current pricing: {'; '.join(offers[:3])}. Reply YES to go ahead."
            return "There's no charge for this. Reply YES and I'll get started."
        if re.search(TIME_WORDS, m):
            slots = c.slot_labels() or c.slot_labels("next_session_options")
            if slots:
                return f"Available: {' or '.join(slots)}. Reply 1 or 2 to pick one."
            return "I can have it ready within 2 hours of your YES. Reply YES and I'll start now."
        fact = core_fact(c)
        if fact:
            return f"Short version: {fact} Reply YES and I'll take care of the rest."
    return "Short version: I prepare it, you approve it, nothing goes live without your OK. Reply YES to start."


def answer_relevance(c: Optional[Ctx]) -> str:
    if not c:
        return answer_question("", c, "merchant")
    it = _item(c)
    if it:
        seg = human(it.get("patient_segment"))
        tie = ""
        if it.get("patient_segment") == "high_risk_adults" and "high_risk_adult_cohort" in c.signals:
            tie = " Your profile flags a high-risk adult cohort, so yes — directly."
        head = f"It matters most for {seg}.{tie}" if seg else "Here's why it matters for you."
        act = first_sentence(it.get("actionable"))
        return f"{head} {act} Reply YES and I'll {what_i_do(c)}.".replace("  ", " ")
    fact = core_fact(c)
    return f"For {c.m_name or 'you'} specifically: {fact} Reply YES and I'll {what_i_do(c)}."


def extract_edit(msg: str) -> str:
    m = re.search(r"(?:mention|add|include|say|highlight|put)\s+(.+?)\s*[?.!]*$", msg, flags=re.I)
    if not m:
        m = re.search(r"(?:change|update|replace)\s+(?:it|the \w+|this)?\s*(?:to|with)\s+(.+?)\s*[?.!]*$", msg, flags=re.I)
    if not m:
        m = re.search(r"(.+?)\s+instead\b", msg, flags=re.I)
    return (m.group(1) if m else msg).strip().strip("\"'")[:120]


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
    hi = speaks_hindi(msg)
    c = Ctx(category_context or {}, merchant_context or {}, trigger_context or {}, customer_context, now) \
        if (merchant_context or trigger_context) else None
    owner = c.owner if c else ""
    name = c.m_name if c and c.m_name else "your business"
    prior = {str(t.get("msg", "")) for t in history if t.get("from") == "vera"}
    intent = classify(msg, history)

    def send(body: str, cta: str, why: str) -> dict:
        if body in prior:  # anti-repetition: never the same text twice in one conversation
            alt = alt_fact(c, core_fact(c))
            body = (f"To add one more data point: {alt} Reply YES and I'll take it from here." if alt else
                    "Happy to go deeper on any part. Reply YES and I'll prepare it — nothing goes live without your OK.")
            if body in prior:
                body = "I'm ready whenever you are — one YES and I'll share the draft here."
        return {"action": "send", "body": body, "cta": cta, "rationale": f"[{intent}] {why}"}

    def end(why: str, reason: Optional[str] = None) -> dict:
        if state:
            state.stage = "closed"
            state.closed_reason = reason or state.closed_reason
        return {"action": "end", "rationale": f"[{intent}] {why}"}

    def wait(seconds: int, why: str) -> dict:
        if state:
            state.stage = "paused"
        return {"action": "wait", "wait_seconds": int(seconds), "rationale": f"[{intent}] {why}"}

    if intent == "EMPTY":
        return wait(1800, "Empty message; waiting for a real reply.")

    # 1. Auto-reply ladder: nudge once -> wait 24h -> end
    if intent == "AUTO_REPLY":
        count = max(state.auto_reply_count if state else 0, merchant_auto_count) + 1
        if state:
            state.auto_reply_count = count
        if count == 1:
            body = ("Lagta hai yeh auto-reply hai. Jab owner dekhein, bas 'Yes' reply kar dein — baaki main sambhal lungi."
                    if hi or (c and c.hi) else
                    "Looks like an auto-reply. When the owner sees this, just reply 'Yes' and I'll handle the rest.")
            return send(body, "binary", "First auto-reply detected; one short prompt to reach the owner.")
        if count == 2:
            return wait(86400, "Auto-reply again — owner not at phone. Backing off 24h instead of burning turns.")
        return end(f"Auto-reply {count}x with no human response; exiting gracefully.", "auto_reply")

    # A human is here. Respect an earlier close unless they clearly re-engage.
    if state and state.stage == "closed":
        # After a STOP only a substantive message re-opens the thread: "hello?" or "?" is not consent.
        substantive = len(re.findall(r"\w+", msg)) >= 3
        reengage = intent in ("COMMIT", "EDIT") or (substantive and intent in (
            "QUESTION", "PRICE", "TIME", "RELEVANCE", "IDENTITY", "PRIVACY", "OUT_OF_SCOPE"))
        if state.closed_reason == "opt_out" and not reengage:
            return end("Merchant opted out earlier; staying silent.")
        if state.closed_reason == "done" and intent in ("THANKS", "OTHER", "COMMIT"):
            return end("Work already delivered; nothing left to say.")
        state.stage = "initial"
    if state and intent != "OTHER":
        state.auto_reply_count = 0  # only a clearly-human reply resets the ladder

    if intent == "OPT_OUT":
        return end("Merchant asked us to stop / not interested; ending immediately and suppressing further sends.", "opt_out")

    if intent == "ABUSE":
        if state and state.apologized:
            return end("Repeated hostility after apology; exiting gracefully.", "opt_out")
        if state:
            state.apologized = True
        body = (f"Sorry{', ' + owner if owner else ''} — I'll only message when there's something concrete for {name}. "
                f"Reply STOP anytime and I'll stop completely.")
        return send(body, "none", "Hostile tone; apologised, offered a clean opt-out, no hard sell.")

    if intent == "OUT_OF_SCOPE":
        fact = core_fact(c)
        back = f" Coming back to what I flagged: {fact}" if fact else ""
        return send(f"That one's outside what I can do — your CA is the right person for it.{back} "
                    f"Reply YES and I'll {what_i_do(c)}.", "binary",
                    "Out-of-scope ask declined politely; steered back to the original trigger.")

    if intent == "BUSY":
        return wait(1800, "Merchant is busy; backing off 30 min.")
    if intent == "LATER":
        return wait(86400, "Merchant said not now; backing off 24h.")
    if intent == "DECLINE":
        return end("Merchant declined; exiting without pushing.", "declined")

    if intent == "THANKS":
        if state and state.stage in ("done", "action_mode"):
            if state:
                state.closed_reason = "done"
            return end("Merchant thanked us after delivery; conversation complete.", "done")
        body = ("Aapka swagat hai! Jab chahein, bas YES reply karein — main taiyaar kar dungi." if hi else
                f"Anytime! Whenever you're ready, reply YES and I'll {what_i_do(c)}.")
        return send(body, "binary", "Polite acknowledgement; one low-friction next step.")

    if intent == "IDENTITY":
        if from_role == "customer":
            return send(f"This is {name}'s booking assistant — we message only about your visits and offers. "
                        f"Reply STOP anytime to opt out.", "none", "Answered who we are, honestly.")
        fact = core_fact(c)
        return send(f"I'm Vera, magicpin's assistant for {name}. I watch your listing's numbers and flag what's worth acting on — "
                    f"nothing goes live without your OK. {fact} Reply YES and I'll {what_i_do(c)}.".replace("  ", " "),
                    "binary", "Answered identity honestly (assistant, not a human), then restated value.")

    if intent == "PRIVACY":
        who = "a customer of" if from_role == "customer" else "the owner contact for"
        return send(f"You're registered as {who} {name} on magicpin — that's the only reason I have this number, and it isn't shared. "
                    f"Reply STOP anytime and I won't message again.", "none", "Privacy question answered plainly with an opt-out.")

    if intent == "EDIT":
        want = extract_edit(msg)
        if state:
            state.edits.append(want)
            state.stage = "action_mode"
        if from_role == "customer":
            return send(f"Noted — {want}. We'll confirm it shortly.", "none", "Customer change request acknowledged.")
        return send(f"Updated ✅ — the draft now includes: {want}. Reply CONFIRM and it goes live.", "binary",
                    "Applied the merchant's edit to the draft and asked for one confirmation.")

    if intent == "DELEGATE":
        return send("Sure — just forward this chat to them. They can reply YES here and I'll take it from there.", "none",
                    "Merchant delegated; made hand-off effortless without asking for anyone's number.")

    if intent == "ALREADY_DONE":
        return end("Merchant already did it; closing without repeating the pitch.", "done") if not c else \
            send("Nice — then I'll mark that done and won't bring it up again.", "none", "Merchant already acted; acknowledged and stood down.")

    if intent == "CHANNEL":
        fact = core_fact(c)
        return send(f"I can only help over WhatsApp, so I'll keep it short: {fact} Reply YES and I'll {what_i_do(c)} — no call needed."
                    .replace("  ", " "), "binary", "Channel switch declined; compressed the value into one message.")

    if intent == "COMMIT":
        if state:
            state.action_count += 1
        if state and state.stage == "action_mode":
            state.stage = "done"
            state.closed_reason = "done"
            body = ("Done — live kar diya ✅. 7 din mein results share karungi." if hi else
                    "Done — it's live ✅. I'll share how it performs in 7 days.")
            return send(body, "none", "Merchant confirmed the delivered draft; executed and closed the loop.")
        if state and state.stage == "done":
            return end("Already executed; nothing pending.", "done")
        if state:
            state.stage = "action_mode"
        return send(action_body(c, hi, from_role), "binary" if from_role != "customer" else "none",
                    "Merchant committed; switched from pitching to delivering the concrete artifact.")

    if intent == "RELEVANCE":
        return send(answer_relevance(c), "binary", "Answered 'does this apply to me' from the digest item + merchant signals.")

    if intent in ("PRICE", "TIME", "QUESTION"):
        return send(answer_question(msg, c, from_role), "binary", "Answered the question from pushed context, then one CTA.")

    if intent == "HEDGE":
        body = ("Koi pressure nahi. Main ek draft bana deti hoon — bina aapke OK ke kuch live nahi hoga. Dekhna ho to YES reply karein."
                if hi else "No pressure. I'll prepare a draft you can look at — nothing goes live without your OK. Reply YES to see it.")
        return send(body, "binary", "Hesitation; lowered the stakes to a no-commitment preview.")

    # OTHER
    if state and state.stage == "action_mode":
        return send("Noted — updating the draft with that. The revised version lands here in 2 minutes.", "none",
                    "Merchant gave input while in action mode; applying it.")
    fact = core_fact(c)
    return send(f"Got it. {fact + ' ' if fact else ''}Reply YES and I'll {what_i_do(c)} — nothing goes live without your OK.",
                "binary", "Acknowledged and restated one concrete, grounded next step.")
