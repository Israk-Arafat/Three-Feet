"""Extract the heater's clearance rule from its PDF manual with Muse Wonder, then approve it.

    python scripts/extract_rules.py data/manuals/my_heater.pdf
    python scripts/extract_rules.py --find "Lasko 754200"   # Muse Spark finds the manual on the web
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from three_feet.config import RULES_PATH, get_settings  # noqa: E402
from three_feet.perception.manual_finder import ManualFinder, ManualNotFound  # noqa: E402
from three_feet.perception.meta_client import CostMeter, make_client  # noqa: E402
from three_feet.perception.wonder import WonderClient, rule_from_extraction  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("pdf", nargs="?", help="path to the heater's PDF manual")
    source.add_argument("--find", metavar="HEATER", help="brand and model; search the web for its manual")
    ap.add_argument("--yes", action="store_true", help="approve without prompting")
    args = ap.parse_args()

    s = get_settings()
    meter = CostMeter(s.budget_usd_per_hour)
    wonder = WonderClient(make_client(s), meter)
    manual_url = ""
    if args.find:
        finder = ManualFinder(wonder)
        print(f"Searching the web for the {args.find} manual with {finder.model} ...")
        try:
            reading = finder.find(args.find)
        except ManualNotFound as exc:
            print(exc)
            for c in exc.candidates:
                print(f"  {c['url']}  ({c.get('error', 'not tried')})")
            return 1
        data, manual_url = reading.extraction, reading.url
        print(f"Found {reading.heater_model}: {reading.url}\nRead by {reading.read_by}"
              + (f", saved to {reading.path}" if reading.path else ""))
    else:
        print(f"Reading {args.pdf} with {wonder.model} ...")
        data = wonder.extract_clearance(args.pdf)
    print(json.dumps(data, indent=2))

    rule = rule_from_extraction(data, s.default_clearance_in)
    rule.manual_url = manual_url
    print("\nRule that will be enforced (stricter of manual and "
          f"{s.default_clearance_in:.0f} in minimum):")
    print(f"  front {rule.front_in:.0f} in | back {rule.back_in:.0f} in | "
          f"left {rule.left_in:.0f} in | right {rule.right_in:.0f} in  (source: {rule.source})")
    if rule.quote:
        print(f'  manual p.{rule.page}: "{rule.quote}"')
    if rule.manual_url:
        print(f"  source: {rule.manual_url}")

    if not args.yes and input("\nApprove this rule? [y/N] ").strip().lower() != "y":
        print("Not saved.")
        return 1
    rule.approve()
    rule.save()
    print(f"Saved {RULES_PATH}  (cost ${meter.total_usd:.4f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
