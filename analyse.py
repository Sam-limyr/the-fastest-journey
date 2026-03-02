#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyse.py — Singapore MRT commute analysis

Computes commute scores for MRT stations and opens an interactive HTML map.
Can also print the destination weights that form the scoring centroid.

Usage:
    python analyse.py [--display <mode>] [--scope <scope>]
    python analyse.py --weights [--top N] [--bottom N]

Examples:
    python analyse.py
    python analyse.py --display log --scope residential
    python analyse.py --display minutes --scope all
    python analyse.py --display linear --scope hdb
    python analyse.py --weights
    python analyse.py --weights --top 10
    python analyse.py --weights --bottom 10
    python analyse.py --weights --top 5 --bottom 5
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_driven_rankings import ANALYSIS_MONTH, get_destination_weights
from visualize_scores import build_map, OUTPUT_HTML


def _print_weights_table(rows: list[tuple[int, str, float]], total: int) -> None:
    """Print a formatted weights table for the given (rank, station, weight) rows."""
    print(f"{'Rank':>4}  {'Station':<32}  {'Weight':>7}  {'Cumulative':>10}")
    print("-" * 62)
    cumulative = 0.0
    for rank, station, weight in rows:
        cumulative += weight
        print(
            f"{rank:>4}  {station:<32}  {weight * 100:>6.2f}%"
            f"  {cumulative * 100:>9.2f}%"
        )
    print("-" * 62)
    print(f"Showing {len(rows)} of {total} stations")


def cmd_weights(top: int | None, bottom: int | None) -> None:
    """Print destination weights, optionally filtered to the top and/or bottom N."""
    weights = get_destination_weights(year_month=ANALYSIS_MONTH)
    year, month = ANALYSIS_MONTH[:4], ANALYSIS_MONTH[4:]
    all_rows = [(rank, stn, w) for rank, (stn, w) in enumerate(weights.items(), 1)]
    total = len(all_rows)

    header = f"\n=== Destination weights  (all-hours weighted, {year}-{month}) ==="

    if top is None and bottom is None:
        print(header)
        _print_weights_table(all_rows, total)
        return

    if top is not None:
        print(header + f"  [top {top}]")
        _print_weights_table(all_rows[:top], total)

    if bottom is not None:
        # Bottom N: lowest weights, shown ascending (least influential first)
        bottom_rows = all_rows[-bottom:][::-1]
        sep = "\n" if top is not None else ""
        print(sep + header + f"  [bottom {bottom}]")
        _print_weights_table(bottom_rows, total)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Singapore MRT commute analyser.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Map mode (default):\n"
            "  python analyse.py\n"
            "  python analyse.py --display log --scope residential\n"
            "  python analyse.py --display minutes --scope all\n"
            "  python analyse.py --display linear --scope hdb\n"
            "\n"
            "  All 9 combinations of --display and --scope are valid.\n"
            "  After analysis, the HTML map is opened automatically.\n"
            "\n"
            "Weights mode:\n"
            "  python analyse.py --weights\n"
            "  python analyse.py --weights --top 10\n"
            "  python analyse.py --weights --bottom 10\n"
            "  python analyse.py --weights --top 5 --bottom 5\n"
            "\n"
            "  Prints the destination weights used for the centroid.\n"
            "  --top N   shows the N highest-weighted stations (descending).\n"
            "  --bottom N shows the N lowest-weighted stations (ascending).\n"
            "  Omit both to print all stations.\n"
            "  --display and --scope are ignored in weights mode."
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
    parser.add_argument(
        "--weights",
        action="store_true",
        help=(
            "Print the destination weights used for the scoring centroid, then exit. "
            "Combine with --top / --bottom to filter the output."
        ),
    )
    parser.add_argument(
        "--top",
        type=int,
        metavar="N",
        default=None,
        help="With --weights: show only the N highest-weighted stations (descending).",
    )
    parser.add_argument(
        "--bottom",
        type=int,
        metavar="N",
        default=None,
        help="With --weights: show only the N lowest-weighted stations (ascending).",
    )
    args = parser.parse_args()

    if args.weights or args.top is not None or args.bottom is not None:
        cmd_weights(top=args.top, bottom=args.bottom)
        return

    print(f"==> display={args.display}  scope={args.scope}\n")
    build_map(scoring_method=args.display, station_scope=args.scope)
    print(f"\nOpening map: {OUTPUT_HTML}")
    webbrowser.open(OUTPUT_HTML)


if __name__ == "__main__":
    main()
