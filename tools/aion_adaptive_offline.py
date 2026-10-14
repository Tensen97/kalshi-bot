#!/usr/bin/env python3
"""AION offline adaptive research CLI; no network access or live trading."""
import argparse
import json
from aion_adaptive.pipeline import import_closed_trades, offline_health_report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    importer = sub.add_parser("import-closed")
    importer.add_argument("csv")
    importer.add_argument("journal")
    health = sub.add_parser("diagnose")
    health.add_argument("journal")
    health.add_argument("report")
    args = parser.parse_args()
    if args.action == "import-closed":
        result = import_closed_trades(args.csv, args.journal)
    else:
        result = offline_health_report(args.journal, args.report)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
