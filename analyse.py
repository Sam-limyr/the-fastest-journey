#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
analyse.py — Singapore MRT commute analysis

Computes commute scores for MRT stations and opens an interactive HTML map.
Can also print the destination weights that form the scoring centroid.

Usage:
    python analyse.py [--score <mode>] [--scope <scope>] [--weight-method <method>]
                      [--from <hour>] [--to <hour>]
                      [--weekday [--from <hour>] [--to <hour>]]
                      [--weekend [--from <hour>] [--to <hour>]]
                      [--weekend-weight-ratio <ratio>]
                      [--use-custom-weights]
    python analyse.py --weights [--top N] [--bottom N]
                      [--weight-method <method>] [--from <hour>] [--to <hour>]
                      [--weekday [--from <hour>] [--to <hour>]]
                      [--weekend [--from <hour>] [--to <hour>]]
                      [--weekend-weight-ratio <ratio>]
                      [--use-custom-weights]

Examples:
    python analyse.py
    python analyse.py --score log --scope residential
    python analyse.py --score minutes --scope all
    python analyse.py --score linear --scope hdb
    python analyse.py --score weights
    python analyse.py --weight-method morning_peak
    python analyse.py --use-custom-weights
    python analyse.py --from 8 --to 22
    python analyse.py --weekday --from 7 --to 19
    python analyse.py --weekday --from 7 --to 19 --weekend --from 7 --to 12
    python analyse.py --weekday --from 9 --to 18 --weekend --from 7 --to 19 --weekend-weight-ratio 2
    python analyse.py --weights
    python analyse.py --weights --top 10
    python analyse.py --weights --bottom 10
    python analyse.py --weights --weekday --from 15 --to 21
    python analyse.py --weights --weekday --from 12 --to 20 --weekend --from 7 --to 10
    python analyse.py --weights --weekday --from 12 --to 20 --weekend --from 7 --to 10 --weekend-weight-ratio 2
    python analyse.py --weights --weight-method all_hours_weighted
    python analyse.py --weights --use-custom-weights
    python analyse.py --tap in
    python analyse.py --tap both --score weights
"""

from __future__ import annotations

import argparse
import os
import platform
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


def _preprocess_argv(argv: list[str]) -> list[str]:
    """
    Rewrite --from/--to flags that immediately follow --weekday or --weekend
    into their scoped equivalents so argparse can treat them as distinct:

        --weekday --from=12 --to=20  →  --weekday --weekday-from=12 --weekday-to=20
        --weekend --from=7  --to=10  →  --weekend --weekend-from=7  --weekend-to=10

    If neither --weekday nor --weekend is present, --from/--to pass through
    unchanged (they still control the "destinations" window as before).
    """
    result: list[str] = []
    scope: str | None = None
    for arg in argv:
        if arg == "--weekday":
            scope = "weekday"
            result.append(arg)
        elif arg == "--weekend":
            scope = "weekend"
            result.append(arg)
        elif arg in ("--from", "--to") or arg.startswith("--from=") or arg.startswith("--to="):
            if scope is not None:
                if arg == "--from":
                    result.append(f"--{scope}-from")
                elif arg.startswith("--from="):
                    result.append(f"--{scope}-from=" + arg[7:])
                elif arg == "--to":
                    result.append(f"--{scope}-to")
                elif arg.startswith("--to="):
                    result.append(f"--{scope}-to=" + arg[5:])
            else:
                result.append(arg)
        else:
            # Any other flag resets scope
            if arg.startswith("--"):
                scope = None
            result.append(arg)
    return result


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
    custom_weekday_window: tuple[int, int] | None = None,
    custom_weekend_window: tuple[int, int] | None = None,
    custom_weekend_weight_ratio: float = 0.4,
    tap: str = "out",
    custom_weights: dict[str, float] | None = None,
) -> None:
    """Print destination weights, optionally filtered to the top and/or bottom N."""
    # If custom windows are given, override weight_method regardless of --weight-method
    if custom_weekday_window is not None or custom_weekend_window is not None:
        weight_method = "custom"

    weights = get_destination_weights(
        year_month=ANALYSIS_MONTH,
        weight_method=weight_method,
        start_hour=start_hour,
        end_hour=end_hour,
        custom_weekday_window=custom_weekday_window,
        custom_weekend_window=custom_weekend_window,
        custom_weekend_weight_ratio=custom_weekend_weight_ratio,
        tap=tap,
        custom_weights=custom_weights,
    )
    year, month = ANALYSIS_MONTH[:4], ANALYSIS_MONTH[4:]
    all_rows = [(rank, stn, w) for rank, (stn, w) in enumerate(weights.items(), 1)]
    total = len(all_rows)

    if custom_weights is not None:
        method_label = "custom manual weights"
    elif weight_method == "custom":
        parts = []
        if custom_weekday_window is not None:
            parts.append(
                f"wd {custom_weekday_window[0]:02d}:00–{custom_weekday_window[1]:02d}:00 ×1.0"
            )
        if custom_weekend_window is not None:
            parts.append(
                f"wknd {custom_weekend_window[0]:02d}:00–{custom_weekend_window[1]:02d}:00"
                f" ×{custom_weekend_weight_ratio}"
            )
        method_label = "custom  [" + " + ".join(parts) + "]"
    else:
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
    processed_argv = _preprocess_argv(sys.argv[1:])

    parser = argparse.ArgumentParser(
        description="Singapore MRT commute analyser.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Map mode (default):\n"
            "  python analyse.py\n"
            "  python analyse.py --score log --scope residential\n"
            "  python analyse.py --score minutes --scope all\n"
            "  python analyse.py --score linear --scope hdb\n"
            "  python analyse.py --score weights\n"
            "  python analyse.py --weight-method morning_peak\n"
            "  python analyse.py --from 8 --to 22\n"
            "  python analyse.py --weekday --from 7 --to 19\n"
            "  python analyse.py --weekday --from 7 --to 19 --weekend --from 7 --to 12\n"
            "  python analyse.py --weekday --from 9 --to 18 --weekend --from 7 --to 19"
            " --weekend-weight-ratio 2\n"
            "\n"
            "  All combinations of --score and --scope are valid.\n"
            "  --score weights opens a destination-weights map instead.\n"
            "  --weekday/--weekend define custom day-type weight windows (map or weights mode).\n"
            "  After analysis, the HTML map is opened automatically.\n"
            "\n"
            "Weights mode:\n"
            "  python analyse.py --weights\n"
            "  python analyse.py --weights --top 10\n"
            "  python analyse.py --weights --bottom 10\n"
            "  python analyse.py --weights --top 5 --bottom 5\n"
            "  python analyse.py --weights --weight-method all_hours_weighted\n"
            "  python analyse.py --weights --from 8 --to 20\n"
            "  python analyse.py --weights --weekday --from 15 --to 21\n"
            "  python analyse.py --weights --weekday --from 12 --to 20 --weekend --from 7 --to 10\n"
            "  python analyse.py --weights --weekday --from 9 --to 18 --weekend --from 7 --to 19"
            " --weekend-weight-ratio 2\n"
            "\n"
            "  Prints the destination weights used for the centroid.\n"
            "  --top N    shows the N highest-weighted stations (descending).\n"
            "  --bottom N shows the N lowest-weighted stations (ascending).\n"
            "  Omit both to print all stations.\n"
            "  --score and --scope are ignored in weights mode."
        ),
    )
    parser.add_argument(
        "--score",
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
        default="all",
        help=(
            "hdb         — HDB estate stations only\n"
            "residential — all residential MRT stations\n"
            "all         — every station in the travel-time dataset  [default]"
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
            f"Ignored for other weight methods.  When preceded by --weekday "
            f"or --weekend, scoped to that day type only."
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
            f"Ignored for other weight methods.  When preceded by --weekday "
            f"or --weekend, scoped to that day type only."
        ),
    )
    # Day-type component flags — work in both map mode and weights mode
    parser.add_argument(
        "--weekday",
        action="store_true",
        help=(
            "Enable a custom weekday tap-out window for weight calculation.  "
            "The --from/--to immediately following set the weekday window "
            f"(defaults: {DESTINATIONS_START_HOUR}–{DESTINATIONS_END_HOUR}).  "
            "Overrides --weight-method with 'custom'.  Works in both map and weights mode."
        ),
    )
    parser.add_argument(
        "--weekend",
        action="store_true",
        help=(
            "Enable a custom weekend/PH tap-out window for weight calculation.  "
            "The --from/--to immediately following set the weekend window "
            f"(defaults: {DESTINATIONS_START_HOUR}–{DESTINATIONS_END_HOUR}).  "
            "Overrides --weight-method with 'custom'.  Works in both map and weights mode."
        ),
    )
    # Scoped window args produced by _preprocess_argv — hidden from help
    parser.add_argument("--weekday-from", dest="weekday_from", type=int,
                        default=DESTINATIONS_START_HOUR)
    parser.add_argument("--weekday-to",   dest="weekday_to",   type=int,
                        default=DESTINATIONS_END_HOUR)
    parser.add_argument("--weekend-from", dest="weekend_from", type=int,
                        default=DESTINATIONS_START_HOUR)
    parser.add_argument("--weekend-to",   dest="weekend_to",   type=int,
                        default=DESTINATIONS_END_HOUR)
    parser.add_argument(
        "--weekend-weight-ratio",
        dest="weekend_weight_ratio",
        type=float,
        default=0.4,
        metavar="RATIO",
        help=(
            "With --weekday + --weekend: weight of the weekend/PH component "
            "relative to the weekday component.  Default 0.4 (= 2/5 ratio, "
            "matching the 'destinations' method).  Use >1 to make weekend "
            "data dominate."
        ),
    )
    parser.add_argument(
        "--weights",
        action="store_true",
        help=(
            "Print the destination weights used for the scoring centroid, then exit. "
            "Combine with --top / --bottom to filter the output.  "
            "Use --weekday / --weekend to define custom day-type windows."
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
    parser.add_argument(
        "--tap",
        choices=["out", "in", "both"],
        default="out",
        help=(
            "Which passenger flow direction to use as destination weights.\n"
            "out   — tap-outs (arrivals at destination)  [default]\n"
            "in    — tap-ins (departures from destination)\n"
            "both  — sum of tap-ins and tap-outs"
        ),
    )
    parser.add_argument(
        "--use-custom-weights",
        action="store_true",
        help=(
            "Use custom manually defined destination weights from get_weights() "
            "instead of data-driven weights. Overrides --weight-method."
        ),
    )

    args = parser.parse_args(processed_argv)

    custom_weekday_window = (args.weekday_from, args.weekday_to) if args.weekday else None
    custom_weekend_window = (args.weekend_from, args.weekend_to) if args.weekend else None

    # When custom windows are provided, override weight_method to "custom"
    has_custom = custom_weekday_window is not None or custom_weekend_window is not None
    weight_method = "custom" if has_custom else args.weight_method

    # Handle custom weights override
    custom_weights = None
    if args.use_custom_weights:
        from mrt_distance.mrt_distance import get_weights
        custom_weights = get_weights()
        weight_method = "custom"  # Override to custom when using custom weights

    in_weights_mode = (
        args.weights
        or args.top is not None
        or args.bottom is not None
    )

    _custom_kwargs = dict(
        custom_weekday_window=custom_weekday_window,
        custom_weekend_window=custom_weekend_window,
        custom_weekend_weight_ratio=args.weekend_weight_ratio,
        tap=args.tap,
        custom_weights=custom_weights,
    )

    if in_weights_mode:
        cmd_weights(
            top=args.top,
            bottom=args.bottom,
            weight_method=weight_method,
            start_hour=args.hour_from,
            end_hour=args.hour_to,
            **_custom_kwargs,
        )
        return

    window_suffix = (
        f"  window={args.hour_from:02d}:00–{args.hour_to:02d}:00"
        if weight_method in ("destinations", "real_world_commuter_destinations")
        else ""
    )
    print(
        f"==> score={args.score}  scope={args.scope}"
        f"  weight_method={weight_method}{window_suffix}\n"
    )

    if args.score == "weights":
        build_weights_map(
            station_scope=args.scope,
            weight_method=weight_method,
            start_hour=args.hour_from,
            end_hour=args.hour_to,
            **_custom_kwargs,
        )
        print(f"\nOpening map: {OUTPUT_WEIGHTS_HTML}")
        html_path = OUTPUT_WEIGHTS_HTML
        if platform.system() != 'Windows':
            html_path = "file://" + html_path
        webbrowser.open(html_path)
    else:
        build_map(
            scoring_method=args.score,
            station_scope=args.scope,
            weight_method=weight_method,
            start_hour=args.hour_from,
            end_hour=args.hour_to,
            **_custom_kwargs,
        )
        print(f"\nOpening map: {OUTPUT_HTML}")
        html_path = OUTPUT_HTML
        if platform.system() != 'Windows':
            html_path = "file://" + html_path
        webbrowser.open(html_path)


if __name__ == "__main__":
    main()
