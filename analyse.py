#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyse.py — Singapore MRT commute analysis

Computes commute scores for MRT stations and opens an interactive HTML map.

Usage:
    python analyse.py [--display <mode>] [--scope <scope>]

Examples:
    python analyse.py
    python analyse.py --display log --scope residential
    python analyse.py --display minutes --scope all
    python analyse.py --display linear --scope hdb
"""

import argparse
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from visualize_scores import build_map, OUTPUT_HTML


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML map of Singapore MRT commute scores.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python analyse.py\n"
            "  python analyse.py --display log --scope residential\n"
            "  python analyse.py --display minutes --scope all\n"
            "  python analyse.py --display linear --scope hdb\n"
            "\n"
            "All 9 combinations of --display and --scope are valid.\n"
            "After analysis, the HTML map is opened automatically."
        ),
    )
    parser.add_argument(
        "--display",
        choices=["minutes", "linear", "log"],
        default="log",
        help=(
            "minutes  — colour by raw expected commute time (no 1-10 scale)\n"
            "linear   — 1-10 score using z-scores of raw minutes\n"
            "log      — 1-10 score using z-scores of log(minutes)  [default]"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=["hdb", "residential", "all"],
        default="residential",
        help=(
            "hdb         — HDB estate stations only\n"
            "residential — all residential MRT stations  [default]\n"
            "all         — every station in the travel-time dataset"
        ),
    )
    args = parser.parse_args()

    print(f"==> display={args.display}  scope={args.scope}\n")

    build_map(scoring_method=args.display, station_scope=args.scope)

    print(f"\nOpening map: {OUTPUT_HTML}")
    webbrowser.open(OUTPUT_HTML)


if __name__ == "__main__":
    main()
