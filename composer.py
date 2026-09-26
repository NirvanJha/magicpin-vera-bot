"""
Grounded message composer for Vera.

Rule #1: every fact in a body (names, numbers, prices, dates, sources) must come
from a pushed context (category / merchant / trigger / customer). When a value is
missing, the sentence that needed it is dropped instead of being filled with a
made-up default.
"""

from __future__ import annotations

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
    try:
        if isinstance(x, bool):
            return None
        return float(x)
    except (TypeError, ValueError):
        return None


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

class Ctx:
    def __init__(self, category: dict, merchant: dict, trigger: dict,
                 customer: Optional[dict], now: Optional[datetime],
                 exclude_items: Iterable[str] = ()):
        self.category = _d(category)
        self.merchant = _d(merchant)
        self.trigger = _d(trigger)
        self.customer = _d(customer) if customer else None
        self.payload = _d(self.trigger.get("payload"))
        self.now = now
        self.exclude_items = set(exclude_items or ())

        self.cat = self.category.get("slug") or self.merchant.get("category_slug") or ""
        ident = _d(self.merchant.get("identity"))
        self.m_name = ident.get("name") or ""
        self.owner = (ident.get("owner_first_name") or "").strip()
        self.locality = ident.get("locality") or ""
        self.city = ident.get("city") or ""
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

    def ask(self, noun: str) -> str:
        """Single binary CTA, always the last sentence."""
        noun = noun.strip()
        if self.hi:
            noun = re.sub(r"^(the|a|an)\s+", "", noun)
            return f"Main {noun} bhej doon? Reply YES."
        return f"Want me to send {noun}? Reply YES."

    # ---- customer-facing helpers -------------------------------------------
    def c_ident(self) -> dict:
        return _d(_d(self.customer).get("identity"))

    def c_hi(self) -> bool:
        pref = str(self.c_ident().get("language_pref") or "").lower()
        return pref.startswith("hi")

    def c_names(self) -> Tuple[str, str]:
        """(who we address, who the service is for) - handles 'Karthik (parent: Sumitra)'."""
        name = (self.c_ident().get("name") or "").strip()
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

def m_research(c: Ctx) -> dict:
    item = c.digest(c.payload.get("top_item_id") or c.payload.get("digest_item_id"),
                    ("research", "trend", "tech"))
    if not item:
        return m_generic(c)
    title, source = item.get("title", ""), item.get("source", "")
    trial = fmt_int(item.get("trial_n"))
    trial_s = f"{trial}-{c.audience()} trial:" if trial else ""
    tie = ""
    if item.get("patient_segment") == "high_risk_adults" and "high_risk_adult_cohort" in c.signals:
        tie = "Directly relevant to your high-risk adult cohort."
    elif item.get("patient_segment"):
        tie = f"Most relevant for {human(item.get('patient_segment'))}."
    head = f"{c.sal()}, new in {source}:" if source else f"{c.sal()}, new digest item:"
    body = join(head, f"{title}.", trial_s, first_sentence(item.get("summary")), tie,
                c.ask(f"the 2-min abstract + a {c.audience()}-ed WhatsApp draft you can forward"))
    return _res(body, "binary",
                f"Digest item {item.get('id')} ({source}) matched to merchant; cites source + numbers, reciprocity CTA.",
                params=[c.sal(), title, source])


def m_compliance(c: Ctx) -> dict:
    item = c.digest(c.payload.get("top_item_id") or c.payload.get("digest_item_id"), ("compliance", "alert"))
    deadline = parse_dt(c.payload.get("deadline_iso") or _d(item).get("deadline_iso"))
    days_left = (deadline - c.now).days if (deadline and c.now) else None
    if not item:
        if not deadline:
            return m_generic(c)
        body = join(f"{c.sal()}, compliance deadline on {fmt_date(deadline)}"
                    + (f" — {days_left} days left." if days_left and days_left > 0 else "."),
                    c.ask("a 1-page audit checklist"))
        return _res(body, "binary", "Compliance deadline from trigger payload.", params=[c.sal()])
    title = item.get("title", "")
    when = ""
    if deadline and fmt_date(deadline) not in title and deadline.strftime("%Y-%m-%d") not in title:
        when = f"Deadline: {fmt_date(deadline)} {deadline.year}."
    if days_left is not None and days_left > 0:
        when = join(when, f"That's {days_left} days from today.")
    src = f"({item.get('source')})" if item.get("source") else ""
    body = join(f"{c.sal()}, compliance heads-up {src}: {title}.", first_sentence(item.get("summary")), when,
                first_sentence(item.get("actionable")),
                c.ask("a 1-page audit checklist for your setup"))
    return _res(body, "binary", f"Regulatory item {item.get('id')} with deadline; loss-aversion + effort externalization.",
                params=[c.sal(), title])


def m_cde(c: Ctx) -> dict:
    item = c.digest(c.payload.get("digest_item_id") or c.payload.get("top_item_id"), ("cde",))
    if not item:
        return m_generic(c)
    credits = c.payload.get("credits") or item.get("credits")
    dt = parse_dt(item.get("date"))
    when = ""
    if dt:
        when = fmt_date(dt) + (f", {dt.strftime('%I%p').lstrip('0').lower()}" if dt.hour else "")
    meta = ", ".join(x for x in [when, f"{credits} CDE credits" if credits else ""] if x)
    body = join(f"{c.sal()}, {item.get('title', '')}" + (f" — {meta}." if meta else "."),
                first_sentence(item.get("summary")), first_sentence(item.get("actionable")),
                c.ask("the registration link"))
    return _res(body, "binary", f"CDE opportunity {item.get('id')} with date/credits/fee from digest.",
                params=[c.sal(), item.get("title", "")])


def _metric_delta(c: Ctx) -> Tuple[str, Optional[float]]:
    metric = c.payload.get("metric") or "views"
    delta = _num(c.payload.get("delta_pct"))
    if delta is None:
        delta = _num(_d(c.perf.get("delta_7d")).get(f"{metric}_pct"))
    return metric, delta


def m_perf_dip(c: Ctx) -> dict:
    metric, delta = _metric_delta(c)
    p = pct(delta)
    window = c.payload.get("window") or "7d"
    base = fmt_int(c.payload.get("vs_baseline"))
    head = f"{c.sal()}, your {human(metric)} dropped {p}% in the last {window.replace('d', ' days')}" if p \
        else f"{c.sal()}, your {human(metric)} are trending down this week"
    head += f" (baseline {base}/week)." if base else "."
    stale = c.stale_posts_days()
    avg_freq = fmt_int(c.peer.get("avg_post_freq_days"))
    stale_s = f"Your last Google post was {stale} days ago; peers post every {avg_freq} days." if (stale and avg_freq) else ""
    offers = c.active_offers()
    lever = f"Re-pushing '{offers[0]}' in a fresh post is the fastest lever." if offers else (
        f"You have no active offer — '{c.catalog_offer()}' is the most common one in your category." if c.catalog_offer() else "")
    body = join(head, c.perf_line(), c.peer_ctr_line(), stale_s, lever, c.ask("2 ready-to-publish Google posts"))
    return _res(body, "binary", f"Performance dip on {metric} ({p}%) anchored on merchant numbers + peer benchmark.",
                params=[c.sal(), f"{p}%"])


def m_seasonal_dip(c: Ctx) -> dict:
    metric, delta = _metric_delta(c)
    p = pct(delta)
    beat = ""
    for b in map(_d, _l(c.category.get("seasonal_beats"))):
        note = b.get("note", "")
        if any(k in note.lower() for k in ("lowest", "lull", "retention", "dip")):
            beat = f"This is the expected {b.get('month_range', '')} pattern: {note}."
            break
    if not beat and c.payload.get("season_note"):
        beat = f"This is expected seasonality ({human(c.payload.get('season_note'))})."
    agg = _d(c.merchant.get("customer_aggregate"))
    members = fmt_int(agg.get("total_unique_ytd"))
    head = f"{c.sal()}, {human(metric)} are down {p}% this week — no panic." if p else f"{c.sal()}, {human(metric)} are dipping this week — no panic."
    action = f"Better ROI now: keep your {members} existing {c.audience()}s engaged instead of spending on ads." if members \
        else ("" if "retention" in beat.lower() else "Better ROI now: focus on retention instead of ad spend.")
    body = join(head, beat, c.perf_line(), action, c.ask(f"a 3-message retention plan for current {c.audience()}s"))
    return _res(body, "binary", "Seasonal dip reframed with category seasonal beat; retention over acquisition.",
                params=[c.sal(), f"{p}%"])


def m_perf_spike(c: Ctx) -> dict:
    metric, delta = _metric_delta(c)
    p = pct(delta)
    driver = human(c.payload.get("likely_driver"))
    head = f"{c.sal()}, your {human(metric)} are up {p}% this week" if p else f"{c.sal()}, your {human(metric)} are climbing this week"
    head += f" — looks driven by your {driver}." if driver else "."
    offers = c.active_offers()
    lever = f"A follow-up post pinning '{offers[0]}' converts the extra traffic while it lasts." if offers else \
        "A follow-up post now converts the extra traffic while it lasts."
    body = join(head, c.perf_line(), lever, c.ask("a follow-up post draft"))
    return _res(body, "binary", f"Performance spike ({p}%) — momentum + effort externalization.", params=[c.sal(), f"{p}%"])


def m_renewal(c: Ctx) -> dict:
    sub = _d(c.merchant.get("subscription"))
    days = c.payload.get("days_remaining", sub.get("days_remaining"))
    plan = c.payload.get("plan") or sub.get("plan") or ""
    amt = fmt_int(c.payload.get("renewal_amount"))
    label = "trial" if str(plan).lower() == "trial" else f"{plan + ' ' if plan else ''}plan"
    dn = _num(days)
    if dn is None:
        head = f"{c.sal()}, your {label} is due for renewal"
    elif dn <= 0:
        head = f"{c.sal()}, your {label} expires today"
    else:
        head = f"{c.sal()}, your {label} {'ends' if label == 'trial' else 'renews'} in {int(dn)} days"
    head += f" (₹{amt})." if amt else "."
    d = fmt_int(c.perf.get("directions"))
    roi = c.perf_line()
    if roi and d:
        roi = roi[:-1] + f", {d} direction requests."
    body = join(head, roi and f"What it delivered — {roi[0].lower() + roi[1:]}", c.ask("the renewal link"))
    return _res(body, "binary", "Renewal nudge anchored on the merchant's own 30-day results.", params=[c.sal(), str(days)])


def m_festival(c: Ctx) -> dict:
    fest = c.payload.get("festival")
    dt = parse_dt(c.payload.get("date"))
    days = c.payload.get("days_until")
    if days is None and dt and c.now:
        days = (dt - c.now).days
    beat = ""
    for b in map(_d, _l(c.category.get("seasonal_beats"))):
        note = b.get("note", "")
        if any(k in note.lower() for k in ("festival", "diwali", "wedding", "gifting", "feast")):
            beat = f"Category pattern for {b.get('month_range', '')}: {note}."
            break
    if fest:
        head = f"{c.sal()}, {fest} is on {fmt_date(dt)}" if dt else f"{c.sal()}, {fest} is coming up"
        head += f" ({days} days out)." if days not in (None, "") else "."
    else:
        head = f"{c.sal()}, festive season is coming up for {c.m_name}{c.where()}."
    offers = c.active_offers()
    noun = f"a festive post + WhatsApp draft built on '{offers[0]}'" if offers else "a festive offer + post draft"
    body = join(head, beat, "Merchants who lock a festive offer early catch the early searches.", c.ask(noun))
    return _res(body, "binary", "Festival timing from trigger + category seasonal beat.", params=[c.sal(), fest or "festival"])


def m_curious(c: Ctx) -> dict:
    views, calls = fmt_int(c.perf.get("views")), fmt_int(c.perf.get("calls"))
    stats = f"Your listing got {views} views and {calls} calls in 30 days." if (views and calls) else ""
    q = (f"{c.sal()}, ek quick sawaal — is hafte {c.m_name} pe sabse zyada kis service ki enquiry aayi?"
         if c.hi else f"{c.sal()}, quick one — which service got the most enquiries at {c.m_name} this week?")
    body = join(q, stats, "Tell me and I'll turn it into a Google post + a ready WhatsApp reply for price questions.")
    return _res(body, "open_ended", "Asking-the-merchant lever with reciprocity (Vera does the drafting).", params=[c.sal(), c.m_name])


def m_winback(c: Ctx) -> dict:
    lapsed = fmt_int(c.payload.get("lapsed_customers_added_since_expiry"))
    days = fmt_int(c.payload.get("days_since_expiry") or _d(c.merchant.get("subscription")).get("days_since_expiry"))
    dip = pct(c.payload.get("perf_dip_pct"))
    bits = []
    if days:
        bits.append(f"it's been {days} days since your plan expired")
    if dip:
        bits.append(f"views are down {dip}% since")
    head = f"{c.sal()}, " + (", and ".join(bits) + "." if bits else f"quick check-in on {c.m_name}.")
    lapsed_s = f"{lapsed} customers have lapsed in that window — they're the cheapest to win back." if lapsed else ""
    offers = c.active_offers()
    offer_s = f"'{offers[0]}' is still live to use as the hook." if offers else ""
    body = join(head, lapsed_s, offer_s, c.ask("a 3-line win-back WhatsApp for them"))
    return _res(body, "binary", "Win-back with lapsed-customer count and expiry window from trigger.", params=[c.sal(), lapsed or ""])


def m_ipl(c: Ctx) -> dict:
    match, venue = c.payload.get("match"), c.payload.get("venue")
    t = parse_dt(c.payload.get("match_time_iso"))
    when = t.strftime("%I:%M%p").lstrip("0").lower() if t else ""
    head = f"{c.sal()}, {match} tonight" if match else f"{c.sal()}, IPL match tonight"
    head += (f" at {venue}" if venue else "") + (f", {when}." if when else ".")
    item = c.digest(None, ("seasonal",))
    insight = ""
    if item and "ipl" in (item.get("title", "") + item.get("id", "")).lower():
        insight = first_sentence(item.get("summary"))
        if item.get("source"):
            insight = insight[:-1] + f" — {item.get('source')}."
    weekend = c.payload.get("is_weeknight") is False
    offers = c.active_offers()
    play = ""
    if offers:
        o = offers[0]
        days = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
        rng = re.search(r"\((mon|tue|wed|thu|fri|sat|sun)\w*\s*-\s*(mon|tue|wed|thu|fri|sat|sun)\w*\)", o.lower())
        valid_tonight = True
        if rng and t:
            a, b, d = days.index(rng.group(1)), days.index(rng.group(2)), t.weekday()
            valid_tonight = a <= d <= b if a <= b else (d >= a or d <= b)
        if not valid_tonight:
            play = f"Note: '{o}' doesn't run on {t.strftime('%A')}s — a delivery-only match combo is the better play tonight."
        elif weekend:
            play = f"Tonight, push '{o}' for delivery/takeaway rather than a dine-in promo."
        else:
            play = f"Good night for a match-night dine-in push around '{o}'."
    body = join(head, insight, play, c.ask("the match-night post + WhatsApp story"))
    return _res(body, "binary", "IPL match from trigger + category IPL insight; offer-specific play.", params=[c.sal(), match or "IPL"])


def m_review_theme(c: Ctx) -> dict:
    theme = c.payload.get("theme")
    n = c.payload.get("occurrences_30d")
    quote = c.payload.get("common_quote")
    if not theme:
        for r in map(_d, _l(c.merchant.get("review_themes"))):
            if r.get("sentiment") == "neg":
                theme, n = r.get("theme"), r.get("occurrences_30d")
                break
    if not theme:
        pos = sorted((_d(r) for r in _l(c.merchant.get("review_themes"))),
                     key=lambda r: -(_num(r.get("occurrences_30d")) or 0))
        if not pos or not pos[0].get("theme"):
            return m_generic(c)
        r = pos[0]
        body = join(f"{c.sal()}, your reviews keep praising {human(r.get('theme'))}"
                    + (f" — {r.get('occurrences_30d')} mentions in 30 days." if r.get("occurrences_30d") else "."),
                    "Putting that line in your Google description turns reviews into bookings.",
                    c.ask("a refreshed description + a post quoting 2 of those reviews"))
        return _res(body, "binary", "Review theme from merchant's own review_themes; social proof.",
                    params=[c.sal(), human(r.get("theme"))])
    trend = " and rising" if c.payload.get("trend") == "rising" else ""
    head = f"{c.sal()}, {n} reviews in the last 30 days mention {human(theme)}{trend}." if n else \
        f"{c.sal()}, a review theme is emerging: {human(theme)}{trend}."
    q = f"One says: \"{quote}\"." if quote else ""
    body = join(head, q, "Replying publicly within 48h limits the rating damage.",
                c.ask("reply templates for those reviews + a 1-line fix note for your team"))
    return _res(body, "binary", "Review theme from trigger with verbatim quote; loss aversion on rating.", params=[c.sal(), human(theme)])


def m_milestone(c: Ctx) -> dict:
    metric = {"review_count": "reviews", "reviews": "reviews", "rating": "rating"}.get(
        str(c.payload.get("metric")), human(c.payload.get("metric")) or "reviews")
    now_v, goal = _num(c.payload.get("value_now")), _num(c.payload.get("milestone_value"))
    if now_v is not None and goal is not None and goal > now_v:
        head = f"{c.sal()}, you're at {int(now_v)} {metric} — just {int(goal - now_v)} away from {int(goal)}."
    elif goal is not None:
        head = f"{c.sal()}, you just crossed {int(goal)} {metric}!"
    else:
        head = f"{c.sal()}, {c.m_name or 'your listing'} just hit a {metric} milestone!"
    body = join(head, c.perf_line() if goal is None else "", "A short thank-you post asking happy customers to share feedback usually closes the gap in days.",
                c.ask("the thank-you post + a WhatsApp nudge for regulars"))
    return _res(body, "binary", "Milestone progress from trigger; social proof + small ask.", params=[c.sal(), metric])


def m_planning(c: Ctx) -> dict:
    topic = human(c.payload.get("intent_topic")) or "your new program"
    said = c.payload.get("merchant_last_message")
    offers = c.active_offers()
    lines: List[str] = []
    base = None
    for o in offers:
        if price_in(o):
            base = (o, price_in(o))
            break
    if base and c.cat == "restaurants":
        o, p = base
        lines = [f"- 10+ orders: ₹{round(p * 0.95)} each (5% off your '{o}')",
                 f"- 25+ orders: ₹{round(p * 0.90)} each (10% off)",
                 f"- 50+ orders: ₹{round(p * 0.85)} each (15% off) + free delivery"]
    else:
        lines = ["- Who: age band / segment it's for",
                 "- Format: sessions per week, batch size, duration",
                 f"- Intro offer: anchored on your '{base[0]}'" if base else "- Intro offer: a first-week trial"]
    ack = f"you asked: \"{said}\" — here's a starter draft for {topic} (edit anything):" if said else \
        f"here's a starter draft for {topic} (edit anything):"
    name = c.owner or c.m_name
    body = f"{name + ', ' if name else ''}{ack}\n" + "\n".join(lines) + "\n" + (
        "Isko Google post + WhatsApp flyer bana doon? Reply YES." if c.hi else
        "Want me to turn it into a Google post + WhatsApp flyer? Reply YES.")
    return {"body": body.strip(), "cta": "binary", "send_as": "vera",
            "rationale": "Merchant already expressed intent — delivered a draft artifact built on their real offer.",
            "template_params": [name, topic]}


def m_competitor(c: Ctx) -> dict:
    name, dist = c.payload.get("competitor_name"), c.payload.get("distance_km")
    their = c.payload.get("their_offer")
    who = name or f"a new {c.noun('place')}"
    head = f"{c.sal()}, heads-up: {who} opened" + (f" {dist} km from you" if dist else f" near you{c.where()}")
    opened = parse_dt(c.payload.get("opened_date"))
    head += f" on {fmt_date(opened)}." if opened else "."
    theirs = f"They're advertising '{their}'." if their else ""
    offers = c.active_offers()
    mine = ""
    if offers:
        mine = f"Your '{offers[0]}' is live — worth a fresh post so searchers see it first."
    elif c.catalog_offer():
        mine = f"You have no live offer to answer with — '{c.catalog_offer()}' is the usual category hook."
    stale = c.stale_posts_days()
    stale_s = f"Your last post is {stale} days old." if stale else ""
    body = join(head, theirs, mine, stale_s, "" if (their or stale) else c.perf_line(),
                c.ask("a post highlighting your offer + reviews"))
    return _res(body, "binary", "Competitor facts only from trigger payload; loss aversion.", params=[c.sal(), who])


def m_dormant(c: Ctx) -> dict:
    days = fmt_int(c.payload.get("days_since_last_merchant_message"))
    topic = human(c.payload.get("last_topic"))
    head = f"{c.sal()}, it's been {days} days since we last spoke" + (f" (about {topic})." if topic else ".") if days \
        else f"{c.sal()}, quick check-in."
    item = c.digest(None, ("trend", "research", "seasonal", "tech"))
    hook = f"One thing worth your time: {item.get('title')} ({item.get('source')})." if item and item.get("source") else ""
    body = join(head, hook or c.perf_line(), c.ask("a 2-line summary of what it means for you"))
    return _res(body, "binary", "Re-engagement with a fresh, sourced hook instead of a generic ping.", params=[c.sal(), days or ""])


def m_supply(c: Ctx) -> dict:
    mol = c.payload.get("molecule")
    batches = [str(b) for b in _l(c.payload.get("affected_batches"))]
    mfr = c.payload.get("manufacturer")
    item = c.digest(c.payload.get("alert_id"), ("alert", "supply"))
    src = f" ({item.get('source')})" if item and item.get("source") else ""
    head = f"{c.sal()}, recall alert{src}: {mol or 'medicine'} batches {', '.join(batches)}" if batches \
        else f"{c.sal()}, recall alert{src} on {mol or 'a medicine'}"
    head += f" by {mfr}." if mfr else "."
    reason = first_sentence(item.get("summary")) if item else ""
    rx = fmt_int(_d(c.merchant.get("customer_aggregate")).get("chronic_rx_count"))
    rx_s = f"You have {rx} chronic-Rx customers — some may hold these batches." if rx else ""
    promised = ""
    for h in reversed(_l(c.merchant.get("conversation_history"))):
        h = _d(h)
        if h.get("from") == "merchant" and h.get("engagement") == "intent_action":
            promised = "As you asked earlier, I can pull the list."
            break
    body = join(head, reason, rx_s, promised,
                c.ask(f"the filtered list of {mol or 'affected'} customers + a replacement message"))
    return _res(body, "binary", "Recall alert with batch numbers from trigger; merchant's own Rx base.", params=[c.sal(), mol or ""])


def m_cat_seasonal(c: Ctx) -> dict:
    trends = []
    for t in _l(c.payload.get("trends"))[:4]:
        m = re.match(r"(.+?)_demand_([+-]?\d+)", str(t))
        trends.append(f"{m.group(1).replace('_', ' ')} {int(m.group(2)):+d}%" if m else human(t))
    season = human(c.payload.get("season"))
    head = f"{c.sal()}, {season} demand shift" if season else f"{c.sal()}, seasonal demand shift"
    head += f" for {c.cat}{c.where()}: {', '.join(trends)}." if trends else "."
    body = join(head, "Moving the rising items to the front shelf + a WhatsApp list for regulars captures it early.",
                c.ask("a shelf-rotation list + WhatsApp message"))
    return _res(body, "binary", "Category seasonal trends from trigger payload with exact deltas.", params=[c.sal(), season])


def m_gbp(c: Ctx) -> dict:
    up = pct(c.payload.get("estimated_uplift_pct"))
    path = human(c.payload.get("verification_path")).replace(" or ", " or a ")
    head = f"{c.sal()}, {c.m_name or 'your listing'} is still unverified on Google."
    upl = f"Verified listings see about {up}% more views and calls." if up else ""
    how = f"Verification is via {path}." if path else ""
    body = join(head, upl, how, c.perf_line(), c.ask("step-by-step verification help"))
    return _res(body, "binary", "Unverified GBP with uplift estimate from trigger.", params=[c.sal(), f"{up}%"])


def m_generic(c: Ctx) -> dict:
    offers = c.active_offers()
    offer_s = f"Your '{offers[0]}' is live — a fresh post keeps it visible." if offers else ""
    body = join(f"{c.sal()}, quick update on {c.m_name or 'your listing'}{c.where()}.", c.perf_line(), c.peer_ctr_line(),
                offer_s, c.ask("a ready-to-publish post"))
    return _res(body, "binary", "Fallback: merchant numbers + peer benchmark.", params=[c.sal(), c.m_name])


# =============================================================================
# CUSTOMER-FACING COMPOSERS (send_as = merchant_on_behalf)
# =============================================================================

def _cust(body: str, cta: str, rationale: str, c: Ctx) -> dict:
    return _res(body, cta, rationale, send_as="merchant_on_behalf",
                params=[c.c_ident().get("name", ""), c.m_name])


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
    else:
        s1 = f"It's been {since} since your last visit — your {svc} is due." if since else join(c.last_visit_line(), f"Your {svc} is due.")
        s2 = (f"Matching your {pref} preference: {' or '.join(slots)}." if pref and slots
              else f"Slots open for you: {' or '.join(slots)}." if slots else "")
    s3 = f"{offer}." if offer else ""
    cta_s = _two_slot_cta(slots) or "Reply YES to book."
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, s2, s3, cta_s)
    return _cust(body, "binary", "Recall due: real last-visit gap, real open slots, merchant's active price.", c)


def c_lapsed(c: Ctx) -> dict:
    since = c.since_last_visit()
    focus = human(c.payload.get("previous_focus"))
    offers = c.active_offers()
    gym = c.cat == "gyms"
    if c.c_hi():
        s1 = (f"Aapko aaye {since} ho gaye" + (" — hota hai, koi baat nahi." if gym else ".")) if since else c.last_visit_line()
        s2 = f"Aapke {focus} goal pe wapas aane mein hum help karenge." if focus else ""
        s3 = f"Is hafte aapke liye: {offers[0]}." if offers else ""
        cta = "Reply YES, hum aapka slot hold kar lenge."
    else:
        s1 = (f"It's been {since} since your last visit" + (" — happens to everyone, no judgment." if gym else ".")) if since else c.last_visit_line()
        s2 = f"We'd love to help you get back to your {focus} goal." if focus else ""
        s3 = f"This week for you: {offers[0]}." if offers else ""
        cta = "Reply YES and we'll hold a slot for you."
    body = join(f"{c.c_greet()}, {c.from_line()}", s1, s2, s3, cta)
    return _cust(body, "binary", "Lapsed customer win-back with real gap + merchant offer; no-shame framing.", c)


def c_appointment(c: Ctx) -> dict:
    p = c.payload
    t = parse_dt(p.get("appointment_iso") or p.get("slot_iso"))
    when = p.get("slot_label") or p.get("time") or (t.strftime("%I:%M %p").lstrip("0") if t else "")
    svc = human(p.get("service"))
    what = f"{svc} appointment" if svc else c.noun("appt")
    where = f" at {c.m_name}{', ' + c.locality if c.locality else ''}" if c.m_name else ""
    if c.c_hi():
        s1 = f"reminder: kal aapka {what} hai{where}" + (f", {when}." if when else ".")
        cta = "Confirm ke liye 1, reschedule ke liye 2 reply karein."
    else:
        s1 = f"reminder: your {what} is tomorrow{where}" + (f" at {when}." if when else ".")
        cta = "Reply 1 to confirm or 2 to reschedule."
    body = join(f"{c.c_greet()},", s1, cta)
    return _cust(body, "binary", "Appointment reminder with confirm/reschedule choice (booking flow).", c)


def c_trial(c: Ctx) -> dict:
    tdt = parse_dt(c.payload.get("trial_date"))
    slots = c.slot_labels("next_session_options")
    _, child = c.c_names()
    who = f"bringing {child} for the" if child else "coming in for your"
    trial = c.noun("trial")
    s1 = f"Thanks for {who} {trial}" + (f" on {fmt_date(tdt)}." if tdt else ".")
    s2 = f"Next session: {' or '.join(slots)}." if slots else ""
    offers = c.active_offers()
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
    meds = [str(m) for m in _l(c.payload.get("molecule_list") or c.payload.get("medicines"))]
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
    try:
        out = fn(c)
    except Exception:  # never let one odd payload kill a tick
        out = c_generic(c) if is_customer else m_generic(c)

    if "\n" not in out["body"]:
        out["body"] = _strip_taboo(out["body"], c.category)
    out["suppression_key"] = c.trigger.get("suppression_key") or f"{kind}:{c.merchant.get('merchant_id', '')}"
    out["kind"] = kind
    out["digest_item_id"] = c.used_item
    return out
