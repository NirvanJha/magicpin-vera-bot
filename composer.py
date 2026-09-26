"""
Grounded message composer for Vera.

Rule #1: every fact in a body (names, numbers, prices, dates, sources) must come
from a pushed context (category / merchant / trigger / customer). When a value is
missing, the sentence that needed it is dropped instead of being filled with a
made-up default.
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Tuple


# =============================================================================
# SMALL HELPERS
# =============================================================================

def _d(x: Any) -> dict:
    return x if isinstance(x, dict) else {}


def _l(x: Any) -> list:
    return x if isinstance(x, list) else []


def _num(x: Any) -> Optional[float]:
    """Numbers from context, or None for anything a merchant couldn't plausibly verify (NaN, inf, 1e308, bools)."""
    try:
        if isinstance(x, bool):
            return None
        v = float(x)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) and abs(v) < 1e10 else None


def _txt(x: Any, limit: int = 60) -> str:
    """Display-safe short string: names/localities longer than `limit` are cut at a word boundary."""
    if not isinstance(x, str):
        return ""
    x = " ".join(x.split())
    if len(x) <= limit:
        return x
    cut = x[:limit].rsplit(" ", 1)[0]
    return cut if len(cut) >= limit // 2 else x[:limit]


def parse_dt(s: Any) -> Optional[datetime]:
    if not isinstance(s, str) or not s:
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def fmt_date(dt: Optional[datetime]) -> str:
    return f"{dt.day} {dt.strftime('%b')}" if dt else ""


def pct(frac: Any) -> Optional[int]:
    """0.3 -> 30, -0.5 -> 50, 38 -> 38 (already a percentage)."""
    v = _num(frac)
    if v is None:
        return None
    v = abs(v)
    return int(round(v if v > 1.5 else v * 100))


def fmt_int(x: Any) -> Optional[str]:
    v = _num(x)
    return f"{int(v):,}" if v is not None else None


def human(s: Any) -> str:
    """'6_month_cleaning' -> '6-month cleaning', 'delivery_late' -> 'delivery late'."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"(\d)_?(month|day|week|year)s?(?![a-z])", r"\1-\2", s)
    return s.replace("_", " ").strip()


def first_sentence(text: Any) -> str:
    if not isinstance(text, str) or not text.strip():
        return ""
    t = text.strip()
    for m in re.finditer(r"[.!?](?=\s+[A-Z0-9₹\"'])", t):
        words = t[:m.start()].split()
        prev = words[-1] if words else ""
        if re.fullmatch(r"(Dr|Mr|Mrs|Ms|St|vs|No|Rs|approx|[A-Z])", prev):
            continue
        return t[:m.end()]
    return t if t.endswith((".", "!", "?")) else t + "."


def price_in(title: str) -> Optional[int]:
    m = re.search(r"₹\s*([\d,]+)", title or "")
    return int(m.group(1).replace(",", "")) if m else None


def clean(body: str) -> str:
    body = re.sub(r"[ \t]+", " ", body)
    body = re.sub(r" +([.,?!])", r"\1", body)
    body = re.sub(r"\.\.+", ".", body)
    body = re.sub(r"\(\s*\)", "", body)
    return body.strip()


def join(*parts: Optional[str]) -> str:
    return clean(" ".join(p.strip() for p in parts if p and p.strip()))


# =============================================================================
# CONTEXT WRAPPER
# =============================================================================

def _clean(obj: Any, depth: int = 0) -> Any:
    """Absurd / non-finite numbers become None everywhere, so they can never be rendered."""
    if depth > 30:
        return None
    if isinstance(obj, float) and not (math.isfinite(obj) and abs(obj) < 1e10):
        return None
    if isinstance(obj, int) and not isinstance(obj, bool) and abs(obj) >= 10 ** 10:
        return None
    if isinstance(obj, dict):
        return {k: _clean(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean(v, depth + 1) for v in obj]
    return obj


def _strs(items: Any) -> List[str]:
    """Only plain text / integer items from a list — never str() of a dict or list."""
    return [str(x) for x in _l(items) if isinstance(x, str) or (isinstance(x, int) and not isinstance(x, bool))]


class Ctx:
    def __init__(self, category: dict, merchant: dict, trigger: dict,
                 customer: Optional[dict], now: Optional[datetime],
                 exclude_items: Iterable[str] = ()):
        self.category = _clean(_d(category))
        self.merchant = _clean(_d(merchant))
        self.trigger = _clean(_d(trigger))
        self.customer = _clean(_d(customer)) if customer else None
        self.payload = _d(self.trigger.get("payload"))
        self.now = now
        self.exclude_items = set(exclude_items or ())

        slug = self.category.get("slug")
        self.cat = slug if isinstance(slug, str) else (self.merchant.get("category_slug")
                                                       if isinstance(self.merchant.get("category_slug"), str) else "")
        ident = _d(self.merchant.get("identity"))
        self.m_name = _txt(ident.get("name"), 60)
        self.owner = _txt(ident.get("owner_first_name"), 30)
        self.locality = _txt(ident.get("locality"), 40)
        self.city = _txt(ident.get("city"), 30)
        self.m_langs = [str(x).lower() for x in _l(ident.get("languages"))]
        self.perf = _d(self.merchant.get("performance"))
        self.signals = [str(s) for s in _l(self.merchant.get("signals"))]
        self.peer = _d(self.category.get("peer_stats"))
        self.hi = "hi" in self.m_langs
        self.used_item: Optional[str] = None

    # ---- merchant-facing helpers -------------------------------------------
    def sal(self) -> str:
        if self.cat == "dentists":
            name = re.sub(r"^dr\.?\s*", "", self.owner, flags=re.I)
            return f"Dr. {name}" if name else "Doctor"
        if self.owner:
            return f"Hi {self.owner}"
        return f"Hi {self.m_name} team" if self.m_name else "Hi"

    def where(self) -> str:
        return f" in {self.locality}" if self.locality else ""

    def active_offers(self) -> List[str]:
        return [o.get("title") for o in _l(self.merchant.get("offers"))
                if _d(o).get("status") == "active" and o.get("title")]

    def catalog_offer(self, *keywords: str) -> Optional[str]:
        for o in _l(self.category.get("offer_catalog")):
            t = _d(o).get("title") or ""
            if not keywords or any(k.lower() in t.lower() for k in keywords):
                return t or None
        return None

    def offer_matching(self, *keywords: str) -> Optional[str]:
        offers = self.active_offers()
        for t in offers:
            if any(k.lower() in t.lower() for k in keywords):
                return t
        return None

    def noun(self, kind: str) -> str:
        table = {
            "visit": {"dentists": "dental check-up", "salons": "next salon appointment", "gyms": "next session",
                      "pharmacies": "monthly health check", "restaurants": "next visit"},
            "trial": {"dentists": "consultation", "salons": "trial", "gyms": "trial session",
                      "pharmacies": "first order", "restaurants": "first visit"},
            "appt": {"restaurants": "table reservation", "pharmacies": "pickup", "gyms": "session"},
            "place": {"dentists": "dental clinic", "salons": "salon", "gyms": "gym", "pharmacies": "pharmacy",
                      "restaurants": "restaurant"},
        }
        default = {"visit": "next visit", "trial": "trial", "appt": "appointment", "place": "business"}[kind]
        return table.get(kind, {}).get(self.cat, default)

    def audience(self) -> str:
        return {"dentists": "patient", "pharmacies": "customer", "gyms": "member"}.get(self.cat, "customer")

    def digest(self, item_id: Any = None, kinds: Tuple[str, ...] = ()) -> Optional[dict]:
        item = self._digest(item_id, kinds)
        if item:
            self.used_item = item.get("id")
        return item

    def _digest(self, item_id: Any = None, kinds: Tuple[str, ...] = ()) -> Optional[dict]:
        items = [_d(i) for i in _l(self.category.get("digest"))]
        if item_id:
            for i in items:
                if i.get("id") == item_id:
                    return i
        if kinds:
            fresh = [i for i in items if i.get("kind") in kinds and i.get("id") not in self.exclude_items]
            if fresh:
                return fresh[-1]  # newest pushed item last
            for i in items:
                if i.get("kind") in kinds:
                    return i
        return None

    def stale_posts_days(self) -> Optional[int]:
        for s in self.signals:
            m = re.match(r"stale_posts:(\d+)d", s)
            if m:
                return int(m.group(1))
        return None

    def perf_line(self) -> str:
        views, calls, ctr = fmt_int(self.perf.get("views")), fmt_int(self.perf.get("calls")), _num(self.perf.get("ctr"))
        bits = []
        if views:
            bits.append(f"{views} views")
        if calls:
            bits.append(f"{calls} calls")
        if ctr is not None:
            bits.append(f"CTR {ctr:.1%}")
        if not bits:
            return ""
        days = fmt_int(self.perf.get("window_days")) or "30"
        return f"Last {days} days: {', '.join(bits)}."

    def peer_ctr_line(self) -> str:
        ctr, avg = _num(self.perf.get("ctr")), _num(self.peer.get("avg_ctr"))
        if ctr is None or avg is None or ctr >= avg:
            return ""
        return f"Your CTR is {ctr:.1%} vs {avg:.1%} peer average."

    def peer_scope(self) -> str:
        return re.sub(r"_?\d{4}$", "", _txt(self.peer.get("scope"), 60)).replace("_", " ").strip()

    def peer_views_line(self) -> str:
        """Social proof from category peer_stats — only when both numbers exist and differ meaningfully."""
        views, avg = _num(self.perf.get("views")), _num(self.peer.get("avg_views_30d"))
        if views is None or not avg:
            return ""
        who = f"{self.peer_scope()} average" if self.peer_scope() else "Peers average"
        who = who[0].upper() + who[1:]
        if views < avg * 0.9:
            return f"{who} {int(avg):,} views a month — you're at {int(views):,}."
        if views > avg * 1.1:
            return f"You're ahead of the {int(avg):,}-view monthly average for {self.peer_scope() or 'your peers'}."
        return ""

    def peer_line(self) -> str:
        return self.peer_ctr_line() or self.peer_views_line()

    def ask(self, noun: str) -> str:
        """Single binary CTA, always the last sentence."""
        noun = noun.strip()
        if self.hi:
            noun = re.sub(r"^(the|a|an)\s+", "", noun).replace("+ a ", "+ ")
            return f"Main {noun} bhej doon? Reply YES."
        return f"Want me to send {noun}? Reply YES."

    def yes(self, question_en: str, question_hi: Optional[str] = None) -> str:
        """The single CTA: one yes/no question, always the last sentence. Hinglish when the merchant speaks Hindi."""
        q = question_hi if (self.hi and question_hi) else question_en
        return f"{q.strip().rstrip('?')}? Reply YES."

    # ---- customer-facing helpers -------------------------------------------
    def c_ident(self) -> dict:
        return _d(_d(self.customer).get("identity"))

    def c_hi(self) -> bool:
        pref = str(self.c_ident().get("language_pref") or "").lower()
        return pref.startswith("hi")

    def c_names(self) -> Tuple[str, str]:
        """(who we address, who the service is for) - handles 'Karthik (parent: Sumitra)'."""
        name = _txt(self.c_ident().get("name"), 60)
        m = re.match(r"^(.+?)\s*\(parent:\s*(.+?)\)$", name)
        return (m.group(2), m.group(1)) if m else (name, "")

    def c_greet(self) -> str:
        ident = self.c_ident()
        name = self.c_names()[0]
        if not name:
            return "Namaste" if self.c_hi() else "Hi"
        m = re.match(r"^(mr|mrs|ms)\.?\s+(.+)$", name, flags=re.I)
        if m and self.c_hi():
            return f"Namaste {m.group(2).split()[-1]} ji"
        return f"Namaste {name}" if (self.c_hi() and ident.get("senior_citizen")) else f"Hi {name}"

    def from_line(self) -> str:
        if not self.m_name:
            return ""
        return f"{self.m_name} here{' 🦷' if self.cat == 'dentists' else ''}."

    def rel(self) -> dict:
        return _d(_d(self.customer).get("relationship"))

    def since_last_visit(self) -> Optional[str]:
        days = _num(self.payload.get("days_since_last_visit"))
        if days is None:
            last = parse_dt(self.payload.get("last_service_date") or self.rel().get("last_visit"))
            if last and self.now:
                days = (self.now - last).days
        if days is None or days <= 0:
            return None
        days = int(days)
        if days >= 60:
            return f"{round(days / 30.4)} months"
        if days >= 14:
            return f"{round(days / 7)} weeks"
        return f"{days} days"

    def last_visit_line(self) -> str:
        last = parse_dt(self.rel().get("last_visit"))
        if not last:
            return ""
        return f"Aapki last visit {fmt_date(last)} ko thi." if self.c_hi() else f"Your last visit was on {fmt_date(last)}."

    def slot_labels(self, key: str = "available_slots") -> List[str]:
        return [s.get("label") for s in map(_d, _l(self.payload.get(key))) if s.get("label")][:2]


def _res(body: str, cta: str, rationale: str, send_as: str = "vera",
         params: Optional[List[str]] = None) -> dict:
    return {"body": clean(body), "cta": cta, "send_as": send_as,
            "rationale": rationale, "template_params": [p for p in (params or []) if p]}


# =============================================================================
# MERCHANT-FACING COMPOSERS (send_as = vera)
# =============================================================================

def _no_dot(s: str) -> str:
    return s.rstrip(" .")


def _offer_or_catalog(c: Ctx) -> Tuple[Optional[str], bool]:
    """(offer title, is_merchants_own). Falls back to the category's most common offer, framed as a proposal."""
    offers = c.active_offers()
    if offers:
        return offers[0], True
    return c.catalog_offer(), False


def m_research(c: Ctx) -> dict:
    item = c.digest(c.payload.get("top_item_id") or c.payload.get("digest_item_id"),
                    ("research", "trend", "tech"))
    if not item:
        return m_generic(c)
    title, source = item.get("title", ""), item.get("source", "")
    finding = _no_dot(first_sentence(item.get("summary")) or title)
    trial = fmt_int(item.get("trial_n"))
    if trial and trial not in finding and str(item.get("trial_n")) not in finding:
        finding += f" ({trial} {c.audience()}s)"
    tie = ""
    if item.get("patient_segment") == "high_risk_adults" and "high_risk_adult_cohort" in c.signals:
        tie = "Your high-risk adult cohort is exactly who this is for."
    elif item.get("patient_segment"):
        tie = f"Most relevant for {human(item.get('patient_segment'))}."
    head = f"{c.sal()}, new in {source}: {finding}." if source else f"{c.sal()}, {finding}."
    body = join(head, tie, c.yes(f"Shall I send the 2-min abstract + a {c.audience()} WhatsApp you can forward",
                                 f"2-min abstract + {c.audience()} WhatsApp draft bhej doon"))
    return _res(body, "binary",
                f"Digest item {item.get('id')} ({source}): leads with the finding + sample size, ties it to the merchant's cohort, reciprocity CTA.",
                params=[c.sal(), title, source])


def m_compliance(c: Ctx) -> dict:
    item = c.digest(c.payload.get("top_item_id") or c.payload.get("digest_item_id"), ("compliance", "alert"))
    deadline = parse_dt(c.payload.get("deadline_iso") or _d(item).get("deadline_iso"))
    days_left = (deadline - c.now).days if (deadline and c.now) else None
    when = ""
    if deadline:
        when = f"Deadline {fmt_date(deadline)} {deadline.year}" + (f" — {days_left} days left." if days_left and days_left > 0 else ".")
    if not item:
        if not deadline:
            return m_generic(c)
        body = join(f"{c.sal()}, a compliance change applies to you.", when,
                    c.yes("Shall I send a 1-page checklist to get compliant in time", "1-page compliance checklist bhej doon"))
        return _res(body, "binary", "Compliance deadline from trigger payload; loss aversion.", params=[c.sal()])
    source = item.get("source")
    finding = _no_dot(first_sentence(item.get("summary")) or item.get("title", ""))
    head = f"{c.sal()}, {source}: {finding}." if source else f"{c.sal()}, {item.get('title', '')}: {finding}."
    body = join(head, when, c.yes("Shall I send a 1-page checklist to audit your setup before then",
                                  "Deadline se pehle audit ke liye 1-page checklist bhej doon"))
    return _res(body, "binary", f"Regulatory item {item.get('id')}: concrete rule change + countdown (loss aversion), effortless CTA.",
                params=[c.sal(), item.get("title", "")])


def m_cde(c: Ctx) -> dict:
    item = c.digest(c.payload.get("digest_item_id") or c.payload.get("top_item_id"), ("cde",))
    if not item:
        return m_generic(c)
    credits = c.payload.get("credits") or item.get("credits")
    dt = parse_dt(item.get("date"))
    when = fmt_date(dt) + (f", {dt.strftime('%I%p').lstrip('0').lower()}" if dt and dt.hour else "") if dt else ""
    meta = ", ".join(x for x in [when, f"{credits} CDE credits" if credits else ""] if x)
    fee = _no_dot(first_sentence(item.get("actionable")))
    head = f"{c.sal()}, {item.get('title', '')}" + (f" — {meta}." if meta else ".")
    body = join(head, f"{_no_dot(first_sentence(item.get('summary')))}." if item.get("summary") else "",
                f"{fee}." if fee else "", c.yes("Shall I send the registration link", "Registration link bhej doon"))
    return _res(body, "binary", f"CDE opportunity {item.get('id')}: date, credits and fee straight from the digest.",
                params=[c.sal(), item.get("title", "")])


def _metric_delta(c: Ctx) -> Tuple[str, Optional[float]]:
    metric = c.payload.get("metric") if isinstance(c.payload.get("metric"), str) else "views"
    delta = _num(c.payload.get("delta_pct"))
    if delta is None:
        delta = _num(_d(c.perf.get("delta_7d")).get(f"{metric}_pct"))
    return metric, delta


def _peer_gap(c: Ctx) -> str:
    """Short social-proof clause: CTR vs peers, else views vs peers."""
    ctr, avg = _num(c.perf.get("ctr")), _num(c.peer.get("avg_ctr"))
    if ctr is not None and avg and ctr < avg:
        return f"your CTR is {ctr:.1%} vs {avg:.1%} for {c.peer_scope() or 'peers'}"
    views, avgv = _num(c.perf.get("views")), _num(c.peer.get("avg_views_30d"))
    if views is not None and avgv and views < avgv * 0.9:
        return f"you're at {int(views):,} views a month vs {int(avgv):,} for {c.peer_scope() or 'peers'}"
    return ""


def m_perf_dip(c: Ctx) -> dict:
    metric, delta = _metric_delta(c)
    p = pct(delta)
    base = fmt_int(c.payload.get("vs_baseline"))
    head = f"{c.sal()}, your {human(metric)} are down {p}% this week" if p else f"{c.sal()}, your {human(metric)} are slipping this week"
    head += f" (usually {base}/week)." if base else "."
    facts = join(c.perf_line(), f"{_peer_gap(c)[0].upper() + _peer_gap(c)[1:]}." if _peer_gap(c) else "")
    stale = c.stale_posts_days()
    why = (f"Your last Google post was {stale} days ago — fresh posts are the quickest way to get found again." if stale
           else "No stress — this is fixable with a visibility push.")
    offer, own = _offer_or_catalog(c)
    if offer and own:
        cta = c.yes(f"Shall I put '{offer}' back in front of searchers with a fresh post today, so the {_outcome(c)} pick up again",
                    f"Aaj hi '{offer}' ka fresh post live kar doon, taaki {_outcome(c)} phir se badhein")
    elif offer:
        why = join(why, f"You have no active offer — '{offer}' is the most common one in your category.")
        cta = c.yes("Shall I set it up on your listing today, so searchers have a reason to pick you",
                    "Aaj hi listing pe live kar doon, taaki searchers aapko chunein")
    else:
        cta = c.yes("Shall I publish 2 fresh posts today to win those searches back", "Aaj hi 2 fresh posts live kar doon")
    body = join(head, facts, why, cta)
    return _res(body, "binary", f"Dip on {metric} ({p}%) with the merchant's 30-day numbers + peer benchmark; reason + benefit-led CTA.",
                params=[c.sal(), f"{p}%" if p else "", offer or ""])


def m_seasonal_dip(c: Ctx) -> dict:
    metric, delta = _metric_delta(c)
    p = pct(delta)
    beat = None
    for b in map(_d, _l(c.category.get("seasonal_beats"))):
        if any(k in str(b.get("note", "")).lower() for k in ("lowest", "lull", "retention", "dip")):
            beat = b
            break
    reason = (f"that's the normal {beat.get('month_range', '')} pattern for {c.cat} ({_no_dot(str(beat.get('note', '')))})"
              if beat else (f"that's expected seasonality ({human(c.payload.get('season_note'))})" if c.payload.get("season_note") else ""))
    head = f"{c.sal()}, {human(metric)} are down {p}% this week" if p else f"{c.sal()}, {human(metric)} are dipping this week"
    head += f" — {reason}." if reason else "."
    members = fmt_int(_d(c.merchant.get("customer_aggregate")).get("total_unique_ytd"))
    who = f"your {members} existing {c.audience()}s" if members else f"your current {c.audience()}s"
    body = join(head, c.yes(f"Shall I draft a 3-message plan to keep {who} coming instead of spending on ads",
                            f"Ads ki jagah {who} ke liye 3-message retention plan bana doon"))
    return _res(body, "binary", "Seasonal dip reframed with the category's own seasonal beat; retention over acquisition.",
                params=[c.sal(), f"{p}%" if p else ""])


def m_perf_spike(c: Ctx) -> dict:
    metric, delta = _metric_delta(c)
    p = pct(delta)
    driver = human(c.payload.get("likely_driver"))
    head = f"{c.sal()}, your {human(metric)} are up {p}% this week" if p else f"{c.sal()}, your {human(metric)} are climbing this week"
    if driver:
        head += f", most likely from your {driver}."
    else:
        views = _num(c.perf.get("views"))
        avg = _num(c.peer.get("avg_views_30d"))
        if views is not None and avg and views > avg * 1.1:
            head += f" — you're now at {int(views):,} views a month, ahead of the {int(avg):,} average for {c.peer_scope() or 'peers'}."
        elif c.perf_line():
            head += f" ({_no_dot(c.perf_line())[0].lower() + _no_dot(c.perf_line())[1:]})."
        else:
            head += "."
    offer, own = _offer_or_catalog(c)
    if offer and own:
        cta = c.yes(f"Shall I pin '{offer}' in a follow-up post while the traffic is hot",
                    f"Traffic garam hai — '{offer}' ka follow-up post pin kar doon")
    elif offer:
        cta = c.yes(f"Shall I add a '{offer}' offer to turn the extra views into bookings",
                    f"Extra views ko bookings mein badalne ke liye '{offer}' offer add kar doon")
    else:
        cta = c.yes("Shall I publish a follow-up post while the traffic is hot", "Follow-up post abhi live kar doon")
    body = join(head, cta)
    return _res(body, "binary", f"Spike ({p}%) with its likely driver; momentum + effort externalization.",
                params=[c.sal(), f"{p}%" if p else "", driver])


def m_renewal(c: Ctx) -> dict:
    sub = _d(c.merchant.get("subscription"))
    days = c.payload.get("days_remaining", sub.get("days_remaining"))
    plan = _txt(c.payload.get("plan") or sub.get("plan") or "", 30)
    amt = fmt_int(c.payload.get("renewal_amount"))
    label = "trial" if plan.lower() == "trial" else f"{plan + ' ' if plan else ''}plan"
    dn = _num(days)
    if dn is None:
        head = f"{c.sal()}, your {label} is due for renewal"
    elif dn <= 0:
        head = f"{c.sal()}, your {label} expires today"
    else:
        head = f"{c.sal()}, your {label} {'ends' if label == 'trial' else 'renews'} in {int(dn)} days"
    head += f" (₹{amt})" if amt else ""
    v, cl, d = fmt_int(c.perf.get("views")), fmt_int(c.perf.get("calls")), fmt_int(c.perf.get("directions"))
    got = ", ".join(x for x in [f"{v} views" if v else "", f"{cl} calls" if cl else "", f"{d} direction requests" if d else ""] if x)
    head += f" — in the last 30 days it brought you {got}." if got else "."
    body = join(head, c.yes("Shall I send the renewal link so none of that pauses", "Renewal link bhej doon taaki kuch ruke nahi"))
    return _res(body, "binary", "Renewal anchored on the merchant's own 30-day results (loss aversion).",
                params=[c.sal(), str(int(dn)) if dn is not None else "", got])


def m_festival(c: Ctx) -> dict:
    fest = _txt(c.payload.get("festival"), 30)
    dt = parse_dt(c.payload.get("date"))
    days = _num(c.payload.get("days_until"))
    if days is None and dt and c.now:
        days = (dt - c.now).days
    beat = None
    for b in map(_d, _l(c.category.get("seasonal_beats"))):
        if any(k in str(b.get("note", "")).lower() for k in ("festival", "diwali", "wedding", "gifting", "feast")):
            beat = b
            break
    if fest:
        head = f"{c.sal()}, {fest} is {int(days)} days out" if days is not None else f"{c.sal()}, {fest} is coming up"
        head += f" ({fmt_date(dt)})" if dt else ""
    else:
        head = f"{c.sal()}, festive season is coming up"
    head += f" — in {c.cat}, {beat.get('month_range', '')} is the peak: {_no_dot(str(beat.get('note', '')))}." if beat else "."
    offer, own = _offer_or_catalog(c)
    cta = (c.yes(f"Shall I set up a festive version of '{offer}' now, before the rush", f"Rush se pehle '{offer}' ka festive version set kar doon")
           if offer and own else c.yes("Shall I draft a festive offer + post now, before the rush", "Rush se pehle festive offer + post bana doon"))
    body = join(head, cta)
    return _res(body, "binary", "Festival countdown + category seasonal proof; early-mover CTA on the merchant's real offer.",
                params=[c.sal(), fest or "festival", offer or ""])


def m_curious(c: Ctx) -> dict:
    views, calls = fmt_int(c.perf.get("views")), fmt_int(c.perf.get("calls"))
    options = (c.active_offers() or [])[:2] or [t for t in (c.catalog_offer(),) if t]
    hint = f" — {(' ya ' if c.hi else ' or ').join(repr(o) for o in options)}?" if options else "?"
    stats = f"Your listing got {views} views and {calls} calls in 30 days." if (views and calls) else ""
    if c.hi:
        q = f"{c.sal()}, ek quick sawaal — is hafte {c.m_name or 'aapke yahan'} pe sabse zyada kis service ki enquiry aayi{hint}"
        tail = "Batao, main 10 minute mein uska Google post + price-enquiry ka ready reply bana dungi, taaki woh enquiries booking mein badlein."
    else:
        q = f"{c.sal()}, quick one — which service got the most enquiries at {c.m_name or 'your place'} this week{hint}"
        tail = "Tell me and I'll turn it into a Google post + a ready reply for price questions, so those enquiries become bookings."
    return _res(join(q, stats, tail), "open_ended", "Asking-the-merchant lever, anchored on their real offers + 30-day numbers; reciprocity.",
                params=[c.sal(), c.m_name])


def m_winback(c: Ctx) -> dict:
    lapsed = fmt_int(c.payload.get("lapsed_customers_added_since_expiry"))
    days = fmt_int(c.payload.get("days_since_expiry") or _d(c.merchant.get("subscription")).get("days_since_expiry"))
    dip = pct(c.payload.get("perf_dip_pct"))
    if lapsed:
        head = f"{c.sal()}, {lapsed} customers have lapsed" + (f" since your plan expired {days} days ago" if days else "")
    else:
        head = f"{c.sal()}, your plan expired {days} days ago" if days else f"{c.sal()}, repeat visits are slipping"
    head += f", and views are down {dip}%." if dip else "."
    offer, own = _offer_or_catalog(c)
    cta = (c.yes(f"Shall I send them a win-back WhatsApp with '{offer}'", f"Unhe '{offer}' ke saath win-back WhatsApp bhej doon")
           if offer and own else c.yes("Shall I send them a 3-line win-back WhatsApp", "Unhe 3-line win-back WhatsApp bhej doon"))
    body = join(head, "Lapsed regulars are the cheapest customers to win back." if lapsed else "", cta)
    return _res(body, "binary", "Lapsed-customer count + expiry window from the trigger; win-back CTA.", params=[c.sal(), lapsed or ""])


def m_ipl(c: Ctx) -> dict:
    match, venue = _txt(c.payload.get("match"), 40), _txt(c.payload.get("venue"), 40)
    t = parse_dt(c.payload.get("match_time_iso"))
    when = t.strftime("%I:%M%p").lstrip("0").lower() if t else ""
    head = f"{c.sal()}, {match or 'IPL match'} tonight" + (f" at {venue}" if venue else "") + (f", {when}" if when else "")
    item = c.digest(None, ("seasonal",))
    if item and "ipl" in (str(item.get("title", "")) + str(item.get("id", ""))).lower() and item.get("summary"):
        head += f" — {_no_dot(first_sentence(item.get('summary')))}" + (f" ({item.get('source')})." if item.get("source") else ".")
    else:
        head += "."
    weekend = c.payload.get("is_weeknight") is False
    offers = c.active_offers()
    note, cta = "", c.yes("Shall I post a match-night special on your listing", "Match-night special listing pe daal doon")
    if offers:
        o = offers[0]
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        rng = re.search(r"\((mon|tue|wed|thu|fri|sat|sun)\w*\s*-\s*(mon|tue|wed|thu|fri|sat|sun)\w*\)", o.lower())
        valid_tonight = True
        if rng and t:
            a, b, d = days.index(rng.group(1)), days.index(rng.group(2)), t.weekday()
            valid_tonight = a <= d <= b if a <= b else (d >= a or d <= b)
        if not valid_tonight:
            note = f"Your '{o}' doesn't run on {t.strftime('%A')}s."
            cta = c.yes("Shall I push a delivery-only match combo before the match starts", "Match se pehle delivery-only combo push kar doon")
        elif weekend:
            cta = c.yes(f"Shall I push '{o}' for delivery before the match starts", f"Match se pehle '{o}' delivery ke liye push kar doon")
        else:
            cta = c.yes(f"Shall I post '{o}' as tonight's match-night special", f"'{o}' ko aaj ka match-night special bana doon")
    body = join(head, note, cta)
    return _res(body, "binary", "Tonight's match from the trigger + the category's IPL insight; offer-aware play.",
                params=[c.sal(), match or "IPL"])


def m_review_theme(c: Ctx) -> dict:
    theme = c.payload.get("theme") if isinstance(c.payload.get("theme"), str) else None
    n = fmt_int(c.payload.get("occurrences_30d"))
    quote = _txt(c.payload.get("common_quote"), 90)
    if not theme:
        for r in map(_d, _l(c.merchant.get("review_themes"))):
            if r.get("sentiment") == "neg" and isinstance(r.get("theme"), str):
                theme, n = r.get("theme"), fmt_int(r.get("occurrences_30d"))
                break
    if not theme:
        pos = sorted((r for r in map(_d, _l(c.merchant.get("review_themes"))) if isinstance(r.get("theme"), str)),
                     key=lambda r: -(_num(r.get("occurrences_30d")) or 0))
        if not pos:
            body = join(f"{c.sal()}, a new pattern is showing up in your recent reviews — catching it early is the cheapest way to protect your rating.",
                        c.yes("Shall I send the 30-day summary with exact quotes + reply drafts", "30-din ka review summary + reply drafts bhej doon"))
            return _res(body, "binary", "review_theme_emerged without theme data: flagged truthfully, summary offered.",
                        params=[c.sal(), c.m_name])
        r = pos[0]
        k = fmt_int(r.get("occurrences_30d"))
        body = join(f"{c.sal()}, your reviews keep praising {human(r.get('theme'))}" + (f" — {k} mentions in 30 days." if k else "."),
                    c.yes("Shall I add that to your Google description + post 2 of those reviews",
                          "Yeh line Google description mein daal ke 2 reviews post kar doon"))
        return _res(body, "binary", "Positive review theme from the merchant's own data as social proof.", params=[c.sal(), human(r.get("theme"))])
    trend = ", and it's rising" if c.payload.get("trend") == "rising" else ""
    head = (f"{c.sal()}, {n} reviews this month mention {human(theme)}" if n else f"{c.sal()}, reviews are starting to mention {human(theme)}")
    head += (f" (\"{quote}\"){trend}." if quote else f"{trend}.")
    body = join(head, c.yes("Shall I draft public replies + a 1-line fix note for your team today",
                            "Aaj hi public replies + team ke liye 1-line fix note draft kar doon"))
    return _res(body, "binary", "Review theme with count + verbatim quote from the trigger; loss aversion on rating.",
                params=[c.sal(), human(theme)])


def m_milestone(c: Ctx) -> dict:
    metric = {"review_count": "reviews", "reviews": "reviews", "rating": "rating"}.get(
        str(c.payload.get("metric")), human(c.payload.get("metric")) or "reviews")
    now_v, goal = _num(c.payload.get("value_now")), _num(c.payload.get("milestone_value"))
    views = fmt_int(c.perf.get("views"))
    if now_v is not None and goal is not None and goal > now_v:
        head = f"{c.sal()}, you're {int(goal - now_v)} {metric} away from {int(goal)} ({int(now_v)} today)."
        why = f"A round {int(goal)} stands out to the {views} people who see your listing every month." if views else ""
        cta = c.yes(f"Shall I send a thank-you WhatsApp to your regulars asking for a review, to get you past {int(goal)} this week",
                    f"Regulars ko thank-you WhatsApp bhej ke review maang loon, taaki is hafte {int(goal)} paar ho jaye")
    else:
        head = (f"{c.sal()}, you just crossed {int(goal)} {metric} 🎉" if goal is not None
                else f"{c.sal()}, {c.m_name or 'your listing'} just hit a {metric} milestone 🎉")
        why = f"That's social proof for the {views} people who see your listing every month." if views else ""
        cta = c.yes("Shall I post a thank-you on your listing to show it off", "Listing pe thank-you post daal ke isse dikha doon")
    body = join(head, why, cta)
    return _res(body, "binary", "Milestone gap from the trigger + reach; social-proof building CTA.", params=[c.sal(), metric])


def m_planning(c: Ctx) -> dict:
    topic = human(c.payload.get("intent_topic")) or "your new program"
    said = _txt(c.payload.get("merchant_last_message"), 140)
    offers = c.active_offers()
    base = None
    for o in offers:
        if price_in(o):
            base = (o, price_in(o))
            break
    if base and c.cat == "restaurants":
        o, p = base
        lines = [f"- 10+ orders: suggested ₹{round(p * 0.95)} each (5% off your '{o}')",
                 f"- 25+ orders: suggested ₹{round(p * 0.90)} each (10% off)",
                 f"- 50+ orders: suggested ₹{round(p * 0.85)} each (15% off) + free delivery"]
    else:
        lines = ["- Who: age band / segment it's for",
                 "- Format: sessions per week, batch size, duration",
                 f"- Intro offer: anchored on your '{base[0]}'" if base else "- Intro offer: a first-week trial"]
    ack = f"you asked: \"{said}\" — here's a starter draft for {topic} (edit anything):" if said else \
        f"here's a starter draft for {topic} (edit anything):"
    name = c.owner or c.m_name
    body = f"{name + ', ' if name else ''}{ack}\n" + "\n".join(lines) + "\n" + (
        "Isko Google post + WhatsApp flyer bana doon? Reply YES." if c.hi else
        "Shall I turn it into a Google post + WhatsApp flyer? Reply YES.")
    return {"body": body.strip(), "cta": "binary", "send_as": "vera",
            "rationale": "Merchant already expressed intent — delivered a draft artifact built on their real offer.",
            "template_params": [name, topic]}


def m_competitor(c: Ctx) -> dict:
    name = _txt(c.payload.get("competitor_name"), 40)
    dist = _num(c.payload.get("distance_km"))
    their = _txt(c.payload.get("their_offer"), 60)
    who = name or f"a new {c.noun('place')}"
    head = f"{c.sal()}, heads-up: {who} opened" + (f" {dist:g} km from you" if dist else f" near you{c.where()}")
    opened = parse_dt(c.payload.get("opened_date"))
    head += f" on {fmt_date(opened)}." if opened else "."
    offers = c.active_offers()
    mine = offers[0] if offers else None
    theirs = ""
    if their:
        theirs = f"They're advertising '{their}'"
        tp, mp = price_in(their), price_in(mine or "")
        theirs += f" — ₹{mp - tp} under your '{mine}'." if (tp and mp and tp < mp) else "."
    stance = f"Your '{mine}' is live — worth a fresh post so searchers see it first." if mine and not their else ""
    if not mine and c.catalog_offer():
        stance = f"You have no live offer to answer with — '{c.catalog_offer()}' is the usual category hook."
    stale = c.stale_posts_days()
    facts = f"Your last post is {stale} days old." if stale else c.perf_line()
    cta_en = (f"Shall I post your '{mine}' with your best reviews today, so nearby searchers still pick you first" if mine
              else "Shall I put an answering offer live today, so nearby searchers still pick you first")
    cta_hi = (f"Aaj hi '{mine}' best reviews ke saath post kar doon, taaki paas ke searchers aapko hi chunein" if mine
              else "Aaj hi jawab mein offer live kar doon, taaki paas ke searchers aapko hi chunein")
    body = join(head, theirs, stance, facts, c.yes(cta_en, cta_hi))
    return _res(body, "binary", "Competitor facts only from the trigger payload; price gap computed from real offers; loss aversion + benefit CTA.",
                params=[c.sal(), who, their])


def m_dormant(c: Ctx) -> dict:
    days = fmt_int(c.payload.get("days_since_last_merchant_message"))
    topic = human(c.payload.get("last_topic"))
    head = (f"{c.sal()}, it's been {days} days since we last spoke" + (f" (about {topic})." if topic else ".")) if days \
        else f"{c.sal()}, quick check-in on {c.m_name or 'your listing'}."
    item = c.digest(None, ("trend", "research", "seasonal", "tech"))
    hook = (f"One thing worth 30 seconds: {_no_dot(str(item.get('title')))}" + (f" ({item.get('source')})." if item.get("source") else ".")
            if item and item.get("title") else c.perf_line())
    offer = (c.active_offers() or [None])[0]
    tie = f"It fits well with your '{offer}'." if offer and item else ""
    body = join(head, hook, tie,
                c.yes(f"Shall I send 2 lines on how {c.m_name or 'you'} can turn this into more {_outcome(c)} this week",
                      f"{c.m_name + ' ke' if c.m_name else 'Aapke'} liye isse zyada {_outcome(c)} kaise laayein, 2 line mein bhej doon"))
    return _res(body, "binary", "Re-engagement: acknowledges the gap, fresh sourced hook tied to their offer, outcome-led CTA.",
                params=[c.sal(), days or ""])


def m_supply(c: Ctx) -> dict:
    mol = _txt(c.payload.get("molecule"), 40)
    batches = _strs(c.payload.get("affected_batches"))
    mfr = _txt(c.payload.get("manufacturer"), 40)
    item = c.digest(c.payload.get("alert_id"), ("alert", "supply"))
    src = f"{item.get('source')}: " if item and item.get("source") else ""
    head = f"{c.sal()}, {src}{mol or 'a medicine'} batches {', '.join(batches)}" if batches else f"{c.sal()}, {src}{mol or 'a medicine'}"
    head += (f" by {mfr}" if mfr else "") + " are under a voluntary recall."
    rx = fmt_int(_d(c.merchant.get("customer_aggregate")).get("chronic_rx_count"))
    promised = any(_d(h).get("from") == "merchant" and _d(h).get("engagement") == "intent_action"
                   for h in _l(c.merchant.get("conversation_history")))
    support = f"You have {rx} chronic-Rx customers who may hold them" + (" — and you asked for this list earlier." if promised else ".") if rx else ""
    body = join(head, support, c.yes(f"Shall I pull the affected {mol or ''} list + a replacement message now".replace("  ", " "),
                                     "Affected customers ki list + replacement message abhi bhej doon"))
    return _res(body, "binary", "Recall with batch numbers from the trigger + the merchant's own Rx base; urgent, effortless CTA.",
                params=[c.sal(), mol])


def m_cat_seasonal(c: Ctx) -> dict:
    trends = []
    for t in _strs(c.payload.get("trends"))[:4]:
        m = re.match(r"(.+?)_demand_([+-]?\d+)", t)
        trends.append(f"{m.group(1).replace('_', ' ')} {int(m.group(2)):+d}%" if m else human(t))
    season = human(c.payload.get("season"))
    head = f"{c.sal()}, {season + ' ' if season else ''}demand is shifting for {c.cat}{c.where()}"
    head += f": {', '.join(trends)}." if trends else "."
    views = fmt_int(c.perf.get("views"))
    why = ("Regulars ask for the rising items first — if they're not on the front shelf, that sale walks next door."
           + (f" Your listing already gets {views} views a month." if views else ""))
    body = join(head, why, c.yes("Shall I send a shelf-rotation list + a WhatsApp for your regulars, so you're stocked before the peak",
                                 "Peak se pehle shelf-rotation list + regulars ke liye WhatsApp bhej doon"))
    return _res(body, "binary", "Category seasonal trends with exact deltas from the trigger; loss framing + benefit CTA.",
                params=[c.sal(), season])


def m_gbp(c: Ctx) -> dict:
    up = pct(c.payload.get("estimated_uplift_pct"))
    path = human(c.payload.get("verification_path")).replace(" or ", " or a ")
    head = f"{c.sal()}, {c.m_name or 'your listing'} is still unverified on Google — verified listings see about {up}% more views and calls." \
        if up else f"{c.sal()}, {c.m_name or 'your listing'} is still unverified on Google."
    facts = join(c.perf_line(), c.peer_views_line())
    how = "It only takes a postcard or a phone call." if "postcard" in path else (f"It's done via {path}." if path else "")
    body = join(head, facts, how, c.yes(f"Shall I walk you through it step by step, so that {up}% uplift starts counting for you" if up
                                        else "Shall I walk you through it step by step",
                                        "Step-by-step verify karwa doon, taaki yeh fayda aapko milne lage"))
    return _res(body, "binary", "Unverified listing + uplift estimate from the trigger, merchant's numbers vs peers; benefit CTA.",
                params=[c.sal(), f"{up}%" if up else ""])


def m_generic(c: Ctx) -> dict:
    gap = _peer_gap(c)
    line = c.perf_line()
    head = f"{c.sal()}, {gap}." if gap else join(f"{c.sal()}, quick update on {c.m_name or 'your listing'}{c.where()}.", line)
    offer, own = _offer_or_catalog(c)
    cta = (c.yes(f"Shall I put '{offer}' in a fresh post today", f"Aaj hi '{offer}' ka fresh post live kar doon")
           if offer and own else c.yes("Shall I publish a fresh post today", "Aaj hi fresh post live kar doon"))
    body = join(head, cta)
    return _res(body, "binary", "Fallback: merchant numbers + peer benchmark, CTA on the merchant's own offer.", params=[c.sal(), c.m_name])


# =============================================================================
# CUSTOMER-FACING COMPOSERS (send_as = merchant_on_behalf)
# =============================================================================

def _cust(body: str, cta: str, rationale: str, c: Ctx) -> dict:
    return _res(body, cta, rationale, send_as="merchant_on_behalf",
                params=[c.c_ident().get("name", ""), c.m_name])


def _outcome(c: Ctx) -> str:
    return {"dentists": "appointments", "salons": "bookings", "gyms": "sign-ups", "restaurants": "orders",
            "pharmacies": "orders"}.get(c.cat, "customers")


_CARE = {
    "dentists": ("Regular check-ups keep small issues small.", "Regular check-up se chhoti problem chhoti hi rehti hai."),
    "salons": ("A quick refresh keeps your look on point.", "Ek quick refresh se look fresh rehta hai."),
    "gyms": ("The first session back is the hardest — we'll make it easy.", "Wapas aane ka pehla session sabse mushkil hota hai — hum aasaan bana denge."),
    "pharmacies": ("Staying on schedule with your medicines matters.", "Dawaiyan time pe lena zaroori hai."),
    "restaurants": ("Your favourites are waiting.", "Aapke favourites intezaar kar rahe hain."),
}


def _care(c: Ctx) -> str:
    en, hi = _CARE.get(c.cat, ("", ""))
    return hi if c.c_hi() else en


def _loyalty(c: Ctx) -> str:
    """Personal touch from the customer's real relationship data."""
    n = _num(c.rel().get("visits_total"))
    svcs = [s for s in _strs(c.rel().get("services_received")) if s != "..." and not s.startswith("chronic_rx")]
    last = human(svcs[-1]) if svcs else ""
    if n and n >= 2:
        if c.c_hi():
            return f"Aap humare saath {int(n)} baar aa chuke hain" + (f" (last time: {last})." if last else ".")
        return f"Thanks for your {int(n)} visits with us" + (f" — last time it was {last}." if last else ".")
    return ""


def _two_slot_cta(slots: List[str]) -> str:
    if len(slots) == 2:
        return f"Reply 1 for {slots[0].split(',')[0]}, 2 for {slots[1].split(',')[0]}."
    return ""


def c_recall(c: Ctx) -> dict:
    svc = human(c.payload.get("service_due")) or c.noun("visit")
    since = c.since_last_visit()
    slots = c.slot_labels()
    offer = c.offer_matching(*(svc.split()[-1:])) or (c.active_offers()[:1] or [None])[0]
    pref = human(_d(_d(c.customer).get("preferences")).get("preferred_slots"))
    if c.c_hi():
        s1 = f"Aapki last visit ko {since} ho gaye hain — {svc} due hai." if since else join(c.last_visit_line(), f"Aapka {svc} due hai.")
        s2 = (f"Aapke {pref} preference ke hisaab se slots ready hain: {' ya '.join(slots)}." if pref and slots
              else f"Aapke liye slots ready hain: {' ya '.join(slots)}." if slots else "")
        s3 = f"Aapke liye: {offer}." if offer else ""
    else:
        s1 = f"It's been {since} since your last visit — your {svc} is due." if since else join(c.last_visit_line(), f"Your {svc} is due.")
        s2 = (f"Matching your {pref} preference: {' or '.join(slots)}." if pref and slots
              else f"Slots open for you: {' or '.join(slots)}." if slots else "")
        s3 = f"For you: {offer}." if offer else ""
    cta_s = _two_slot_cta(slots) or ("Reply YES and we'll book you in." if not c.c_hi() else "Reply YES, hum booking kar denge.")
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, _loyalty(c), _care(c), s2, s3, cta_s)
    return _cust(body, "binary", "Recall due: real last-visit gap and visit history, care line, real open slots, merchant's active price.", c)


def c_lapsed(c: Ctx) -> dict:
    since = c.since_last_visit()
    focus = human(c.payload.get("previous_focus"))
    offers = c.active_offers()
    gym = c.cat == "gyms"
    if c.c_hi():
        s1 = (f"Aapko aaye {since} ho gaye" + (" — hota hai, koi baat nahi." if gym else ".")) if since else c.last_visit_line()
        s2 = f"Aapke {focus} goal pe wapas aane mein hum help karenge." if focus else _care(c)
        s3 = f"Is hafte aapke liye: {offers[0]}." if offers else ""
        cta = "Reply YES, hum aapka slot hold kar lenge."
    else:
        s1 = (f"It's been {since} since your last visit" + (" — happens to everyone, no judgment." if gym else ".")) if since else c.last_visit_line()
        s2 = f"We'd love to help you get back to your {focus} goal." if focus else _care(c)
        s3 = f"This week for you: {offers[0]}." if offers else ""
        cta = "Reply YES and we'll hold a slot for you — no commitment."
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, _loyalty(c), s2, s3, cta)
    return _cust(body, "binary", "Lapsed-customer win-back: real gap + history, their own goal, merchant offer; no-shame framing.", c)


def c_appointment(c: Ctx) -> dict:
    p = c.payload
    t = parse_dt(p.get("appointment_iso") or p.get("slot_iso"))
    when = p.get("slot_label") or p.get("time") or (t.strftime("%I:%M %p").lstrip("0") if t else "")
    svcs = [s for s in _strs(c.rel().get("services_received")) if s != "..."]
    svc = human(p.get("service")) or (human(svcs[-1]) if svcs else "")
    what = f"{svc} appointment" if svc else c.noun("appt")
    where = f" at {c.m_name}{', ' + c.locality if c.locality else ''}" if c.m_name else ""
    if c.c_hi():
        s1 = f"reminder: kal aapka {what} hai{where}" + (f", {when}." if when else ".")
        s2 = "Confirm karte hi aapka slot aapke liye hold rahega."
        cta = "Confirm ke liye 1, reschedule ke liye 2 reply karein."
    else:
        s1 = f"reminder: your {what} is tomorrow{where}" + (f" at {when}." if when else ".")
        s2 = "Confirming now keeps your slot held for you."
        cta = "Reply 1 to confirm or 2 to reschedule."
    body = join(f"{c.c_greet()},", s1, s2, cta)
    return _cust(body, "binary", "Appointment reminder with the real service + place, benefit of confirming, confirm/reschedule choice.", c)


def c_trial(c: Ctx) -> dict:
    tdt = parse_dt(c.payload.get("trial_date"))
    slots = c.slot_labels("next_session_options")
    _, child = c.c_names()
    trial = c.noun("trial")
    offers = c.active_offers()
    if c.c_hi():
        who = f"{child} ko {trial} ke liye laane" if child else f"{trial} ke liye aane"
        s1 = f"{who[0].upper() + who[1:]} ka shukriya" + (f" ({fmt_date(tdt)})." if tdt else ".")
        s2 = f"Agla session: {' ya '.join(slots)}." if slots else ""
        s3 = f"Continue karne par: {offers[0]}." if offers else ""
        cta = _two_slot_cta(slots) or "Reply YES, hum agla session set kar denge."
    else:
        who = f"bringing {child} for the" if child else "coming in for your"
        s1 = f"Thanks for {who} {trial}" + (f" on {fmt_date(tdt)}." if tdt else ".")
        s2 = f"Next session: {' or '.join(slots)}." if slots else ""
        s3 = f"If you continue: {offers[0]}." if offers else ""
        cta = _two_slot_cta(slots) or ("Reply YES to book it." if slots else "Reply YES and we'll set up the next one.")
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, s2, s3, cta)
    return _cust(body, "binary", "Trial follow-up with the real next-session slot.", c)


def c_wedding(c: Ctx) -> dict:
    days = fmt_int(c.payload.get("days_to_wedding"))
    wd = parse_dt(c.payload.get("wedding_date"))
    step = human(c.payload.get("next_step_window_open"))
    m = re.match(r"^(.*?)\s*(\d+-day)$", step)
    if m:
        step = f"{m.group(2)} {m.group(1)}"
    s1 = (f"{days} days to your wedding" + (f" on {fmt_date(wd)}" if wd else "") + " 💍.") if days else "Congratulations again 💍."
    s2 = f"This is the right window to start the {step}." if step else ""
    offer = c.offer_matching("bridal", "skin", "facial", "makeup", "spa", "glow")
    s3 = f"{offer} is on right now." if offer else ""
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, s2, s3, "Reply YES and we'll block your first session.")
    return _cust(body, "binary", "Bridal follow-up: countdown + next program window from trigger.", c)


def c_refill(c: Ctx) -> dict:
    meds = _strs(c.payload.get("molecule_list") or c.payload.get("medicines"))
    if not meds and c.cat != "pharmacies":
        return c_recall(c)  # a refill trigger on a non-pharmacy with no molecules: treat as a visit reminder
    out = parse_dt(c.payload.get("stock_runs_out_iso") or c.payload.get("due_date"))
    senior = c.offer_matching("senior") if c.c_ident().get("senior_citizen") else None
    delivery = c.payload.get("delivery_address_saved") or _d(_d(c.customer).get("preferences")).get("delivery_address") == "saved"
    if c.c_hi():
        s1 = f"Aapki monthly dawaiyan ({', '.join(meds)})" if meds else "Aapki monthly dawaiyan"
        s1 += f" {fmt_date(out)} tak khatam ho jayengi." if out else " refill ke liye due hain."
        s2 = "Same dose, same brand ready hai."
        s3 = f"{senior} lagega." if senior else ""
        s4 = "Saved address pe home delivery." if delivery else ""
    else:
        s1 = f"Your monthly medicines ({', '.join(meds)})" if meds else "Your monthly medicines"
        s1 += f" run out on {fmt_date(out)}." if out else " are due for refill."
        s2 = "Same dose, same brand is packed and ready."
        s3 = f"{senior} applies." if senior else ""
        s4 = "Home delivery to your saved address." if delivery else ""
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, s2, s3, s4, "Reply YES to dispatch.")
    return _cust(body, "binary", "Chronic refill: exact molecules + run-out date; senior offer only if merchant has it.", c)


def c_generic(c: Ctx) -> dict:
    offers = c.active_offers()
    body = join(f"{c.c_greet()}, {c.from_line()}", f"{offers[0]} is on this week." if offers else "",
                "Reply YES and we'll reserve a slot for you.")
    return _cust(body, "binary", "Customer fallback with merchant's real offer.", c)


# =============================================================================
# DISPATCH
# =============================================================================

MERCHANT_KINDS = {
    "research_digest": m_research, "research_digest_release": m_research,
    "category_research_digest_release": m_research,
    "regulation_change": m_compliance, "compliance_alert": m_compliance,
    "cde_opportunity": m_cde, "cde_webinar_dentists": m_cde,
    "perf_dip": m_perf_dip, "performance_dip": m_perf_dip,
    "seasonal_perf_dip": m_seasonal_dip,
    "perf_spike": m_perf_spike, "performance_spike": m_perf_spike,
    "renewal_due": m_renewal,
    "festival_upcoming": m_festival,
    "curious_ask_due": m_curious, "scheduled_recurring": m_curious,
    "winback_eligible": m_winback, "winback_campaign_due": m_winback,
    "ipl_match_today": m_ipl, "ipl_match_special": m_ipl,
    "review_theme_emerged": m_review_theme, "review_theme": m_review_theme,
    "milestone_reached": m_milestone, "milestone_imminent": m_milestone,
    "active_planning_intent": m_planning,
    "competitor_opened": m_competitor,
    "dormant_with_vera": m_dormant,
    "supply_alert": m_supply, "supply_recall": m_supply,
    "category_seasonal": m_cat_seasonal, "summer_demand_shift": m_cat_seasonal,
    "gbp_unverified": m_gbp,
}

CUSTOMER_KINDS = {
    "recall_due": c_recall,
    "customer_lapsed_soft": c_lapsed, "customer_lapsed_hard": c_lapsed,
    "appointment_tomorrow": c_appointment, "appointment_reminder": c_appointment,
    "trial_followup": c_trial,
    "wedding_package_followup": c_wedding, "bridal_followup": c_wedding,
    "chronic_refill_due": c_refill, "refill_reminder": c_refill,
}


def _strip_taboo(body: str, category: dict) -> str:
    taboo = [str(t).split(" (")[0].lower() for t in _l(_d(_d(category).get("voice")).get("vocab_taboo"))]
    if not taboo:
        return body
    parts = re.split(r"(?<=[.!?])\s+", body)
    kept = [p for p in parts if not any(t and t in p.lower() for t in taboo)]
    return " ".join(kept) if kept else body


_LEAK = re.compile(r"\b(None|nan|NaN|inf|True|False|undefined|null)\b|[{}\[\]]|\d(\.\d+)?e[+-]\d{2,}")


def valid_body(body: Any) -> bool:
    """Post-composition validator: non-empty text with no Python/JSON artefacts from corrupted context."""
    return isinstance(body, str) and bool(body.strip()) and not _LEAK.search(body) and len(body) <= 1500


def compose(category: dict, merchant: dict, trigger: dict, customer: Optional[dict] = None,
            now: Optional[datetime] = None, exclude_items: Iterable[str] = ()) -> dict:
    """
    Compose one outbound message from the 4 context layers.
    Returns: body, cta, send_as, suppression_key, rationale, template_params
    """
    c = Ctx(category, merchant, trigger, customer, now, exclude_items)
    kind = str(c.trigger.get("kind") or "")
    is_customer = c.customer is not None or c.trigger.get("scope") == "customer"

    if is_customer:
        fn = CUSTOMER_KINDS.get(kind, c_generic)
    else:
        fn = MERCHANT_KINDS.get(kind, m_generic)
    fallback = c_generic if is_customer else m_generic
    try:
        out = fn(c)
    except Exception:  # never let one odd payload kill a tick
        out = fallback(c)
    if not valid_body(out.get("body")):  # corrupted context leaked into the text: degrade to the safe composer
        try:
            out = fallback(c)
        except Exception:
            out = {"body": "", "cta": "none", "send_as": "vera", "rationale": "", "template_params": []}
    out["valid"] = valid_body(out.get("body"))

    if "\n" not in out["body"]:
        out["body"] = _strip_taboo(out["body"], c.category)
    anchors = [a for a in out.get("template_params", []) if a][:3]
    if anchors:
        out["rationale"] = f"{out['rationale']} Anchored on: {'; '.join(anchors)}."
    out["suppression_key"] = c.trigger.get("suppression_key") or f"{kind}:{c.merchant.get('merchant_id', '')}"
    out["kind"] = kind
    out["digest_item_id"] = c.used_item
    return out
