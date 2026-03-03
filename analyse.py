#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyse.py — Singapore MRT commute analysis

Computes commute scores for MRT stations and opens an interactive HTML map.
Can also print the destination weights that form the scoring centroid.

Usage:
    python analyse.py [--display <mode>] [--scope <scope>] [--weight-method <method>]
                      [--from <hour>] [--to <hour>]
    python analyse.py --weights [--top N] [--bottom N]
                      [--weight-method <method>] [--from <hour>] [--to <hour>]

Examples:
    python analyse.py
    python analyse.py --display log --scope residential
    python analyse.py --display minutes --scope all
    python analyse.py --display linear --scope hdb
    python analyse.py --display weights
    python analyse.py --weight-method morning_peak
    python analyse.py --from 8 --to 22
    python analyse.py --weights
    python analyse.py --weights --top 10
    python analyse.py --weights --bottom 10
    python analyse.py --weights --top 5 --bottom 5
    python analyse.py --weights --weight-method all_hours_weighted
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_driven_rankings import (
    ANALYSIS_MONTH,
    DESTINATIONS_START_HOUR,
    DESTINATIONS_END_HOUR,
    get_destination_weights,
)
from visualize_scores import build_map, build_weights_map, OUTPUT_HTML, OUTPUT_WEIGHTS_HTML

_WEIGHT_METHOD_LABELS = {
    "destinations":                    "real-world destinations (wd ×5 + wknd ×2)",
    "real_world_commuter_destinations": "real-world destinations (wd ×5 + wknd ×2)",
    "work_and_leisure":                "work & leisure (wd 07:00–10:00 ×5 + wknd 07:00–19:00 ×2)",
    "morning_peak":                    "morning peak (weekday 7–10am)",
    "all_hours_weighted":              "all-hours weighted",
}

_WEIGHT_METHOD_CHOICES = [
    "destinations",
    "real_world_commuter_destinations",
    "work_and_leisure",
    "morning_peak",
    "all_hours_weighted",
]


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


def cmd_weights(
    top: int | None,
    bottom: int | None,
    weight_method: str = "destinations",
    start_hour: int = DESTINATIONS_START_HOUR,
    end_hour: int = DESTINATIONS_END_HOUR,
) -> None:
    """Print destination weights, optionally filtered to the top and/or bottom N."""
    weights = get_destination_weights(
        year_month=ANALYSIS_MONTH,
        weight_method=weight_method,
        start_hour=start_hour,
        end_hour=end_hour,
    )
    year, month = ANALYSIS_MONTH[:4], ANALYSIS_MONTH[4:]
    all_rows = [(rank, stn, w) for rank, (stn, w) in enumerate(weights.items(), 1)]
    total = len(all_rows)

    method_label = _WEIGHT_METHOD_LABELS.get(weight_method, weight_method)
    if weight_method in ("destinations", "real_world_commuter_destinations"):
        method_label += f"  [{start_hour:02d}:00–{end_hour:02d}:00]"
    header = f"\n=== Destination weights  ({method_label}, {year}-{month}) ==="

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
            "  python analyse.py --display weights\n"
            "  python analyse.py --weight-method morning_peak\n"
            "  python analyse.py --from 8 --to 22\n"
            "\n"
            "  All combinations of --display and --scope are valid.\n"
            "  --display weights opens a destination-weights map instead.\n"
            "  After analysis, the HTML map is opened automatically.\n"
            "\n"
            "Weights mode:\n"
            "  python analyse.py --weights\n"
            "  python analyse.py --weights --top 10\n"
            "  python analyse.py --weights --bottom 10\n"
            "  python analyse.py --weights --top 5 --bottom 5\n"
            "  python analyse.py --weights --weight-method all_hours_weighted\n"
            "  python analyse.py --weights --from 8 --to 20\n"
            "\n"
            "  Prints the destination weights used for the centroid.\n"
            "  --top N    shows the N highest-weighted stations (descending).\n"
            "  --bottom N shows the N lowest-weighted stations (ascending).\n"
            "  Omit both to print all stations.\n"
            "  --display and --scope are ignored in weights mode."
        ),
    )
    parser.add_argument(
        "--display",
        choices=["minutes", "linear", "log", "weights"],
        default="log",
        help=(
            "minutes  — colour by raw expected commute time (no 1-10 scale)\n"
            "linear   — 1-10 score using z-scores of raw minutes\n"
            "log      — 1-10 score using z-scores of log(minutes)  [default]\n"
            "weights  — destination weights map (separate output file)"
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
        "--weight-method",
        dest="weight_method",
        choices=_WEIGHT_METHOD_CHOICES,
        default="destinations",
        metavar="METHOD",
        help=(
            "How destination weights are derived (default: destinations).\n"
            "  destinations / real_world_commuter_destinations\n"
            "      Weekday tap-outs × 5 + weekend/PH tap-outs × 2 in the\n"
            "      time window set by --from / --to, each normalised to 1.0\n"
            "      before combining.  [default]\n"
            "  work_and_leisure\n"
            "      Weekday 7–10am tap-outs × 5 + weekend/PH 7am–7pm × 2.\n"
            "      Fixed windows; --from / --to are ignored.\n"
            "  morning_peak\n"
            "      Weekday morning (7–10am) tap-outs only.\n"
            "  all_hours_weighted\n"
            "      All-hours, day-count-weighted tap-outs."
        ),
    )
    parser.add_argument(
        "--from",
        dest="hour_from",
        type=int,
        default=DESTINATIONS_START_HOUR,
        metavar="HOUR",
        help=(
            f"Start of the tap-out window for 'destinations' weighting, "
            f"in 24-hour clock (default: {DESTINATIONS_START_HOUR}). "
            f"Ignored for other weight methods."
        ),
    )
    parser.add_argument(
        "--to",
        dest="hour_to",
        type=int,
        default=DESTINATIONS_END_HOUR,
        metavar="HOUR",
        help=(
            f"Exclusive end of the tap-out window for 'destinations' weighting, "
            f"in 24-hour clock (default: {DESTINATIONS_END_HOUR}, i.e. up to 18:59). "
            f"Ignored for other weight methods."
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
        cmd_weights(
            top=args.top,
            bottom=args.bottom,
            weight_method=args.weight_method,
            start_hour=args.hour_from,
            end_hour=args.hour_to,
        )
        return

    window_suffix = (
        f"  window={args.hour_from:02d}:00–{args.hour_to:02d}:00"
        if args.weight_method in ("destinations", "real_world_commuter_destinations")
        else ""
    )
    print(
        f"==> display={args.display}  scope={args.scope}"
        f"  weight_method={args.weight_method}{window_suffix}\n"
    )

    if args.display == "weights":
        build_weights_map(
            station_scope=args.scope,
            weight_method=args.weight_method,
            start_hour=args.hour_from,
            end_hour=args.hour_to,
        )
        print(f"\nOpening map: {OUTPUT_WEIGHTS_HTML}")
        webbrowser.open(OUTPUT_WEIGHTS_HTML)
    else:
        build_map(
            scoring_method=args.display,
            station_scope=args.scope,
            weight_method=args.weight_method,
            start_hour=args.hour_from,
            end_hour=args.hour_to,
        )
        print(f"\nOpening map: {OUTPUT_HTML}")
        webbrowser.open(OUTPUT_HTML)


if __name__ == "__main__":
    main()
