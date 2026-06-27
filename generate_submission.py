"""
Generate submission.jsonl from the dataset by composing one message per
(merchant, trigger) pair in dataset/triggers_seed.json.

Usage:
    python generate_submission.py            # uses template composer (no key needed)
    ANTHROPIC_API_KEY=sk-... python generate_submission.py   # uses Claude

Writes submission.jsonl next to this script.
"""
from __future__ import annotations

import json
from pathlib import Path

from composer import compose

ROOT = Path(__file__).resolve().parent.parent          # g:\VERA
DATASET = ROOT / "dataset"
OUT = Path(__file__).resolve().parent / "submission.jsonl"


def load_categories() -> dict:
    cats = {}
    for f in (DATASET / "categories").glob("*.json"):
        d = json.loads(f.read_text(encoding="utf-8"))
        cats[d["slug"]] = d
    return cats


def load_seed(name: str, key: str) -> dict:
    d = json.loads((DATASET / name).read_text(encoding="utf-8"))
    items = d[key]
    id_field = {"merchants": "merchant_id", "customers": "customer_id", "triggers": "id"}[key]
    return {item[id_field]: item for item in items}


def main() -> None:
    categories = load_categories()
    merchants = load_seed("merchants_seed.json", "merchants")
    customers = load_seed("customers_seed.json", "customers")
    triggers = load_seed("triggers_seed.json", "triggers")

    lines = []
    for i, (tid, trigger) in enumerate(triggers.items(), start=1):
        merchant = merchants.get(trigger.get("merchant_id"))
        if not merchant:
            continue
        category = categories.get(merchant.get("category_slug"), {"slug": merchant.get("category_slug")})
        customer = customers.get(trigger.get("customer_id")) if trigger.get("customer_id") else None

        msg = compose(category, merchant, trigger, customer)
        lines.append({
            "test_id": f"T{i:02d}",
            "merchant_id": trigger["merchant_id"],
            "trigger_id": tid,
            "customer_id": trigger.get("customer_id"),
            "body": msg["body"],
            "cta": msg["cta"],
            "send_as": msg["send_as"],
            "template_name": msg["template_name"],
            "template_params": msg["template_params"],
            "suppression_key": msg["suppression_key"],
            "rationale": msg["rationale"],
        })

    OUT.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines) + "\n",
                   encoding="utf-8")
    print(f"Wrote {len(lines)} messages to {OUT}")


if __name__ == "__main__":
    main()
