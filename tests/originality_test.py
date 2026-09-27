"""Originality guard: no composed message may be a near-duplicate of an official case study.

examples/case-studies.md: "direct copying the body text of a case study counts as plagiarism — the judge
runs a similarity check". Shared DATA (names, dates, slots) is unavoidable, so the limits are set above
that floor: sequence similarity < 0.50 and shared 4-word phrases < 20% for every (case, message) pair.
    python tests/originality_test.py
"""
import difflib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import bot  # noqa: E402
from composer import compose  # noqa: E402

text = (ROOT / "examples" / "case-studies.md").read_text(encoding="utf-8")
cases = []
for block in text.split("## Case Study ")[1:]:
    m = re.search(r"\*\*Composed message\*\*[^\n]*\n```\n(.*?)\n```", block, flags=re.S)
    if m:
        cases.append((block.splitlines()[0], " ".join(m.group(1).split())))


def words(s):
    return re.findall(r"[a-z0-9₹%]+", s.lower())


def grams(s, n=4):
    w = words(s)
    return {tuple(w[i:i + n]) for i in range(len(w) - n + 1)}


bodies = []
for (scope, tid), v in bot.seed.items():
    if scope == "trigger":
        t = v["payload"]
        _, m = bot.get_ctx("merchant", t.get("merchant_id"))
        _, c = bot.get_ctx("category", m["category_slug"])
        _, cu = bot.get_ctx("customer", t.get("customer_id"))
        bodies.append((tid, compose(c, m, t, cu)["body"]))

worst_sim = worst_gram = 0.0
fails = []
for title, cb in cases:
    for tid, body in bodies:
        sim = difflib.SequenceMatcher(None, words(cb), words(body)).ratio()
        gram = len(grams(cb) & grams(body)) / max(1, len(grams(cb)))
        worst_sim, worst_gram = max(worst_sim, sim), max(worst_gram, gram)
        if sim >= 0.50 or gram >= 0.20:
            fails.append((title[:40], tid, round(sim, 2), f"{gram:.0%}"))
for f in fails:
    print("  TOO SIMILAR:", f)
print(f"RESULT: {len(cases)} case studies x {len(bodies)} messages — max similarity {worst_sim:.2f}, "
      f"max shared 4-grams {worst_gram:.0%}, {len(fails)} near-duplicates")
sys.exit(1 if fails else 0)
