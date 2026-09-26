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
import os
import re
import time
from datetime import datetime, timedelta, timezone
from itertools import count
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from composer import compose, parse_dt  # noqa: F401  (compose re-exported)
from conversation_handlers import ConversationState, respond


app = FastAPI(title="magicpin Vera Bot", version="2.0.0")
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
merchant_auto_counts: Dict[str, int] = {}
_conv_counter = count(1)


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


# =============================================================================
# LOOKUPS
# =============================================================================

def get_ctx(scope: str, cid: Optional[str]) -> Tuple[Optional[str], Optional[dict]]:
    """Exact id first (pushed, then seed); then a UNIQUE '<id>_' prefix match."""
    if not cid:
        return None, None
    for store in (contexts, seed):
        if (scope, cid) in store:
            return cid, store[(scope, cid)]["payload"]
    for store in (contexts, seed):
        hits = [k[1] for k in store if k[0] == scope and (k[1].startswith(cid + "_") or cid.startswith(k[1] + "_"))]
        if len(hits) == 1:
            return hits[0], store[(scope, hits[0])]["payload"]
    return cid, None


def resolve_trigger(raw: str) -> Tuple[Optional[str], Optional[dict]]:
    tid, trg = get_ctx("trigger", raw)
    if trg:
        return tid, trg
    # 'trg_research_digest_dentists' == 'trg_001_research_digest_dentists' (exact after stripping the number)
    norm = re.sub(r"^trg_\d+_", "trg_", raw)
    for store in (contexts, seed):
        hits = [k[1] for k in store if k[0] == "trigger" and re.sub(r"^trg_\d+_", "trg_", k[1]) == norm]
        if len(hits) == 1:
            return hits[0], store[("trigger", hits[0])]["payload"]
    return None, None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="milliseconds").replace("+00:00", "Z")


async def read_json(request: Request) -> Tuple[Optional[dict], Optional[str]]:
    """Parse the body as JSON regardless of Content-Type (curl -d sends form-encoded)."""
    raw = await request.body()
    if len(raw) > MAX_CONTEXT_BYTES:
        return None, "payload_too_large"
    try:
        data = json.loads(raw.decode("utf-8-sig") or "{}")
    except (ValueError, UnicodeDecodeError) as e:
        return None, f"malformed_json: {e}"
    if not isinstance(data, dict):
        return None, "body must be a JSON object"
    return data, None


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
        "version": "2.0.0",
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
    return {"accepted": True, "ack_id": f"ack_{cid}_v{version}", "stored_at": stored_at}


@app.post("/v1/tick")
@app.post("/tick")
async def tick(request: Request):
    data, err = await read_json(request)
    if err:
        return JSONResponse({"actions": [], "error": err}, status_code=400)
    try:
        return {"actions": _run_tick(data)}
    except Exception as e:  # never 500 on the judge
        return {"actions": [], "error": f"internal: {type(e).__name__}"}


def _run_tick(data: dict) -> List[dict]:
    now = parse_dt(data.get("now")) or now_utc()
    raw_ids = data.get("available_triggers") or []
    if not isinstance(raw_ids, list):
        return []

    candidates = []
    seen: Set[str] = set()
    for order, raw in enumerate(raw_ids):
        if not isinstance(raw, str):
            continue
        tid, trg = resolve_trigger(raw)
        if not trg or tid in seen:
            continue
        seen.add(tid)

        # No expires_at filtering: available_triggers is the judge's own list of what's active
        # right now, and its simulated clock may not line up with the dataset's dates.
        mid, merchant = get_ctx("merchant", trg.get("merchant_id"))
        if not merchant:
            continue
        if mid in opted_out and opted_out[mid] > now:
            continue
        slug = merchant.get("category_slug") or (trg.get("payload") or {}).get("category")
        _, category = get_ctx("category", slug)
        if not category:
            continue
        cust_id, customer = get_ctx("customer", trg.get("customer_id"))
        if trg.get("scope") == "customer" and not customer:
            continue  # can't address a customer we know nothing about
        recipient = cust_id if customer else mid
        skey = trg.get("suppression_key") or f"{trg.get('kind')}:{tid}"
        if (recipient, skey) in sent_suppression:
            continue
        urgency = trg.get("urgency") if isinstance(trg.get("urgency"), (int, float)) else 0
        candidates.append((-urgency, order, tid, trg, mid, merchant, category, cust_id if customer else None,
                           customer, recipient, skey))

    candidates.sort(key=lambda x: (x[0], x[1]))
    actions: List[dict] = []
    used_recipients: Set[str] = set()
    for (_, _, tid, trg, mid, merchant, category, cust_id, customer, recipient, skey) in candidates:
        if len(actions) >= MAX_ACTIONS_PER_TICK:
            break
        if recipient in used_recipients:
            continue  # one message per recipient per tick; the rest wait for the next tick
        out = compose(category, merchant, trg, customer, now=now, exclude_items=sent_digest.get(mid, set()))
        body = out["body"]
        if not body or body in sent_bodies:
            continue

        conv_id = f"conv_{recipient}_{trg.get('kind', 'msg')}_{next(_conv_counter)}"
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
            "suppression_key": out["suppression_key"],
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
        return {"action": "wait", "wait_seconds": 1800, "rationale": f"internal fallback ({type(e).__name__})"}


def _run_reply(data: dict, conv_id: str, message: str) -> dict:
    from_role = data.get("from_role") or "merchant"
    state = conversations.get(conv_id)
    if state is None:
        state = ConversationState(conversation_id=conv_id, merchant_id=data.get("merchant_id"),
                                  customer_id=data.get("customer_id"))
        conversations[conv_id] = state
    state.merchant_id = state.merchant_id or data.get("merchant_id")
    state.customer_id = state.customer_id or data.get("customer_id")

    mid, merchant = get_ctx("merchant", state.merchant_id)
    slug = (merchant or {}).get("category_slug")
    _, category = get_ctx("category", slug)
    _, trigger = get_ctx("trigger", state.trigger_id)
    _, customer = get_ctx("customer", state.customer_id) if from_role == "customer" or state.customer_id else (None, None)

    state.turns.append({"from": from_role, "msg": message, "turn": data.get("turn_number")})
    now = parse_dt(data.get("received_at")) or now_utc()

    result = respond(state, message, merchant_context=merchant, category_context=category,
                     trigger_context=trigger, customer_context=customer, from_role=from_role,
                     merchant_auto_count=merchant_auto_counts.get(mid or "", 0), now=now)

    if mid:
        merchant_auto_counts[mid] = state.auto_reply_count
        if state.closed_reason == "opt_out" and result["action"] == "end" and from_role == "merchant":
            opted_out[mid] = now + timedelta(days=OPT_OUT_DAYS)

    if result["action"] == "send":
        state.turns.append({"from": "vera", "msg": result.get("body", ""), "turn": (data.get("turn_number") or 0) + 1})
    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
