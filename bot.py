"""
magicpin AI Challenge — Vera Bot
================================
FastAPI server exposing the 5 judge endpoints:
   GET  /v1/healthz   GET  /v1/metadata
   POST /v1/context   POST /v1/tick   POST /v1/reply

compose() lives in composer.py (re-exported here for generate_submission.py).
Run:  uvicorn bot:app --host 0.0.0.0 --port 8080
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from composer import compose, parse_dt  # noqa: F401  (compose re-exported)
from conversation_handlers import ConversationState, respond


app = FastAPI(title="magicpin Vera Bot", version="2.1.0")
log = logging.getLogger("vera")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
START_TIME = time.time()

VALID_SCOPES = ("category", "merchant", "customer", "trigger")
MAX_CONTEXT_BYTES = 2_000_000       # judge cap is 500 KB; allow headroom
MAX_ACTIONS_PER_TICK = 20
OPT_OUT_DAYS = 30

# (scope, context_id) -> {"version", "payload", "stored_at"}   — pushed by the judge
contexts: Dict[Tuple[str, str], Dict[str, Any]] = {}
# Same shape, loaded from ./dataset — used ONLY when the judge hasn't pushed that id.
seed: Dict[Tuple[str, str], Dict[str, Any]] = {}

conversations: Dict[str, ConversationState] = {}
sent_suppression: Set[Tuple[str, str]] = set()     # (recipient_id, suppression_key)
sent_bodies: Set[str] = set()                      # anti-repetition
sent_digest: Dict[str, Set[str]] = {}              # merchant_id -> digest item ids already used
opted_out: Dict[str, datetime] = {}                # merchant_id -> suppressed until
recipient_hold: Dict[str, datetime] = {}           # recipient -> no new outbound until (our own wait/end decisions)
merchant_auto_counts: Dict[str, int] = {}
counters: Dict[str, Any] = {"conv": 0, "last_tick_now": None}

ACTIVE_THREAD_HOLD = timedelta(minutes=5)    # no second thread to the same person within one tick window
DECLINE_HOLD = timedelta(hours=6)
AUTO_REPLY_HOLD = timedelta(hours=24)


# =============================================================================
# STATE PERSISTENCE (optional: set VERA_STATE_FILE)
# A restart mid-test would otherwise wipe every context the judge pushed.
# =============================================================================

STATE_FILE = os.environ.get("VERA_STATE_FILE", "").strip()
_state_lock = threading.Lock()
_dirty = threading.Event()


def mark_dirty() -> None:
    if STATE_FILE:
        _dirty.set()


def _snapshot() -> dict:
    def dt(d: Dict[str, datetime]) -> Dict[str, str]:
        return {k: v.isoformat() for k, v in d.items()}
    return {
        "contexts": [[k[0], k[1], v] for k, v in contexts.items()],
        "conversations": {k: asdict(v) for k, v in conversations.items()},
        "sent_suppression": [list(x) for x in sent_suppression],
        "sent_bodies": list(sent_bodies),
        "sent_digest": {k: list(v) for k, v in sent_digest.items()},
        "opted_out": dt(opted_out),
        "recipient_hold": dt(recipient_hold),
        "merchant_auto_counts": merchant_auto_counts,
        "counters": {"conv": counters["conv"],
                     "last_tick_now": counters["last_tick_now"].isoformat() if counters["last_tick_now"] else None},
    }


def save_state() -> None:
    if not STATE_FILE:
        return
    with _state_lock:
        for _ in range(5):  # handlers may mutate state mid-copy; retry instead of locking the hot path
            try:
                data = json.dumps(_snapshot(), ensure_ascii=False)
                break
            except RuntimeError:
                time.sleep(0.05)
        else:
            _dirty.set()
            return
    path = Path(STATE_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(data, encoding="utf-8")
    os.replace(tmp, path)  # atomic


def load_state() -> None:
    if not STATE_FILE or not Path(STATE_FILE).is_file():
        return
    try:
        d = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        for scope, cid, v in d.get("contexts", []):
            contexts[(scope, cid)] = v
        for k, v in d.get("conversations", {}).items():
            conversations[k] = ConversationState(**v)
        sent_suppression.update(tuple(x) for x in d.get("sent_suppression", []))
        sent_bodies.update(d.get("sent_bodies", []))
        for k, v in d.get("sent_digest", {}).items():
            sent_digest[k] = set(v)
        for src, dst in (("opted_out", opted_out), ("recipient_hold", recipient_hold)):
            for k, v in d.get(src, {}).items():
                dst[k] = datetime.fromisoformat(v)
        merchant_auto_counts.update(d.get("merchant_auto_counts", {}))
        c = d.get("counters", {})
        counters["conv"] = c.get("conv", 0)
        counters["last_tick_now"] = datetime.fromisoformat(c["last_tick_now"]) if c.get("last_tick_now") else None
    except Exception as e:  # a corrupt snapshot must never stop the bot from booting
        print(f"[vera] could not restore state: {e}")


def _saver_loop() -> None:
    while True:
        _dirty.wait()
        time.sleep(1.0)  # coalesce bursts of writes
        _dirty.clear()
        try:
            save_state()
        except Exception as e:
            print(f"[vera] state save failed: {e}")


@app.on_event("startup")
def _startup() -> None:
    if STATE_FILE:
        threading.Thread(target=_saver_loop, daemon=True, name="vera-state-saver").start()


@app.on_event("shutdown")
def _shutdown() -> None:
    try:
        save_state()
    except Exception:
        pass


# =============================================================================
# SEED FALLBACK
# =============================================================================

def _load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_seed() -> None:
    if os.environ.get("VERA_DISABLE_SEED") == "1":
        return
    base = Path(__file__).parent / "dataset"
    id_keys = {"category": "slug", "merchant": "merchant_id", "customer": "customer_id", "trigger": "id"}

    def put(scope: str, obj: Any) -> None:
        if isinstance(obj, dict) and obj.get(id_keys[scope]):
            seed[(scope, obj[id_keys[scope]])] = {"version": 0, "payload": obj}

    for scope, sub in (("category", "categories"), ("merchant", "merchants"),
                       ("customer", "customers"), ("trigger", "triggers")):
        for d in (base / "expanded" / sub, base / sub):
            if d.is_dir():
                for f in d.glob("*.json"):
                    try:
                        put(scope, _load_json(f))
                    except Exception:
                        pass
        f = base / f"{sub}_seed.json"
        if f.is_file():
            try:
                for obj in _load_json(f).get(sub, []):
                    put(scope, obj)
            except Exception:
                pass


load_seed()
load_state()


# =============================================================================
# LOOKUPS
# =============================================================================

_index: Dict[str, Any] = {"n": -1, "seed_n": -1, "ids": {}, "norm": {}}


def _norm_trg(tid: str) -> str:
    return re.sub(r"^trg_\d+_", "trg_", tid)


def _ids() -> Tuple[Dict[str, List[List[str]]], Dict[str, List[List[str]]]]:
    """Per-scope id lists and normalized-trigger map, rebuilt only when the stores change size."""
    if _index["n"] != len(contexts) or _index["seed_n"] != len(seed):
        ids: Dict[str, List[List[str]]] = {s: [[], []] for s in VALID_SCOPES}
        norm: Dict[str, List[List[str]]] = {}
        for i, store in enumerate((contexts, seed)):
            for (scope, cid) in list(store):
                if scope in ids:
                    ids[scope][i].append(cid)
                if scope == "trigger":
                    norm.setdefault(_norm_trg(cid), [[], []])[i].append(cid)
        _index.update(n=len(contexts), seed_n=len(seed), ids=ids, norm=norm)
    return _index["ids"], _index["norm"]


def get_ctx(scope: str, cid: Optional[str]) -> Tuple[Optional[str], Optional[dict]]:
    """Exact id first (pushed, then seed); then a UNIQUE '<id>_' prefix match."""
    if not cid or not isinstance(cid, str):
        return None, None
    for store in (contexts, seed):
        if (scope, cid) in store:
            return cid, store[(scope, cid)]["payload"]
    ids, _ = _ids()
    for i, store in enumerate((contexts, seed)):
        hits = [x for x in ids.get(scope, [[], []])[i] if x.startswith(cid + "_") or cid.startswith(x + "_")]
        if len(hits) == 1:
            return hits[0], store[(scope, hits[0])]["payload"]
    return cid, None


def resolve_trigger(raw: str) -> Tuple[Optional[str], Optional[dict]]:
    for store in (contexts, seed):
        if ("trigger", raw) in store:
            return raw, store[("trigger", raw)]["payload"]
    # 'trg_research_digest_dentists' == 'trg_001_research_digest_dentists' (exact after stripping the number)
    _, norm = _ids()
    for i, store in enumerate((contexts, seed)):
        hits = norm.get(_norm_trg(raw), [[], []])[i]
        if len(hits) == 1:
            return hits[0], store[("trigger", hits[0])]["payload"]
    return None, None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


_INVISIBLE = re.compile("[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b\u200e\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


def sanitize(obj: Any, depth: int = 0) -> Any:
    """Strip control / bidi-override characters from every string; drop pathological nesting."""
    if depth > 40:
        return None
    if isinstance(obj, str):
        return _INVISIBLE.sub("", obj)
    if isinstance(obj, dict):
        return {sanitize(k, depth + 1) if isinstance(k, str) else k: sanitize(v, depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize(v, depth + 1) for v in obj]
    return obj


async def read_json(request: Request) -> Tuple[Optional[dict], Optional[str]]:
    """Parse the body as JSON regardless of Content-Type (curl -d sends form-encoded)."""
    raw = await request.body()
    if len(raw) > MAX_CONTEXT_BYTES:
        return None, "payload_too_large"
    try:
        # NaN / Infinity literals become null; deeply nested bodies raise RecursionError -> malformed
        data = json.loads(raw.decode("utf-8-sig") or "{}", parse_constant=lambda _c: None)
    except (ValueError, UnicodeDecodeError, RecursionError) as e:
        return None, f"malformed_json: {type(e).__name__}"
    except Exception as e:  # never let parsing take the endpoint down
        return None, f"malformed_json: {type(e).__name__}"
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    return sanitize(data), None


def bad_request(reason: str, details: str = "", code: int = 400) -> JSONResponse:
    return JSONResponse({"accepted": False, "reason": reason, "details": details}, status_code=code)


# =============================================================================
# ENDPOINTS
# =============================================================================

@app.get("/")
async def root():
    return {"status": "ok", "service": "magicpin-vera-bot",
            "endpoints": ["GET /v1/healthz", "GET /v1/metadata", "POST /v1/context", "POST /v1/tick", "POST /v1/reply"]}


@app.get("/v1/healthz")
@app.get("/healthz")
async def healthz():
    counts = {s: 0 for s in VALID_SCOPES}
    for (scope, _) in list(contexts):
        counts[scope] = counts.get(scope, 0) + 1
    return {"status": "ok", "uptime_seconds": int(time.time() - START_TIME), "contexts_loaded": counts}


@app.get("/v1/metadata")
@app.get("/metadata")
async def metadata():
    return {
        "team_name": "magicpin-ai-mastery",
        "team_members": ["Nirvan Jha"],
        "model": "deterministic-grounded-composer (no LLM at runtime)",
        "approach": "trigger-kind dispatch over 4 context layers; every fact sourced from pushed context; "
                    "suppression + per-merchant dedup on tick; intent-classified multi-turn replies",
        "contact_email": "nirvan.jha.ug23@nsut.ac.in",
        "version": "2.1.0",
        "submitted_at": "2026-04-26T08:00:00Z",
    }


@app.post("/v1/context")
@app.post("/context")
async def push_context(request: Request):
    data, err = await read_json(request)
    if err:
        return bad_request("payload_too_large" if err == "payload_too_large" else "malformed", err,
                           413 if err == "payload_too_large" else 400)
    scope, cid, version, payload = data.get("scope"), data.get("context_id"), data.get("version"), data.get("payload")
    if scope not in VALID_SCOPES:
        return bad_request("invalid_scope", f"scope must be one of {list(VALID_SCOPES)}")
    if not isinstance(cid, str) or not cid.strip():
        return bad_request("invalid_context_id", "context_id must be a non-empty string")
    if isinstance(version, bool) or not isinstance(version, int):
        return bad_request("invalid_version", "version must be an integer")
    if not isinstance(payload, dict):
        return bad_request("invalid_payload", "payload must be a JSON object")

    key = (scope, cid)
    cur = contexts.get(key)
    if cur and cur["version"] > version:
        return JSONResponse({"accepted": False, "reason": "stale_version", "current_version": cur["version"]},
                            status_code=409)
    if cur and cur["version"] == version:  # idempotent no-op: keep the original payload
        return {"accepted": True, "ack_id": f"ack_{cid}_v{version}", "stored_at": cur["stored_at"]}

    stored_at = iso(now_utc())
    contexts[key] = {"version": version, "payload": payload, "stored_at": stored_at}
    mark_dirty()
    return {"accepted": True, "ack_id": f"ack_{cid}_v{version}", "stored_at": stored_at}


@app.post("/v1/teardown")
@app.post("/teardown")
async def teardown():
    """End of test (testing brief §11): wipe every context, conversation and the on-disk snapshot."""
    with _state_lock:
        for store in (contexts, conversations, sent_digest, opted_out, recipient_hold, merchant_auto_counts):
            store.clear()
        sent_suppression.clear()
        sent_bodies.clear()
        counters.update(conv=0, last_tick_now=None)
        _dirty.clear()
        if STATE_FILE:
            for f in (Path(STATE_FILE), Path(STATE_FILE).with_suffix(".tmp")):
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass
    return {"status": "ok", "wiped": True}


@app.post("/v1/tick")
@app.post("/tick")
async def tick(request: Request):
    data, err = await read_json(request)
    if err:
        return JSONResponse({"actions": [], "error": err}, status_code=400)
    try:
        actions = _run_tick(data)
        mark_dirty()
        return {"actions": actions}
    except Exception as e:  # never 500 on the judge
        log.exception("tick failed")
        return {"actions": [], "error": f"internal: {type(e).__name__}"}


def _candidate(order: int, raw: Any, now: datetime, seen: Set[str]) -> Optional[tuple]:
    """Resolve one available trigger into a send candidate, or None with the reason logged at debug."""
    if not isinstance(raw, str):
        return None
    tid, trg = resolve_trigger(raw)
    if not isinstance(trg, dict) or tid in seen:
        return None
    seen.add(tid)
    # No expires_at filtering: available_triggers is the judge's own list of what's active
    # right now, and its simulated clock may not line up with the dataset's dates.
    mid, merchant = get_ctx("merchant", trg.get("merchant_id"))
    if not isinstance(merchant, dict):
        return None
    if mid in opted_out and opted_out[mid] > now:
        return None
    payload = trg.get("payload") if isinstance(trg.get("payload"), dict) else {}
    _, category = get_ctx("category", merchant.get("category_slug")) if isinstance(merchant.get("category_slug"), str) \
        else (None, None)
    if not isinstance(category, dict):
        _, category = get_ctx("category", payload.get("category"))
    if not isinstance(category, dict):
        return None
    cust_id, customer = get_ctx("customer", trg.get("customer_id"))
    customer = customer if isinstance(customer, dict) else None
    if trg.get("scope") == "customer" and not customer:
        return None  # can't address a customer we know nothing about
    if customer:
        prefs = customer.get("preferences") if isinstance(customer.get("preferences"), dict) else {}
        consent = customer.get("consent") if isinstance(customer.get("consent"), dict) else {}
        if prefs.get("reminder_opt_in") is False or consent.get("scope") == []:
            return None  # customer explicitly opted out of messages
    recipient = cust_id if customer else mid
    if recipient in recipient_hold and recipient_hold[recipient] > now:
        return None  # honour our own earlier wait / decline / live-thread decision
    skey = trg.get("suppression_key") if isinstance(trg.get("suppression_key"), str) and trg.get("suppression_key") \
        else f"{trg.get('kind')}:{tid}"
    if (recipient, skey) in sent_suppression:
        return None
    urgency = trg.get("urgency") if isinstance(trg.get("urgency"), (int, float)) and not isinstance(trg.get("urgency"), bool) else 0
    return (-urgency, order, tid, trg, mid, merchant, category, cust_id if customer else None, customer, recipient, skey)


def _run_tick(data: dict) -> List[dict]:
    now = parse_dt(data.get("now")) or now_utc()
    counters["last_tick_now"] = now
    raw_ids = data.get("available_triggers") or []
    if not isinstance(raw_ids, list):
        return []

    candidates = []
    seen: Set[str] = set()
    for order, raw in enumerate(raw_ids):
        try:
            cand = _candidate(order, raw, now, seen)
        except Exception:  # one corrupt trigger must never cost the rest of the tick
            log.exception("tick: skipping trigger %r", raw)
            continue
        if cand:
            candidates.append(cand)

    candidates.sort(key=lambda x: (x[0], x[1]))
    actions: List[dict] = []
    used_recipients: Set[str] = set()
    for (_, _, tid, trg, mid, merchant, category, cust_id, customer, recipient, skey) in candidates:
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break
        if recipient in used_recipients:
            continue  # one message per recipient per tick; the rest wait for the next tick
        try:
            out = compose(category, merchant, trg, customer, now=now, exclude_items=sent_digest.get(mid, set()))
        except Exception:
            log.exception("tick: compose failed for %s", tid)
            continue
        body = out["body"]
        if not out.get("valid", True):
            log.warning("tick: dropped %s — body failed output validation", tid)
            continue
        if not body or body in sent_bodies:
            continue

        counters["conv"] += 1
        conv_id = f"conv_{recipient}_{trg.get('kind', 'msg')}_{counters['conv']}"
        state = ConversationState(conversation_id=conv_id, merchant_id=mid, customer_id=cust_id,
                                  trigger_id=tid, trigger_kind=trg.get("kind"))
        state.turns.append({"from": "vera", "msg": body, "turn": 1})
        conversations[conv_id] = state

        used_recipients.add(recipient)
        sent_bodies.add(body)
        sent_suppression.add((recipient, skey))
        if out.get("digest_item_id"):
            sent_digest.setdefault(mid, set()).add(out["digest_item_id"])

        actions.append({
            "conversation_id": conv_id,
            "merchant_id": mid,
            "customer_id": cust_id,
            "send_as": out["send_as"],
            "trigger_id": tid,
            "template_name": f"vera_{out.get('kind') or 'generic'}_v1",
            "template_params": out.get("template_params") or [],
            "body": body,
            "cta": out["cta"],
            "suppression_key": skey,
            "rationale": out["rationale"],
        })
    return actions


@app.post("/v1/reply")
@app.post("/reply")
async def handle_reply(request: Request):
    data, err = await read_json(request)
    if err:
        return JSONResponse({"action": "wait", "wait_seconds": 1800, "rationale": f"bad request: {err}"}, status_code=400)
    conv_id = data.get("conversation_id")
    message = data.get("message")
    if not isinstance(conv_id, str) or not conv_id or not isinstance(message, str):
        return JSONResponse({"action": "wait", "wait_seconds": 1800,
                             "rationale": "conversation_id and message are required"}, status_code=400)
    try:
        return _run_reply(data, conv_id, message)
    except Exception as e:
        log.exception("reply failed for %s", conv_id)
        return {"action": "wait", "wait_seconds": 1800, "rationale": f"internal fallback ({type(e).__name__})"}


def _as_int(x: Any, default: int = 0) -> int:
    try:
        return int(x)
    except (TypeError, ValueError):
        return default


def _run_reply(data: dict, conv_id: str, message: str) -> dict:
    from_role = data.get("from_role") if data.get("from_role") in ("merchant", "customer") else "merchant"
    data["turn_number"] = _as_int(data.get("turn_number"))
    state = conversations.get(conv_id)
    if state is None:
        state = ConversationState(conversation_id=conv_id, merchant_id=data.get("merchant_id"),
                                  customer_id=data.get("customer_id"))
        conversations[conv_id] = state
    state.merchant_id = state.merchant_id or (data.get("merchant_id") if isinstance(data.get("merchant_id"), str) else None)
    state.customer_id = state.customer_id or (data.get("customer_id") if isinstance(data.get("customer_id"), str) else None)

    mid, merchant = get_ctx("merchant", state.merchant_id)
    slug = (merchant or {}).get("category_slug")
    _, category = get_ctx("category", slug)
    _, trigger = get_ctx("trigger", state.trigger_id)
    _, customer = get_ctx("customer", state.customer_id) if from_role == "customer" or state.customer_id else (None, None)

    state.turns.append({"from": from_role, "msg": message, "turn": data.get("turn_number")})
    now = parse_dt(data.get("received_at")) or now_utc()
    sim_now = counters["last_tick_now"] or now  # judge's simulated clock, as seen on /v1/tick

    result = respond(state, message, merchant_context=merchant, category_context=category,
                     trigger_context=trigger, customer_context=customer, from_role=from_role,
                     merchant_auto_count=merchant_auto_counts.get(mid or "", 0), now=now)

    recipient = (state.customer_id if from_role == "customer" else mid) or mid
    if mid:
        merchant_auto_counts[mid] = state.auto_reply_count
        if state.closed_reason == "opt_out" and result["action"] == "end" and from_role == "merchant":
            opted_out[mid] = sim_now + timedelta(days=OPT_OUT_DAYS)
    if recipient:
        if result["action"] == "wait":
            hold = sim_now + timedelta(seconds=int(result.get("wait_seconds") or 0))
        elif result["action"] == "end":
            hold = sim_now + {"auto_reply": AUTO_REPLY_HOLD, "declined": DECLINE_HOLD}.get(state.closed_reason or "",
                                                                                         timedelta(0))
        else:
            hold = sim_now + ACTIVE_THREAD_HOLD
        recipient_hold[recipient] = max(hold, recipient_hold.get(recipient, hold))
    mark_dirty()

    if result["action"] == "send":
        state.turns.append({"from": "vera", "msg": result.get("body", ""), "turn": (data.get("turn_number") or 0) + 1})
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
