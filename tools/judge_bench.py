"""Reproducible quality benchmark using the OFFICIAL judge's scoring code.

Scores compose() output for the 30 canonical test pairs with judge_simulator.LLMScorer
(same system prompt, same rubric) via a local Ollama model at temperature 0 + fixed seed,
so before/after comparisons measure the change, not sampling noise.

    python tools/judge_bench.py out.json [--model gemma3:latest] [--set pairs|holdout] [--composer path/to/composer.py]
      --set holdout scores the 70 dataset triggers that are NOT canonical test pairs (guards against overfitting)
    python tools/judge_bench.py --compare before.json after.json
"""
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
DIMS = ["specificity", "category_fit", "merchant_fit", "decision_quality", "engagement_compulsion"]


def compare(a_path, b_path):
    a, b = json.load(open(a_path, encoding="utf-8")), json.load(open(b_path, encoding="utf-8"))
    print(f"{'test':5} {'before':>7} {'after':>6}  delta")
    tot_a = tot_b = 0
    for tid in sorted(a):
        ta, tb = a[tid]["total"], b.get(tid, {}).get("total", 0)
        tot_a, tot_b = tot_a + ta, tot_b + tb
        print(f"{tid:5} {ta:7} {tb:6}  {tb - ta:+d}")
    n = len(a)
    print(f"\nmean total: {tot_a / n:.2f} -> {tot_b / n:.2f}  ({(tot_b - tot_a) / n:+.2f})")
    for d in DIMS:
        ma = sum(x[d] for x in a.values()) / n
        mb = sum(x[d] for x in b.values()) / n
        print(f"  {d:22} {ma:5.2f} -> {mb:5.2f}  ({mb - ma:+.2f})")


if "--compare" in sys.argv:
    i = sys.argv.index("--compare")
    compare(sys.argv[i + 1], sys.argv[i + 2])
    sys.exit(0)

import importlib.util  # noqa: E402
import judge_simulator as j  # noqa: E402  (official file, unmodified)

if "--composer" in sys.argv:
    _spec = importlib.util.spec_from_file_location("alt_composer", sys.argv[sys.argv.index("--composer") + 1])
    _mod = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    compose = _mod.compose
else:
    from composer import compose  # noqa: E402
SET = sys.argv[sys.argv.index("--set") + 1] if "--set" in sys.argv else "pairs"

MODEL = sys.argv[sys.argv.index("--model") + 1] if "--model" in sys.argv else "gemma3:latest"


class DeterministicOllama(j.OllamaProvider):
    def complete(self, prompt, system=None):
        full = f"{system}\n\n{prompt}" if system else prompt
        req = urllib.request.Request(f"{self.api_url}/api/generate", headers={"Content-Type": "application/json"},
                                     data=json.dumps({"model": self.model, "prompt": full, "stream": False,
                                                      "options": {"temperature": 0, "seed": 42}}).encode())
        return json.loads(urllib.request.urlopen(req, timeout=180).read())["response"]


ds = ROOT / "dataset" / "expanded"
scorer = j.LLMScorer(DeterministicOllama(MODEL), None)
out = {}
pairs = json.load(open(ds / "test_pairs.json", encoding="utf-8"))["pairs"]
if SET == "holdout":
    used = {p["trigger_id"] for p in pairs}
    pairs = []
    for f in sorted((ds / "triggers").glob("*.json")):
        t = json.load(open(f, encoding="utf-8"))
        if t["id"] not in used:
            pairs.append({"test_id": t["id"][:7], "trigger_id": t["id"], "merchant_id": t["merchant_id"],
                          "customer_id": t.get("customer_id")})
    if "--sample" in sys.argv:  # evenly spaced subset, same every run
        k = int(sys.argv[sys.argv.index("--sample") + 1])
        pairs = [pairs[round(i * (len(pairs) - 1) / (k - 1))] for i in range(k)] if k > 1 else pairs[:1]
for p in pairs:
    t = json.load(open(ds / "triggers" / f"{p['trigger_id']}.json", encoding="utf-8"))
    m = json.load(open(ds / "merchants" / f"{p['merchant_id']}.json", encoding="utf-8"))
    c = json.load(open(ds / "categories" / f"{m['category_slug']}.json", encoding="utf-8"))
    cu = json.load(open(ds / "customers" / f"{p['customer_id']}.json", encoding="utf-8")) if p.get("customer_id") else None
    action = compose(c, m, t, cu)
    s = scorer.score(action, c, m, t, cu)
    out[p["test_id"]] = {"kind": t["kind"], "body": action["body"], "total": s.total, "hint": s.hint,
                         **{d: getattr(s, d) for d in DIMS},
                         "reasons": {d: getattr(s, d.replace("engagement_compulsion", "engagement") + "_reason", "") for d in DIMS}}
    print(f"{p['test_id']} {t['kind']:26} {s.total:2}/50  " + " ".join(f"{getattr(s, d)}" for d in DIMS), flush=True)
json.dump(out, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
n = len(out)
print(f"\nmean total {sum(x['total'] for x in out.values()) / n:.2f}/50 | " +
      " ".join(f"{d[:5]} {sum(x[d] for x in out.values()) / n:.2f}" for d in DIMS))
