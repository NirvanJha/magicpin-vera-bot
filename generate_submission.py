"""
Generate submission.jsonl for magicpin AI Challenge
Runs the 30 canonical test pairs from dataset/expanded/test_pairs.json
through bot.compose() and outputs submission.jsonl.
"""

import json
from pathlib import Path
from bot import compose

def main():
    base_dir = Path(__file__).parent / "dataset" / "expanded"
    test_pairs_path = base_dir / "test_pairs.json"
    
    if not test_pairs_path.exists():
        print("test_pairs.json not found! Please run generate_dataset.py first.")
        return
        
    with open(test_pairs_path) as f:
        pairs = json.load(f).get("pairs", [])
        
    print(f"Loaded {len(pairs)} test pairs.")
    
    categories = {}
    for f in (base_dir / "categories").glob("*.json"):
        with open(f) as fp:
            data = json.load(fp)
            categories[data["slug"]] = data
            
    merchants = {}
    for f in (base_dir / "merchants").glob("*.json"):
        with open(f) as fp:
            data = json.load(fp)
            merchants[data["merchant_id"]] = data
            
    customers = {}
    for f in (base_dir / "customers").glob("*.json"):
        with open(f) as fp:
            data = json.load(fp)
            customers[data["customer_id"]] = data
            
    triggers = {}
    for f in (base_dir / "triggers").glob("*.json"):
        with open(f) as fp:
            data = json.load(fp)
            triggers[data["id"]] = data
            
    submission_lines = []
    
    for pair in pairs:
        test_id = pair["test_id"]
        t_id = pair["trigger_id"]
        m_id = pair["merchant_id"]
        c_id = pair.get("customer_id")
        
        trigger = triggers.get(t_id, {})
        merchant = merchants.get(m_id, {})
        category = categories.get(merchant.get("category_slug", ""), {})
        customer = customers.get(c_id) if c_id else None
        
        composed = compose(category, merchant, trigger, customer)
        
        entry = {
            "test_id": test_id,
            "body": composed["body"],
            "cta": composed["cta"],
            "send_as": composed["send_as"],
            "suppression_key": composed["suppression_key"],
            "rationale": composed["rationale"]
        }
        submission_lines.append(json.dumps(entry, ensure_ascii=False))
        
    out_file = Path(__file__).parent / "submission.jsonl"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write("\n".join(submission_lines) + "\n")
        
    print(f"Successfully generated {out_file} with {len(submission_lines)} lines.")

if __name__ == "__main__":
    main()
