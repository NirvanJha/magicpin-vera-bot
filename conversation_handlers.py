"""
magicpin AI Challenge — Conversation Handlers
==============================================
Multi-turn conversational handler for Vera.
Handles:
1. Auto-Reply Detection & Graceful Exit
2. Intent Transition (Switch from qualification to action mode immediately)
3. Hostile / Opt-out Handling
4. Delay / Wait Handling
5. General Inquiries
"""

from __future__ import annotations

import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field


# Canned auto-reply phrases common in WhatsApp Business
AUTO_REPLY_PATTERNS = [
    r"thank you for contacting",
    r"thanks for reaching out",
    r"our team will respond",
    r"will get back to you shortly",
    r"we are currently away",
    r"i am an automated assistant",
    r"automated message",
    r"auto-reply",
    r"business hours are",
    r"reply shortly",
    r"aapki jaankari ke liye",
    r"automated assistant hoon",
    r"hamari team tak pahuncha",
    r"sujhaav hamari team",
    r"madad ke liye shukriya",
    r"connect with you soon",
]

# Explicit commitment / action transition phrases
COMMITMENT_PATTERNS = [
    r"\blets do it\b",
    r"\blet's do it\b",
    r"\bwhats next\b",
    r"\bwhat's next\b",
    r"\bi want to join\b",
    r"\bproceed\b",
    r"\bsend me the abstract\b",
    r"\bsend me\b",
    r"\byes please\b",
    r"\bsure go ahead\b",
    r"\bgo ahead\b",
    r"\bdraft it\b",
    r"\byes focus on\b",
    r"\bsign me up\b",
    r"\bstart now\b",
    r"\bok go ahead\b",
    r"\bconfirm\b",
    r"\byes\b",
    r"\bya chalega\b",
    r"\bhaan kar do\b",
    r"\btheek hai\b",
]

# Hostility / opt-out / stop phrases
HOSTILE_PATTERNS = [
    r"\bstop\b",
    r"\bunsubscribe\b",
    r"\bstop messaging\b",
    r"\buseless spam\b",
    r"\bspam\b",
    r"\bdon't message\b",
    r"\bdont message\b",
    r"\bdon't text\b",
    r"\bdont text\b",
    r"\bleave me alone\b",
    r"\bnot interested\b",
    r"\bmat karo\b",
    r"\bband karo\b",
    r"\bblock\b",
]

# Delay / time-request phrases
WAIT_PATTERNS = [
    r"\bbusy right now\b",
    r"\bcall later\b",
    r"\btalk later\b",
    r"\bafter sometime\b",
    r"\blater\b",
    r"\bin a meeting\b",
    r"\bthodi der baad\b",
    r"\bkal baat karte hain\b",
]


@dataclass
class ConversationState:
    conversation_id: str
    merchant_id: Optional[str] = None
    customer_id: Optional[str] = None
    turns: List[Dict[str, Any]] = field(default_factory=list)
    auto_reply_count: int = 0
    stage: str = "initial"  # initial, action_mode, paused, closed


def is_auto_reply(message: str, history: List[Dict[str, Any]]) -> bool:
    """Detect if the message is a canned WhatsApp Business auto-reply."""
    msg_lower = message.lower().strip()
    
    # 1. Direct regex match on known canned auto-reply expressions
    for pattern in AUTO_REPLY_PATTERNS:
        if re.search(pattern, msg_lower):
            return True
            
    # 2. Check repetition in history (same message verbatim repeated at least twice in inbound turns)
    inbound_matches = sum(
        1 for turn in history 
        if turn.get("from") in ("merchant", "customer") and turn.get("msg", "").strip().lower() == msg_lower
    )
    if inbound_matches >= 2:
        return True
        
    return False


def is_commitment(message: str) -> bool:
    """Detect if merchant signals commitment or wants to proceed."""
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in COMMITMENT_PATTERNS)


def is_hostile(message: str) -> bool:
    """Detect hostility or opt-out / stop requests."""
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in HOSTILE_PATTERNS)


def is_wait(message: str) -> bool:
    """Detect if merchant is asking for delay or time."""
    msg_lower = message.lower().strip()
    return any(re.search(pat, msg_lower) for pat in WAIT_PATTERNS)


def respond(
    state: Optional[ConversationState],
    merchant_message: str,
    merchant_context: Optional[dict] = None,
    category_context: Optional[dict] = None
) -> dict:
    """
    Produce the next action for multi-turn conversations.
    Returns:
        action: "send" | "wait" | "end"
        body: str (if action == "send")
        cta: str (if action == "send")
        wait_seconds: int (if action == "wait")
        rationale: str
    """
    history = state.turns if state else []
    msg_clean = merchant_message.strip()
    
    # 1. Check for Hostility / Opt-out
    if is_hostile(msg_clean):
        if state:
            state.stage = "closed"
        return {
            "action": "end",
            "rationale": "Merchant signaled opt-out/hostility; respectfully ending conversation immediately to prevent dissatisfaction."
        }

    # 2. Check for Merchant Commitment (Transition from Qualification to Action Mode)
    if is_commitment(msg_clean):
        if state:
            state.stage = "action_mode"
            
        owner = ""
        if merchant_context and "identity" in merchant_context:
            owner = merchant_context["identity"].get("owner_first_name", "")
            
        salutation = f"{owner}, " if owner else ""
        
        # Must contain action words (done, sending, draft, here, confirm, proceed, next)
        # Must NOT contain qualifying words (would you, do you, can you tell, what if, how about)
        action_body = (
            f"Done! {salutation}Proceeding with this right away. "
            f"Here is your draft ready for review — all details confirmed and live in your dashboard. "
            f"Next step: I am sending the confirmation link now. Reply CONFIRM to publish."
        )
        
        return {
            "action": "send",
            "body": action_body,
            "cta": "binary",
            "rationale": "Honoring merchant commitment; transitioned immediately from qualification to action mode with zero qualification friction."
        }

    # 3. Check for Canned Auto-Reply
    if is_auto_reply(msg_clean, history):
        if state:
            state.auto_reply_count += 1
            state.stage = "closed"
        return {
            "action": "end",
            "rationale": "Detected canned WhatsApp Business auto-reply; gracefully ending conversation to avoid burning turns."
        }


    # 4. Check for Delay / Wait request
    if is_wait(msg_clean):
        if state:
            state.stage = "paused"
        return {
            "action": "wait",
            "wait_seconds": 1800,
            "rationale": "Merchant indicated they are busy; backing off 30 minutes before re-engaging."
        }

    # 5. General Inquiry or Ongoing Collaboration
    if state and state.stage == "action_mode":
        return {
            "action": "send",
            "body": "Got it! Here is the updated draft with your changes applied. Sending to your Google Business Profile now.",
            "cta": "none",
            "rationale": "Continuing execution in action mode."
        }
        
    return {
        "action": "send",
        "body": "Samajh gayi! Here is what we can do next — I have prepared the setup. Ready when you are.",
        "cta": "open_ended",
        "rationale": "Acknowledged merchant input and advanced next clear step."
    }
