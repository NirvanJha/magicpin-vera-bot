"""Offline contract test against challenge-brief §7 (deliverables). No server needed.

    python tests/spec_test.py
"""
import inspect
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bot  # noqa: E402
from composer import compose  # noqa: E402
from conversation_handlers import ConversationState, respond  # noqa: E402

results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  -> {str(detail)[:200]}" if detail and not ok else ""))


CTA = {"open_ended", "binary", "none"}
SEND_AS = {"vera", "merchant_on_behalf"}

# §7.1 compose(category, merchant, trigger, customer) -> dict with 5 keys
sig = list(inspect.signature(bot.compose).parameters)
check("§7.1 bot.compose exists with (category, merchant, trigger, customer, ...)", sig[:4] == ["category", "merchant", "trigger", "customer"], sig)

pairs = json.load(open(ROOT / "dataset/expanded/test_pairs.json", encoding="utf-8"))["pairs"]


def ctx_for(pair):
    t = json.load(open(ROOT / f"dataset/expanded/triggers/{pair['trigger_id']}.json", encoding="utf-8"))
    m = json.load(open(ROOT / f"dataset/expanded/merchants/{pair['merchant_id']}.json", encoding="utf-8"))
    c = json.load(open(ROOT / f"dataset/expanded/categories/{m['category_slug']}.json", encoding="utf-8"))
    cu = json.load(open(ROOT / f"dataset/expanded/customers/{pair['customer_id']}.json", encoding="utf-8")) if pair.get("customer_id") else None
    return c, m, t, cu


problems, slow = [], 0
for p in pairs:
    c, m, t, cu = ctx_for(p)
    t0 = time.time()
    a = compose(c, m, t, cu)
    dt = time.time() - t0
    slow = max(slow, dt)
    b = compose(c, m, t, cu)
    if a != b:
        problems.append((p["test_id"], "non-deterministic"))
    for k in ("body", "cta", "send_as", "suppression_key", "rationale"):
        if not isinstance(a.get(k), str) or not a[k].strip():
            problems.append((p["test_id"], f"missing/empty {k}"))
    if a.get("cta") not in CTA or a.get("send_as") not in SEND_AS:
        problems.append((p["test_id"], a.get("cta"), a.get("send_as")))
    if a.get("send_as") != ("merchant_on_behalf" if cu else "vera"):
        problems.append((p["test_id"], "send_as does not match customer presence"))
    if not a.get("valid"):
        problems.append((p["test_id"], "failed output validation"))
check("§7.1 all 30 test pairs: 5 required keys, valid enums, correct send_as, deterministic, validated", not problems, problems[:5])
check(f"§7.1 compose < 30s per call (slowest {slow * 1000:.1f}ms)", slow < 30)

# §7.2 submission.jsonl: 30 lines, T01..T30, required keys, and it regenerates byte-identically
lines = (ROOT / "submission.jsonl").read_text(encoding="utf-8").strip().split("\n")
rows = [json.loads(line) for line in lines]
check("§7.2 submission.jsonl has 30 lines", len(rows) == 30, len(rows))
check("§7.2 test_ids are exactly T01..T30", [r["test_id"] for r in rows] == [f"T{i:02d}" for i in range(1, 31)])
check("§7.2 every line has body/cta/send_as/suppression_key/rationale",
      all(all(isinstance(r.get(k), str) and r[k] for k in ("body", "cta", "send_as", "suppression_key", "rationale")) for r in rows))
check("§7.2 no mojibake in submission (UTF-8 clean)", "â‚¹" not in "\n".join(lines))
with tempfile.TemporaryDirectory() as tmp:
    before = (ROOT / "submission.jsonl").read_bytes()
    subprocess.run([sys.executable, str(ROOT / "generate_submission.py")], cwd=ROOT, check=True, capture_output=True)
    after = (ROOT / "submission.jsonl").read_bytes()
    check("§7.2 submission.jsonl regenerates byte-identically (deterministic)", before.replace(b"\r\n", b"\n") == after.replace(b"\r\n", b"\n"))

# §7.4 respond(state, merchant_message) — the brief's 2-argument form must work
st = ConversationState(conversation_id="spec")
st.turns.append({"from": "merchant", "msg": "Ok lets do it. Whats next?"})
r = respond(st, "Ok lets do it. Whats next?")
check("§7.4 respond(state, merchant_message) works with just 2 args", r.get("action") in ("send", "wait", "end") and r.get("rationale"), r)
check("§7.4 commitment -> action, not another question", r["action"] == "send" and "?" not in r.get("body", "").split(".")[-1])

# §7.3 README is one page
readme = (ROOT / "README.md").read_text(encoding="utf-8")
words = len(readme.split())
check(f"§7.3 README.md is about one page ({words} words <= 650)", words <= 650, words)
for section in ("approach", "tradeoff", "additional context"):
    check(f"§7.3 README covers '{section}'", section in readme.lower())

print(f"\nRESULT: {sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
