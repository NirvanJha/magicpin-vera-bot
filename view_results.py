#!/usr/bin/env python3
"""
magicpin AI Challenge — Results Viewer
Displays the composed messages and score dimensions for all test pairs in submission.jsonl.
"""

import json
from pathlib import Path

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    DIM = '\033[2m'
    RESET = '\033[0m'

def main():
    sub_file = Path(__file__).parent / "submission.jsonl"
    pairs_file = Path(__file__).parent / "dataset" / "expanded" / "test_pairs.json"
    
    if not sub_file.exists():
        print(f"{Colors.RED}submission.jsonl not found!{Colors.RESET}")
        return
        
    pairs_map = {}
    if pairs_file.exists():
        with open(pairs_file) as f:
            pairs_data = json.load(f).get("pairs", [])
            for p in pairs_data:
                pairs_map[p["test_id"]] = p

    print(f"\n{Colors.HEADER}{Colors.BOLD}{'='*75}{Colors.RESET}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'magicpin AI Challenge — Vera Bot Results':^75}{Colors.RESET}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*75}{Colors.RESET}\n")

    lines = [json.loads(line) for line in sub_file.read_text(encoding="utf-8").strip().split("\n") if line.strip()]
    print(f"{Colors.GREEN}[INFO]{Colors.RESET} Loaded {len(lines)} composed evaluation pairs from submission.jsonl\n")

    for item in lines:
        t_id = item["test_id"]
        pair_info = pairs_map.get(t_id, {})
        m_id = pair_info.get("merchant_id", "N/A")
        trg_id = pair_info.get("trigger_id", "N/A")
        c_id = pair_info.get("customer_id")
        
        print(f"{Colors.CYAN}{Colors.BOLD}--- [{t_id}] Trigger: {trg_id} ---{Colors.RESET}")
        print(f"  {Colors.BOLD}Merchant:{Colors.RESET} {m_id}")
        if c_id:
            print(f"  {Colors.BOLD}Customer:{Colors.RESET} {c_id}")
        print(f"  {Colors.BOLD}Send As:{Colors.RESET}  {Colors.YELLOW}{item['send_as']}{Colors.RESET} | {Colors.BOLD}CTA:{Colors.RESET} {item['cta']}")
        print(f"  {Colors.BOLD}Message Body:{Colors.RESET}")
        
        # Indent body
        for line in item['body'].split("\n"):
            print(f"    {Colors.GREEN}{line}{Colors.RESET}")
            
        print(f"  {Colors.BOLD}Rationale:{Colors.RESET} {Colors.DIM}{item['rationale']}{Colors.RESET}")
        print()

    # Summary Stats
    customer_facing = sum(1 for x in lines if x.get("send_as") == "merchant_on_behalf")
    merchant_facing = sum(1 for x in lines if x.get("send_as") == "vera")
    binary_ctas = sum(1 for x in lines if x.get("cta") == "binary")
    open_ctas = sum(1 for x in lines if x.get("cta") == "open_ended")

    print(f"{Colors.HEADER}{Colors.BOLD}{'='*75}{Colors.RESET}")
    print(f"{Colors.BOLD}SUMMARY METRICS across {len(lines)} evaluation pairs:{Colors.RESET}")
    print(f"  - Merchant-Facing Messages (Vera): {merchant_facing}")
    print(f"  - Customer-Facing Messages (on behalf of Merchant): {customer_facing}")
    print(f"  - Binary Commit CTAs (Reply 1/2, YES/STOP): {binary_ctas}")
    print(f"  - Open-ended / Curiosity CTAs: {open_ctas}")
    print(f"{Colors.HEADER}{Colors.BOLD}{'='*75}{Colors.RESET}\n")

if __name__ == "__main__":
    main()
