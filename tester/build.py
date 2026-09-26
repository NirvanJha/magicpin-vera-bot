"""Builds tester/index.html: template.html + 10 real scenarios from ./dataset (self-contained, no server needed).

    python tester/build.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
D = ROOT / "dataset"
SCENARIOS = [
    ("Dentist · research digest", "trg_001_research_digest_dentists"),
    ("Dentist · competitor opened", "trg_023_competitor_opened_dentist"),
    ("Dentist · compliance deadline", "trg_002_compliance_dci_radiograph"),
    ("Customer · dental recall (Priya, Hinglish)", "trg_003_recall_due_priya"),
    ("Restaurant · IPL match tonight", "trg_010_ipl_match_delhi"),
    ("Restaurant · corporate thali draft", "trg_013_corporate_thali_planning"),
    ("Gym · seasonal dip", "trg_014_seasonal_acquisition_dip_powerhouse"),
    ("Pharmacy · drug recall alert", "trg_018_supply_atorvastatin_recall"),
    ("Pharmacy · chronic refill (senior)", "trg_019_chronic_refill_grandfather"),
    ("Salon · bridal follow-up", "trg_007_bridal_followup_kavya"),
]


def load(name):
    return json.load(open(D / name, encoding="utf-8"))


cats = {f.stem: json.load(open(f, encoding="utf-8")) for f in (D / "categories").glob("*.json")}
merchants = {m["merchant_id"]: m for m in load("merchants_seed.json")["merchants"]}
customers = {c["customer_id"]: c for c in load("customers_seed.json")["customers"]}
triggers = {t["id"]: t for t in load("triggers_seed.json")["triggers"]}

data = {"categories": {}, "merchants": {}, "customers": {}, "scenarios": []}
for label, tid in SCENARIOS:
    t = triggers[tid]
    m = merchants[t["merchant_id"]]
    data["categories"][m["category_slug"]] = cats[m["category_slug"]]
    data["merchants"][m["merchant_id"]] = m
    if t.get("customer_id"):
        data["customers"][t["customer_id"]] = customers[t["customer_id"]]
    data["scenarios"].append({"label": label, "trigger": t, "merchant_id": m["merchant_id"],
                              "category": m["category_slug"], "customer_id": t.get("customer_id")})

html = (ROOT / "tester" / "template.html").read_text(encoding="utf-8")
blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # safe inside <script>
(ROOT / "tester" / "index.html").write_text(html.replace("__SAMPLES__", blob), encoding="utf-8")
print(f"tester/index.html written ({len(data['scenarios'])} scenarios)")
