"""
data_driven_rankings.py

Ranks HDB residential MRT stations by expected commute time, using
actual passenger volume data to derive destination weights instead of
handcrafted weights.

Key assumptions:
  - Weekday morning tap-INs  → proxy for where people LIVE
                               (residents leave home and enter the MRT network)
  - Weekday morning tap-OUTs → proxy for where people WORK / spend their day
                               (workers arrive and exit the MRT network)

Method:
  1. Sum weekday morning tap-out volumes per station to find popular work
     destinations.
  2. Normalize these volumes into probability weights (summing to 1.0).
  3. For each candidate HDB residential station, compute the expected
     commute time as the weighted average of travel times to all work
     destinations (weighted by how many people work there).
  4. Rank residential stations by ascending expected commute time.

Run from the repo root:
    python data_driven_rankings.py
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

from mrt_distance.mrt_distance import (
    read_travel_time_data,
    get_residential_mrt_stations,
    get_hdb_mrt_stations,
    COLUMN_FROM_STATION_NAME,
    COLUMN_TO_STATION_NAME,
    COLUMN_TRIP_DURATION_IN_MINUTES,
    COLUMN_WEIGHT,
    COLUMN_WEIGHTED_TRIP_DURATION,
)
from mrt_volume.explore import (
    get_station_code_to_name_mapping,
    DATA_PATH__PASSENGER_VOLUME_BY_TRAIN_STATIONS,
    STATION_CODE,
    TIME_OF_DAY,
    TOTAL_TAP_IN_VOLUME,
    TOTAL_TAP_OUT_VOLUME,
    DAY_TYPE,
    WEEKDAY,
    NOT_WEEKDAY,
    STATION_NAME,
    MRT_LINE,
)

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Weekday morning window used to infer home origins (tap-in) and work
# destinations (tap-out).  Hours are inclusive of start, exclusive of end,
# matching the TIME_PER_HOUR column semantics (e.g. 7 = 07:00–07:59).
WEEKDAY_MORNING_START_HOUR = 7   # 7am
WEEKDAY_MORNING_END_HOUR   = 10  # up to but not including 10am (i.e. 7, 8, 9)

# Default daytime window used by the "destinations" weight method.
# Applied to BOTH weekday and weekend/PH tap-outs before combining 5:2.
# These are the defaults; the CLI --from / --to flags can override them.
DESTINATIONS_START_HOUR = 7    # 7am (inclusive)
DESTINATIONS_END_HOUR   = 19   # 7pm (exclusive, i.e. hours 7–18)

# Ratio of weekday weight to weekend/PH weight in the "destinations" method.
DESTINATIONS_WEEKDAY_WEIGHT = 5
DESTINATIONS_WEEKEND_WEIGHT = 2

# Month used for analysis (must have an entry in _MONTH_NORMALIZATION_FACTORS)
ANALYSIS_MONTH = "202601"

# Column names in the processed (normalized) daily-average CSV
COLUMN_DAILY_AVG_TAP_IN  = "daily_avg_tap_in"
COLUMN_DAILY_AVG_TAP_OUT = "daily_avg_tap_out"

# Aggregated morning-window column names (output of get_weekday_morning_aggregates)
COLUMN_MORNING_TAP_IN  = "daily_avg_morning_tap_in"
COLUMN_MORNING_TAP_OUT = "daily_avg_morning_tap_out"

# Generic tap-out column used by the "destinations" method helper
COLUMN_DESTINATIONS_TAP_OUT = "destinations_tap_out"
COLUMN_COMMUTE_PENALTY_SCORE  = "commute_penalty_score"
COLUMN_EXPECTED_COMMUTE_MINUTES = "expected_commute_minutes"

# How many top stations to print in the diagnostic tables
DIAGNOSTIC_TOP_N = 20


# ---------------------------------------------------------------------------
# Data loading and preparation
# ---------------------------------------------------------------------------

CANONICAL_CODE = "CANONICAL_CODE"


def load_volume_data(file_path: str) -> pd.DataFrame:
    """
    Load the passenger volume CSV and enrich each row with its station name.

    Interchange stations are stored with combined codes (e.g. "EW16/NE3/TE17").
    Crucially, these stations appear ONLY as the combined code — they have no
    separate rows for each constituent line.  Therefore we must NOT explode the
    combined codes into multiple rows (which would multiply the volumes by the
    number of lines).  Instead we extract one component as the canonical lookup
    key, which uniquely identifies the physical station.

    Canonical code selection: prefer the first non-LRT component.  This avoids
    mistakenly discarding MRT/LRT interchanges such as Bukit Panjang ("BP6/DT1"),
    where the first component (BP6) is LRT but the second (DT1, Downtown Line)
    is not.  Pure LRT stations (all components LRT) still fall through to the
    LRT filter and are excluded as before.
    """
    df = pd.read_csv(file_path)

    # Build a code→line lookup so we can identify LRT components before merging.
    mapping_df = get_station_code_to_name_mapping()
    code_to_line: dict[str, str] = dict(
        zip(mapping_df[STATION_CODE], mapping_df[MRT_LINE])
    )

    def _pick_canonical(combined_code: str) -> str:
        """First non-LRT component, or the first component as fallback."""
        for part in combined_code.split("/"):
            if "LRT" not in code_to_line.get(part, "LRT"):
                return part
        return combined_code.split("/")[0]

    df[CANONICAL_CODE] = df[STATION_CODE].apply(_pick_canonical)

    # Enrich with human-readable station names and line info
    mapping_df = mapping_df.rename(columns={STATION_CODE: CANONICAL_CODE})
    df = df.merge(mapping_df, on=CANONICAL_CODE)

    # Exclude pure-LRT stations — their structural traffic patterns differ from MRT
    df = df[~df[MRT_LINE].str.contains("LRT")]

    df[STATION_NAME] = df[STATION_NAME].str.strip()
    return df


def build_comprehensive_code_to_name_mapping() -> dict:
    """
    Return a dict mapping EVERY individual station code to its station name,
    including all component codes of interchange combined codes.

    The LTA station-name mapping file has individual codes (e.g. EW16, NE3,
    TE17 each as separate rows).  The volume CSV uses combined codes
    (e.g. "EW16/NE3/TE17") for interchange stations.  This function merges
    both sources so that any individual code — whether encountered standalone
    or as part of a combined code — resolves to the correct station name.

    This is the authoritative lookup for future visualisation code: instead
    of only being able to resolve the first component of an interchange code,
    any individual code can be passed and will return the right station.

    Example:  mapping["NE3"]  == "Outram Park"
              mapping["TE17"] == "Outram Park"
              mapping["EW16"] == "Outram Park"
    """
    mapping_df = get_station_code_to_name_mapping()
    # Seed with all individual codes already present in the mapping file
    individual_to_name = {
        row[STATION_CODE]: row[STATION_NAME].strip()
        for _, row in mapping_df.iterrows()
    }

    # Walk through every combined code in the volume data, resolve it via any
    # component already known, then register ALL components under that name.
    raw_df = pd.read_csv(DATA_PATH__PASSENGER_VOLUME_BY_TRAIN_STATIONS)
    for combined_code in raw_df[STATION_CODE].unique():
        components = combined_code.split("/")
        station_name = next(
            (individual_to_name[c] for c in components if c in individual_to_name),
            None,
        )
        if station_name:
            for component in components:
                individual_to_name[component] = station_name

    return individual_to_name


def get_weekday_morning_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Filter to weekday morning rows and sum daily-average tap-in / tap-out
    across the analysis window (WEEKDAY_MORNING_START_HOUR–WEEKDAY_MORNING_END_HOUR).

    Expects a processed daily-average DataFrame (output of write_processed_daily_averages).
    The resulting values represent the average total passengers entering/exiting
    each station across the full morning window on a typical weekday.

    Returns a DataFrame sorted by tap-out volume descending, with columns:
        STATION_NAME, COLUMN_MORNING_TAP_IN, COLUMN_MORNING_TAP_OUT
    """
    morning_hours = range(WEEKDAY_MORNING_START_HOUR, WEEKDAY_MORNING_END_HOUR)
    mask = (df[DAY_TYPE] == WEEKDAY) & (df[TIME_OF_DAY].isin(morning_hours))
    df = df[mask]

    aggregated = (
        df.groupby(STATION_NAME)
        .agg(
            **{
                COLUMN_MORNING_TAP_IN:  (COLUMN_DAILY_AVG_TAP_IN,  "sum"),
                COLUMN_MORNING_TAP_OUT: (COLUMN_DAILY_AVG_TAP_OUT, "sum"),
            }
        )
        .reset_index()
        .sort_values(COLUMN_MORNING_TAP_OUT, ascending=False)
    )
    return aggregated


def _get_tap_out_aggregates(
    df: pd.DataFrame,
    day_type: str,
    start_hour: int,
    end_hour: int,
    tap: str = "out",
) -> pd.DataFrame:
    """
    Filter the processed daily-average DataFrame to a specific day type and
    hour window, then sum tap volumes per station.

    Parameters
    ----------
    df : pd.DataFrame
        Processed daily-average DataFrame (output of write_processed_daily_averages).
    day_type : str
        WEEKDAY or NOT_WEEKDAY.
    start_hour : int
        First hour to include (inclusive), e.g. 7 for 7am.
    end_hour : int
        Last hour boundary (exclusive), e.g. 19 means hours 7–18 are included.
    tap : str
        "out" — tap-out volumes only (default, current behaviour).
        "in"  — tap-in volumes only.
        "both" — sum of tap-in and tap-out volumes.

    Returns a DataFrame with columns STATION_NAME and COLUMN_DESTINATIONS_TAP_OUT,
    sorted by volume descending.
    """
    hours = range(start_hour, end_hour)
    mask = (df[DAY_TYPE] == day_type) & (df[TIME_OF_DAY].isin(hours))
    subset = df[mask].copy()
    if tap == "in":
        subset["_vol"] = subset[COLUMN_DAILY_AVG_TAP_IN]
    elif tap == "both":
        subset["_vol"] = subset[COLUMN_DAILY_AVG_TAP_IN] + subset[COLUMN_DAILY_AVG_TAP_OUT]
    else:  # "out"
        subset["_vol"] = subset[COLUMN_DAILY_AVG_TAP_OUT]
    return (
        subset
        .groupby(STATION_NAME)
        .agg(**{COLUMN_DESTINATIONS_TAP_OUT: ("_vol", "sum")})
        .reset_index()
        .sort_values(COLUMN_DESTINATIONS_TAP_OUT, ascending=False)
    )


# ---------------------------------------------------------------------------
# Weight derivation
# ---------------------------------------------------------------------------

def derive_work_destination_weights(
    morning_df: pd.DataFrame,
    travel_station_names: set,
) -> dict:
    """
    Convert weekday morning tap-out volumes into normalized destination
    weights for the scoring calculation.

    Stations from the volume dataset that have no corresponding travel-time
    data are dropped.  The remaining weights are renormalized to sum to 1.0.

    Returns {station_name: weight_as_decimal}.
    """
    in_travel_data = morning_df[STATION_NAME].isin(travel_station_names)
    filtered_df    = morning_df[in_travel_data]
    dropped_df     = morning_df[~in_travel_data]

    if not dropped_df.empty:
        total_volume   = morning_df[COLUMN_MORNING_TAP_OUT].sum()
        dropped_volume = dropped_df[COLUMN_MORNING_TAP_OUT].sum()
        print(
            f"[!] {len(dropped_df)} volume stations have no travel-time data "
            f"and are excluded ({dropped_volume / total_volume * 100:.1f}% of "
            f"morning tap-out volume)."
        )
        print(f"    Excluded: {sorted(dropped_df[STATION_NAME].tolist())}\n")

    remaining_total = filtered_df[COLUMN_MORNING_TAP_OUT].sum()
    weights = {
        row[STATION_NAME]: row[COLUMN_MORNING_TAP_OUT] / remaining_total
        for _, row in filtered_df.iterrows()
    }
    return weights


# ---------------------------------------------------------------------------
# Comprehensive (all-hours, all-day-type) weight derivation
# ---------------------------------------------------------------------------

COLUMN_WEIGHTED_ALL_HOURS_TAP_OUT = "weighted_all_hours_tap_out"


def get_all_hours_weighted_aggregates(
    df: pd.DataFrame,
    weekday_count: float,
    non_weekday_count: float,
) -> pd.DataFrame:
    """
    Combine weekday and non-weekday tap-out volumes across ALL hours into a
    single per-station score, weighted proportionally by day counts.

    For each station:
        score = (sum_hours(daily_avg_tap_out_weekday)    × weekday_count)
              + (sum_hours(daily_avg_tap_out_nonweekday) × non_weekday_count)

    This produces a quantity proportional to the monthly total tap-outs,
    so stations that serve large weekend / holiday crowds contribute to
    the centroid alongside weekday workers.

    Returns a DataFrame sorted by the aggregate column descending, with columns:
        STATION_NAME, COLUMN_WEIGHTED_ALL_HOURS_TAP_OUT
    """
    day_multipliers = {WEEKDAY: weekday_count, NOT_WEEKDAY: non_weekday_count}
    df = df.copy()
    df["_w"] = df.apply(
        lambda row: row[COLUMN_DAILY_AVG_TAP_OUT] * day_multipliers[row[DAY_TYPE]], axis=1
    )
    return (
        df.groupby(STATION_NAME)["_w"]
        .sum()
        .reset_index()
        .rename(columns={"_w": COLUMN_WEIGHTED_ALL_HOURS_TAP_OUT})
        .sort_values(COLUMN_WEIGHTED_ALL_HOURS_TAP_OUT, ascending=False)
    )


def derive_comprehensive_destination_weights(
    all_hours_df: pd.DataFrame,
    travel_station_names: set,
) -> dict:
    """
    Convert all-hours weighted tap-out scores into normalized destination
    weights for the scoring calculation.

    Mirrors derive_work_destination_weights but operates on the full-day,
    day-count-weighted aggregate produced by get_all_hours_weighted_aggregates.

    Stations absent from the travel-time dataset are dropped; the remaining
    weights are renormalized to sum to 1.0.

    Returns {station_name: weight_as_decimal}.
    """
    volume_col = COLUMN_WEIGHTED_ALL_HOURS_TAP_OUT
    in_travel = all_hours_df[STATION_NAME].isin(travel_station_names)
    filtered  = all_hours_df[in_travel]
    dropped   = all_hours_df[~in_travel]

    if not dropped.empty:
        total       = all_hours_df[volume_col].sum()
        dropped_vol = dropped[volume_col].sum()
        print(
            f"[!] {len(dropped)} volume stations have no travel-time data "
            f"and are excluded ({dropped_vol / total * 100:.1f}% of "
            f"all-hours tap-out volume)."
        )
        print(f"    Excluded: {sorted(dropped[STATION_NAME].tolist())}\n")

    remaining_total = filtered[volume_col].sum()
    return {
        row[STATION_NAME]: row[volume_col] / remaining_total
        for _, row in filtered.iterrows()
    }


def derive_destinations_weights(
    weekday_df: pd.DataFrame,
    weekend_df: pd.DataFrame,
    travel_station_names: set,
) -> dict:
    """
    Combine normalised weekday and weekend/PH tap-out weights in the ratio
    DESTINATIONS_WEEKDAY_WEIGHT : DESTINATIONS_WEEKEND_WEIGHT (default 5:2).

    Both DataFrames must have columns STATION_NAME and COLUMN_DESTINATIONS_TAP_OUT
    (produced by _get_tap_out_aggregates).  Each is normalised independently to
    sum to 1.0 before combining, so the absolute tap-out volumes do not need to
    be comparable.  The final combined weights are renormalised after dropping
    stations absent from the travel-time dataset.

    Returns {station_name: weight_as_decimal}.
    """
    col = COLUMN_DESTINATIONS_TAP_OUT

    weekday_total = weekday_df[col].sum()
    weekday_norm = {
        row[STATION_NAME]: row[col] / weekday_total
        for _, row in weekday_df.iterrows()
    }

    weekend_total = weekend_df[col].sum()
    weekend_norm = {
        row[STATION_NAME]: row[col] / weekend_total
        for _, row in weekend_df.iterrows()
    }

    all_stations = set(weekday_norm.keys()) | set(weekend_norm.keys())
    combined = {
        stn: (
            DESTINATIONS_WEEKDAY_WEIGHT * weekday_norm.get(stn, 0.0)
            + DESTINATIONS_WEEKEND_WEIGHT * weekend_norm.get(stn, 0.0)
        )
        for stn in all_stations
    }

    filtered = {stn: v for stn, v in combined.items() if stn in travel_station_names}
    dropped  = {stn: v for stn, v in combined.items() if stn not in travel_station_names}

    if dropped:
        total       = sum(combined.values())
        dropped_vol = sum(dropped.values())
        print(
            f"[!] {len(dropped)} volume stations have no travel-time data "
            f"and are excluded ({dropped_vol / total * 100:.1f}% of "
            f"destinations-weighted tap-out volume)."
        )
        print(f"    Excluded: {sorted(dropped.keys())}\n")

    remaining_total = sum(filtered.values())
    return {stn: v / remaining_total for stn, v in filtered.items()}


def derive_custom_destination_weights(
    weekday_df: pd.DataFrame | None,
    weekend_df: pd.DataFrame | None,
    travel_station_names: set,
    weekday_weight: float = 1.0,
    weekend_weight: float = 0.4,
) -> dict:
    """
    Generalised weight combiner for the "custom" method.

    Either component may be None (omitted entirely); if both are provided they
    are each normalised to 1.0 independently before being blended with the
    given weekday_weight : weekend_weight ratio.  Stations absent from the
    travel-time dataset are dropped and the result is renormalised.

    Parameters
    ----------
    weekday_df : DataFrame or None
        Output of _get_tap_out_aggregates for WEEKDAY, or None to skip.
    weekend_df : DataFrame or None
        Output of _get_tap_out_aggregates for NOT_WEEKDAY, or None to skip.
    travel_station_names : set
        Stations present in the travel-time matrix.
    weekday_weight : float
        Blending weight applied to the normalised weekday component (default 1.0).
    weekend_weight : float
        Blending weight applied to the normalised weekend component (default 0.4,
        giving the same 5:2 ratio as the "destinations" method).

    Returns {station_name: weight_as_decimal}.
    """
    col = COLUMN_DESTINATIONS_TAP_OUT

    weekday_norm: dict[str, float] = {}
    if weekday_df is not None:
        wkd_total = weekday_df[col].sum()
        weekday_norm = {
            row[STATION_NAME]: row[col] / wkd_total
            for _, row in weekday_df.iterrows()
        }

    weekend_norm: dict[str, float] = {}
    if weekend_df is not None:
        wke_total = weekend_df[col].sum()
        weekend_norm = {
            row[STATION_NAME]: row[col] / wke_total
            for _, row in weekend_df.iterrows()
        }

    all_stations = set(weekday_norm.keys()) | set(weekend_norm.keys())
    combined = {
        stn: (
            weekday_weight * weekday_norm.get(stn, 0.0)
            + weekend_weight * weekend_norm.get(stn, 0.0)
        )
        for stn in all_stations
    }

    filtered = {stn: v for stn, v in combined.items() if stn in travel_station_names}
    dropped  = {stn: v for stn, v in combined.items() if stn not in travel_station_names}

    if dropped:
        total       = sum(combined.values())
        dropped_vol = sum(dropped.values())
        print(
            f"[!] {len(dropped)} volume stations have no travel-time data "
            f"and are excluded ({dropped_vol / total * 100:.1f}% of "
            f"custom-weighted tap-out volume)."
        )
        print(f"    Excluded: {sorted(dropped.keys())}\n")

    remaining_total = sum(filtered.values())
    return {stn: v / remaining_total for stn, v in filtered.items()}


# ---------------------------------------------------------------------------
# Public accessor for destination weights
# ---------------------------------------------------------------------------

def get_destination_weights(
    year_month: str = ANALYSIS_MONTH,
    weight_method: str = "destinations",
    start_hour: int = DESTINATIONS_START_HOUR,
    end_hour: int = DESTINATIONS_END_HOUR,
    custom_weekday_window: tuple[int, int] | None = None,
    custom_weekend_window: tuple[int, int] | None = None,
    custom_weekend_weight_ratio: float = 0.4,
    tap: str = "out",
) -> dict[str, float]:
    """
    Return the normalised work-destination weights used as the centroid for
    commute scoring.

    Each key is a station name; each value is its share of total tap volume
    (decimal, summing to 1.0 across all included stations).  The dict is
    ordered by weight descending.

    Parameters
    ----------
    year_month : str
        Month to derive weights from, e.g. "202601".
    weight_method : str
        "destinations" (alias "real_world_commuter_destinations") — weekday
            tap-outs × 5 + weekend/PH tap-outs × 2, each in the window
            [start_hour, end_hour), normalised to 1.0 before combining (default).
        "work_and_leisure" — weekday morning rush (7–10 am) tap-outs × 5 +
            weekend/PH daytime (7 am–7 pm) tap-outs × 2, with different
            windows for each day type; start_hour / end_hour are ignored.
        "morning_peak"       — weekday morning (7–10 am) tap-outs only.
        "all_hours_weighted" — all-hours, day-count-weighted tap-outs.
        "custom"             — user-defined weekday/weekend windows and ratio;
            requires custom_weekday_window and/or custom_weekend_window.
    start_hour : int
        First hour of the tap volume window for the "destinations" method (default 7).
        Ignored for all other methods.
    end_hour : int
        Exclusive end hour for the window (default 19, i.e. hours 7–18).
        Ignored for all other methods.
    custom_weekday_window : (int, int) or None
        For "custom": (from_hour, to_hour) for the weekday tap window.
        None means the weekday component is omitted entirely.
    custom_weekend_window : (int, int) or None
        For "custom": (from_hour, to_hour) for the weekend/PH tap window.
        None means the weekend component is omitted entirely.
    custom_weekend_weight_ratio : float
        For "custom": weekend weight relative to weekday (default 0.4 = 2/5).
        Ignored when only one component is active.
    tap : str
        Which passenger-flow direction to use for weighting.
        "out"  — tap-out volumes (arrivals; default, original behaviour).
        "in"   — tap-in volumes (departures).
        "both" — sum of tap-in and tap-out volumes.
        Note: "all_hours_weighted" always uses tap-out volumes regardless of
        this parameter.
    """
    # Accept long-form alias
    if weight_method == "real_world_commuter_destinations":
        weight_method = "destinations"

    processed_path = os.path.join(
        PROCESSED_DATA_DIR, f"transport_node_train_{year_month}_daily_avg.csv"
    )
    volume_df = pd.read_csv(processed_path)
    travel_times = read_travel_time_data()
    travel_station_names = set(travel_times[COLUMN_FROM_STATION_NAME].unique())

    if weight_method == "destinations":
        weekday_df = _get_tap_out_aggregates(volume_df, WEEKDAY, start_hour, end_hour, tap)
        weekend_df = _get_tap_out_aggregates(volume_df, NOT_WEEKDAY, start_hour, end_hour, tap)
        weights = derive_destinations_weights(weekday_df, weekend_df, travel_station_names)
    elif weight_method == "work_and_leisure":
        weekday_df = _get_tap_out_aggregates(
            volume_df, WEEKDAY, WEEKDAY_MORNING_START_HOUR, WEEKDAY_MORNING_END_HOUR, tap
        )
        weekend_df = _get_tap_out_aggregates(
            volume_df, NOT_WEEKDAY, DESTINATIONS_START_HOUR, DESTINATIONS_END_HOUR, tap
        )
        weights = derive_destinations_weights(weekday_df, weekend_df, travel_station_names)
    elif weight_method == "morning_peak":
        morning_df = get_weekday_morning_aggregates(volume_df)
        if tap != "out":
            # morning_peak uses its own aggregator; replicate with tap support
            morning_df = _get_tap_out_aggregates(
                volume_df, WEEKDAY,
                WEEKDAY_MORNING_START_HOUR, WEEKDAY_MORNING_END_HOUR, tap,
            )
        weights = derive_work_destination_weights(morning_df, travel_station_names)
    elif weight_method == "all_hours_weighted":
        weekday_count, non_weekday_count = get_normalization_factors(year_month)
        all_hours_df = get_all_hours_weighted_aggregates(
            volume_df, weekday_count, non_weekday_count
        )
        weights = derive_comprehensive_destination_weights(
            all_hours_df, travel_station_names
        )
    elif weight_method == "custom":
        if custom_weekday_window is None and custom_weekend_window is None:
            raise ValueError(
                "weight_method='custom' requires at least one of "
                "custom_weekday_window or custom_weekend_window."
            )
        weekday_df = (
            _get_tap_out_aggregates(
                volume_df, WEEKDAY,
                custom_weekday_window[0], custom_weekday_window[1], tap,
            )
            if custom_weekday_window is not None else None
        )
        weekend_df = (
            _get_tap_out_aggregates(
                volume_df, NOT_WEEKDAY,
                custom_weekend_window[0], custom_weekend_window[1], tap,
            )
            if custom_weekend_window is not None else None
        )
        weights = derive_custom_destination_weights(
            weekday_df, weekend_df, travel_station_names,
            weekday_weight=1.0,
            weekend_weight=custom_weekend_weight_ratio,
        )
    else:
        raise ValueError(
            f"Unknown weight_method: {weight_method!r}. "
            f"Use 'destinations', 'work_and_leisure', 'morning_peak', "
            f"'all_hours_weighted', or 'custom'."
        )

    return dict(sorted(weights.items(), key=lambda x: x[1], reverse=True))


# ---------------------------------------------------------------------------
# Commute penalty function
# ---------------------------------------------------------------------------

# Satisfaction band thresholds (minutes).
COMMUTE_BAND_HAPPY_LIMIT = 30.0   # < 30 min: happy
COMMUTE_BAND_OKAY_LIMIT  = 45.0   # 30–45 min: okay;  > 45 min: not happy

# Power exponents applied within each band.  Values above 1.0 mean longer
# trips are penalised super-linearly; a higher exponent = steeper growth.
COMMUTE_EXPONENT_HAPPY   = 1.1   # near-linear — commuters are broadly happy
COMMUTE_EXPONENT_OKAY    = 1.5   # noticeably exponential — discomfort growing
COMMUTE_EXPONENT_UNHAPPY = 2.0   # steep — commuters are unhappy

# Continuity scalars, derived analytically so the penalty curve has no
# discontinuous jumps at the band boundaries.
#
#   Band 1:  f(t)      = t ^ HAPPY
#   Band 2:  f(t)      = _C2 * t ^ OKAY      ; C2 chosen so f(30⁻) == f(30⁺)
#   Band 3:  f(t)      = _C3 * t ^ UNHAPPY   ; C3 chosen so f(45⁻) == f(45⁺)
#
#   C2 = 30^(HAPPY  − OKAY)
#   C3 = C2 × 45^(OKAY − UNHAPPY)
_C2 = COMMUTE_BAND_HAPPY_LIMIT ** (COMMUTE_EXPONENT_HAPPY  - COMMUTE_EXPONENT_OKAY)
_C3 = _C2 * COMMUTE_BAND_OKAY_LIMIT ** (COMMUTE_EXPONENT_OKAY - COMMUTE_EXPONENT_UNHAPPY)


def commute_penalty(minutes: float) -> float:
    """
    Convert a raw travel time (minutes) into a dimensionless penalty score
    using a continuous piecewise-power function.

    The exponent steps up at each satisfaction-band boundary, so each
    additional minute of commute hurts progressively more:

        t < 30  min  →  t ^ 1.1          (happy: near-linear)
        30 ≤ t < 45  →  _C2 × t ^ 1.5   (okay: growing discomfort)
        t ≥ 45  min  →  _C3 × t ^ 2.0   (unhappy: steep penalty)

    _C2 and _C3 guarantee continuity — the curve has no jumps at t=30 or t=45.

    The result is not in minutes; it is a relative penalty score.
    Rankings still hold: higher score = worse commute.
    """
    if minutes < COMMUTE_BAND_HAPPY_LIMIT:
        return minutes ** COMMUTE_EXPONENT_HAPPY
    elif minutes < COMMUTE_BAND_OKAY_LIMIT:
        return _C2 * minutes ** COMMUTE_EXPONENT_OKAY
    else:
        return _C3 * minutes ** COMMUTE_EXPONENT_UNHAPPY


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def calculate_data_driven_ratings(
    year_month: str = ANALYSIS_MONTH,
    station_scope: str = "residential",
    verbose: bool = True,
    weight_method: str = "destinations",
    start_hour: int = DESTINATIONS_START_HOUR,
    end_hour: int = DESTINATIONS_END_HOUR,
    custom_weekday_window: tuple[int, int] | None = None,
    custom_weekend_window: tuple[int, int] | None = None,
    custom_weekend_weight_ratio: float = 0.4,
    tap: str = "out",
) -> pd.DataFrame:
    """
    Compute and (optionally) print station rankings by expected commute time.

    Parameters
    ----------
    year_month : str
        Month to analyse, e.g. "202506".  A processed daily-average CSV for
        this month must exist in PROCESSED_DATA_DIR.
    station_scope : str
        Which stations to include in the output rankings.
        "residential" (default) — all residential MRT stations.
        "hdb"                   — HDB estate MRT stations only (subset of residential).
        "all"                   — every station in the travel-time dataset.
    verbose : bool
        If True (default), print all diagnostic and rankings tables.
        Set to False to suppress output and just get the returned DataFrame.
    weight_method : str
        How to derive destination weights from passenger volume data.
        "destinations" (alias "real_world_commuter_destinations") — weekday
            and weekend/PH tap-outs in [start_hour, end_hour) combined 5:2,
            each normalised to 1.0 before combining (default).
        "work_and_leisure" — weekday morning (7–10 am) tap-outs × 5 +
                             weekend/PH daytime (7 am–7 pm) tap-outs × 2;
                             uses fixed windows, ignores start_hour/end_hour.
        "morning_peak"       — weekday morning (7–10 am) tap-out volumes only;
                               focuses purely on work commuters.
        "all_hours_weighted" — all-hours tap-out across both weekday and
                               non-weekday rows, weighted by day counts from
                               _MONTH_NORMALIZATION_FACTORS.
        "custom"             — user-defined weekday/weekend windows and ratio;
                               controlled by custom_weekday_window,
                               custom_weekend_window, custom_weekend_weight_ratio.
    start_hour : int
        For "destinations": first hour of the tap-out window (default 7).
    end_hour : int
        For "destinations": exclusive end hour (default 19, i.e. hours 7–18).
    custom_weekday_window : (int, int) or None
        For "custom": (from_hour, to_hour) for weekday tap-outs.
    custom_weekend_window : (int, int) or None
        For "custom": (from_hour, to_hour) for weekend/PH tap-outs.
    custom_weekend_weight_ratio : float
        For "custom": weekend weight relative to weekday (default 0.4 = 2/5).
    tap : str
        "out"  — tap-out volumes (arrivals; default).
        "in"   — tap-in volumes (departures).
        "both" — sum of tap-in and tap-out volumes.
        Note: "all_hours_weighted" always uses tap-out regardless of this parameter.

    Reads from the pre-processed daily-average CSV for the given month.
    Run write_processed_daily_averages(year_month) first if the file does not exist.
    """
    # Accept long-form alias
    if weight_method == "real_world_commuter_destinations":
        weight_method = "destinations"
    processed_path = os.path.join(
        PROCESSED_DATA_DIR, f"transport_node_train_{year_month}_daily_avg.csv"
    )
    # 1. Load the pre-processed daily-average data (station names already resolved)
    volume_df = pd.read_csv(processed_path)

    # 2+4. Derive destination weights using the selected method
    travel_times = read_travel_time_data()
    travel_station_names = set(travel_times[COLUMN_FROM_STATION_NAME].unique())

    if weight_method == "destinations":
        # Weekday + weekend/PH tap volumes in the same [start_hour, end_hour) window,
        # each normalised to 1.0 then combined DESTINATIONS_WEEKDAY_WEIGHT:DESTINATIONS_WEEKEND_WEIGHT
        weekday_df = _get_tap_out_aggregates(volume_df, WEEKDAY, start_hour, end_hour, tap)
        weekend_df = _get_tap_out_aggregates(volume_df, NOT_WEEKDAY, start_hour, end_hour, tap)
        if verbose:
            print("=" * 60)
            print(
                f"Top {DIAGNOSTIC_TOP_N} Stations — Real-World Destinations"
                f"  ({year_month})"
            )
            print(
                f"Weekday {start_hour:02d}:00–{end_hour:02d}:00"
                f" × {DESTINATIONS_WEEKDAY_WEIGHT}"
                f" + Weekend/PH {start_hour:02d}:00–{end_hour:02d}:00"
                f" × {DESTINATIONS_WEEKEND_WEIGHT}"
                f"  (each normalised independently)"
            )
            print("=" * 60)
        work_weights = derive_destinations_weights(
            weekday_df, weekend_df, travel_station_names
        )
        weight_label = (
            f"destinations tap-outs {start_hour:02d}:00–{end_hour:02d}:00 "
            f"(wd ×{DESTINATIONS_WEEKDAY_WEIGHT}"
            f" + wknd ×{DESTINATIONS_WEEKEND_WEIGHT}), {year_month}"
        )

    elif weight_method == "morning_peak":
        # Weekday morning tap volumes → destination weights
        if tap != "out":
            morning_df = _get_tap_out_aggregates(
                volume_df, WEEKDAY,
                WEEKDAY_MORNING_START_HOUR, WEEKDAY_MORNING_END_HOUR, tap,
            )
        else:
            morning_df = get_weekday_morning_aggregates(volume_df)
        morning_window = (
            f"{WEEKDAY_MORNING_START_HOUR}am"
            f"–{WEEKDAY_MORNING_END_HOUR}am"
            f" weekday daily avg, {year_month}"
        )
        if verbose:
            print("=" * 60)
            print(f"Top {DIAGNOSTIC_TOP_N} Work Destinations  ({morning_window})")
            print("Tap-OUT = people arriving at work / school")
            print("=" * 60)
            print(
                morning_df[[STATION_NAME, COLUMN_MORNING_TAP_OUT]]
                .head(DIAGNOSTIC_TOP_N)
                .to_string(index=False)
            )
            print()
            print("=" * 60)
            print(f"Top {DIAGNOSTIC_TOP_N} Residential Origins  ({morning_window})")
            print("Tap-IN = people leaving home")
            print("=" * 60)
            print(
                morning_df[[STATION_NAME, COLUMN_MORNING_TAP_IN]]
                .sort_values(COLUMN_MORNING_TAP_IN, ascending=False)
                .head(DIAGNOSTIC_TOP_N)
                .to_string(index=False)
            )
            print()
        work_weights = derive_work_destination_weights(morning_df, travel_station_names)
        weight_label = (
            f"weekday morning tap-outs "
            f"{WEEKDAY_MORNING_START_HOUR}am–{WEEKDAY_MORNING_END_HOUR}am, {year_month}"
        )

    elif weight_method == "all_hours_weighted":
        # All-hours tap-out across both day types, weighted by day counts
        weekday_count, non_weekday_count = get_normalization_factors(year_month)
        all_hours_df = get_all_hours_weighted_aggregates(
            volume_df, weekday_count, non_weekday_count
        )
        if verbose:
            print("=" * 60)
            print(
                f"Top {DIAGNOSTIC_TOP_N} Stations by Comprehensive Tap-Out Weight"
                f"  ({year_month})"
            )
            print(
                f"All hours  |  weekday × {weekday_count:.0f} days"
                f" + non-weekday × {non_weekday_count:.0f} days"
            )
            print("=" * 60)
            print(
                all_hours_df[[STATION_NAME, COLUMN_WEIGHTED_ALL_HOURS_TAP_OUT]]
                .head(DIAGNOSTIC_TOP_N)
                .to_string(index=False)
            )
            print()
        work_weights = derive_comprehensive_destination_weights(
            all_hours_df, travel_station_names
        )
        weight_label = (
            f"all-hours weighted tap-outs "
            f"(wd×{weekday_count:.0f} + nwd×{non_weekday_count:.0f}), {year_month}"
        )

    elif weight_method == "work_and_leisure":
        # Weekday morning tap volumes × 5  +  weekend/PH daytime tap volumes × 2
        weekday_df = _get_tap_out_aggregates(
            volume_df, WEEKDAY, WEEKDAY_MORNING_START_HOUR, WEEKDAY_MORNING_END_HOUR, tap
        )
        weekend_df = _get_tap_out_aggregates(
            volume_df, NOT_WEEKDAY, DESTINATIONS_START_HOUR, DESTINATIONS_END_HOUR, tap
        )
        if verbose:
            print("=" * 60)
            print(
                f"Top {DIAGNOSTIC_TOP_N} Stations — Work & Leisure"
                f"  ({year_month})"
            )
            print(
                f"Weekday {WEEKDAY_MORNING_START_HOUR:02d}:00–{WEEKDAY_MORNING_END_HOUR:02d}:00"
                f" × {DESTINATIONS_WEEKDAY_WEIGHT}"
                f" + Weekend/PH {DESTINATIONS_START_HOUR:02d}:00–{DESTINATIONS_END_HOUR:02d}:00"
                f" × {DESTINATIONS_WEEKEND_WEIGHT}"
                f"  (each normalised independently)"
            )
            print("=" * 60)
        work_weights = derive_destinations_weights(
            weekday_df, weekend_df, travel_station_names
        )
        weight_label = (
            f"work & leisure "
            f"(wd {WEEKDAY_MORNING_START_HOUR:02d}:00–{WEEKDAY_MORNING_END_HOUR:02d}:00"
            f" ×{DESTINATIONS_WEEKDAY_WEIGHT}"
            f" + wknd {DESTINATIONS_START_HOUR:02d}:00–{DESTINATIONS_END_HOUR:02d}:00"
            f" ×{DESTINATIONS_WEEKEND_WEIGHT}), {year_month}"
        )

    elif weight_method == "custom":
        if custom_weekday_window is None and custom_weekend_window is None:
            raise ValueError(
                "weight_method='custom' requires at least one of "
                "--weekday or --weekend."
            )
        weekday_df = (
            _get_tap_out_aggregates(
                volume_df, WEEKDAY,
                custom_weekday_window[0], custom_weekday_window[1], tap,
            )
            if custom_weekday_window is not None else None
        )
        weekend_df = (
            _get_tap_out_aggregates(
                volume_df, NOT_WEEKDAY,
                custom_weekend_window[0], custom_weekend_window[1], tap,
            )
            if custom_weekend_window is not None else None
        )
        if verbose:
            print("=" * 60)
            print(
                f"Top {DIAGNOSTIC_TOP_N} Stations — Custom Weights"
                f"  ({year_month})"
            )
            parts = []
            if custom_weekday_window is not None:
                parts.append(
                    f"Weekday {custom_weekday_window[0]:02d}:00–"
                    f"{custom_weekday_window[1]:02d}:00 × 1.0"
                )
            if custom_weekend_window is not None:
                parts.append(
                    f"Weekend/PH {custom_weekend_window[0]:02d}:00–"
                    f"{custom_weekend_window[1]:02d}:00"
                    f" × {custom_weekend_weight_ratio}"
                )
            print("  +  ".join(parts))
            if custom_weekday_window is not None and custom_weekend_window is not None:
                print("(each normalised independently)")
            print("=" * 60)
        work_weights = derive_custom_destination_weights(
            weekday_df, weekend_df, travel_station_names,
            weekday_weight=1.0,
            weekend_weight=custom_weekend_weight_ratio,
        )
        label_parts = []
        if custom_weekday_window is not None:
            label_parts.append(
                f"wd {custom_weekday_window[0]:02d}:00–"
                f"{custom_weekday_window[1]:02d}:00 ×1.0"
            )
        if custom_weekend_window is not None:
            label_parts.append(
                f"wknd {custom_weekend_window[0]:02d}:00–"
                f"{custom_weekend_window[1]:02d}:00"
                f" ×{custom_weekend_weight_ratio}"
            )
        weight_label = f"custom ({' + '.join(label_parts)}), {year_month}"

    else:
        raise ValueError(
            f"Unknown weight_method: {weight_method!r}. "
            f"Use 'destinations', 'work_and_leisure', 'morning_peak', "
            f"'all_hours_weighted', or 'custom'."
        )

    # 5. Filter travel-time matrix to the stations we have weights for.
    #    The scope filter is applied to the output DataFrame after aggregation
    #    so that weights/scores are always computed across the full station set.
    travel_times = travel_times[
        travel_times[COLUMN_FROM_STATION_NAME].isin(work_weights)
    ]

    # 6. Compute both raw and penalized weighted trip durations
    travel_times = travel_times.copy()
    travel_times[COLUMN_WEIGHT] = (
        travel_times[COLUMN_FROM_STATION_NAME].map(work_weights)
    )
    travel_times[COLUMN_WEIGHTED_TRIP_DURATION] = (
        travel_times[COLUMN_WEIGHT]
        * travel_times[COLUMN_TRIP_DURATION_IN_MINUTES].map(commute_penalty)
    )
    _RAW_WEIGHTED = "_raw_weighted"
    travel_times[_RAW_WEIGHTED] = (
        travel_times[COLUMN_WEIGHT] * travel_times[COLUMN_TRIP_DURATION_IN_MINUTES]
    )

    # 7. Aggregate per residential station and rank
    results = (
        travel_times[[COLUMN_TO_STATION_NAME, COLUMN_WEIGHTED_TRIP_DURATION, _RAW_WEIGHTED]]
        .groupby(COLUMN_TO_STATION_NAME)
        .sum()
        .rename(columns={
            COLUMN_WEIGHTED_TRIP_DURATION: COLUMN_COMMUTE_PENALTY_SCORE,
            _RAW_WEIGHTED: COLUMN_EXPECTED_COMMUTE_MINUTES,
        })
        .sort_values(COLUMN_COMMUTE_PENALTY_SCORE, ascending=True)
    )
    results.index.name = "residential_station"

    # Apply station_scope filter to the output — the computation always used
    # all stations so scores are absolute, not relative to the subset.
    if station_scope == "residential":
        scope_stations = set(get_residential_mrt_stations())
        results = results[results.index.isin(scope_stations)]
    elif station_scope == "hdb":
        scope_stations = set(get_hdb_mrt_stations())
        results = results[results.index.isin(scope_stations)]
    # else station_scope == "all" — no filter

    if verbose:
        print("=" * 60)
        print("Residential Station Rankings — Penalized Commute Score")
        print(f"(piecewise-exponential penalty: "
              f"<{COMMUTE_BAND_HAPPY_LIMIT:.0f}min exp={COMMUTE_EXPONENT_HAPPY}, "
              f"{COMMUTE_BAND_HAPPY_LIMIT:.0f}–{COMMUTE_BAND_OKAY_LIMIT:.0f}min exp={COMMUTE_EXPONENT_OKAY}, "
              f">{COMMUTE_BAND_OKAY_LIMIT:.0f}min exp={COMMUTE_EXPONENT_UNHAPPY})")
        print(f"(destination weights: {weight_label})")
        print("Lower score = better — non-linear, so long commutes are disproportionately penalised")
        print()
        print(f"  {'min':>4}  {'multiplier':>10}  band")
        print(f"  {'----':>4}  {'----------':>10}  ----")
        for _t in [10, 20, 30, 40, 45, 55, 60]:
            _mult = commute_penalty(_t) / _t
            if _t == COMMUTE_BAND_HAPPY_LIMIT:
                _band = "happy -> okay boundary"
            elif _t == COMMUTE_BAND_OKAY_LIMIT:
                _band = "okay -> unhappy boundary"
            elif _t < COMMUTE_BAND_HAPPY_LIMIT:
                _band = "happy"
            elif _t < COMMUTE_BAND_OKAY_LIMIT:
                _band = "okay"
            else:
                _band = "unhappy"
            print(f"  {_t:>4}  {_mult:>10.3f}x  {_band}")
        print()
        print("=" * 60)
        print(results.to_string())

    return results


# ---------------------------------------------------------------------------
# Commute scoring (1-10)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Scoring method "linear": z-scores of raw minutes
# ---------------------------------------------------------------------------

# How many standard deviations from the mean map to the edge of the 1-10
# scale (before clipping).  With ~100 stations:
#   SCORE_RANGE_SD = 2.5  →  P(|z| > 2.22) ≈ 2.6%  →  ~1-3 stations at each extreme
#   SCORE_RANGE_SD = 2.0  →  P(|z| > 2.0)  ≈ 4.6%  →  ~4-5 stations at each extreme
# Increase to push more stations toward the 5-6 midpoint; decrease to spread them out.
SCORE_RANGE_SD = 2.5


def assign_commute_scores(commute_minutes: pd.Series) -> pd.Series:
    """
    Map expected commute times (raw minutes) to integer 1-10 scores using a
    normal-distribution-based formula.  Shorter commutes → higher scores.

    Formula:
        z     = (minutes - mean) / std
        score = clip(round(5.5 - z / SCORE_RANGE_SD * 4.5), 1, 10)

    At z = 0 (exactly average commute): raw score = 5.5, rounds to 5 or 6.
    At z = -SCORE_RANGE_SD (SCORE_RANGE_SD σ below mean): raw score = 10.
    At z = +SCORE_RANGE_SD (SCORE_RANGE_SD σ above mean):  raw score = 1.
    Stations beyond ±SCORE_RANGE_SD are clipped to 1 or 10.

    Caveat: commute times are right-skewed, so the bad tail reaches further
    from the mean than the good tail.  This causes score 1 to appear more
    often than score 10.  Use assign_commute_scores_log for a symmetric result.
    """
    mean = commute_minutes.mean()
    std  = commute_minutes.std()
    z    = (commute_minutes - mean) / std
    raw  = 5.5 - z / SCORE_RANGE_SD * 4.5
    return raw.clip(1, 10).round().astype(int)


# ---------------------------------------------------------------------------
# Scoring method "log": z-scores of log(minutes) — symmetric, preferred
# ---------------------------------------------------------------------------

# Commute times are right-skewed: the bad tail extends further from the mean
# than the good tail (worst station z ≈ +2.55 vs best station z ≈ -1.89 with
# raw minutes).  Taking log(minutes) before z-scoring makes the distribution
# near-symmetric (ratio ≈ 1.01), so score 1 and score 10 appear equally often.
#
# SCORE_RANGE_SD_LOG is calibrated so that the tails of the current residential
# dataset just reach scores 1 and 10 (rather than stopping at 9 as before).
# Interpretation: log(minutes) is the right metric because commute goodness is
# multiplicative — a 10 % improvement feels equally valuable at any duration.
SCORE_RANGE_SD_LOG = 2.1


def assign_commute_scores_log(commute_minutes: pd.Series) -> pd.Series:
    """
    Map expected commute times to integer 1-10 scores using z-scores of
    log(minutes).  Shorter commutes → higher scores.

    Commute times are right-skewed (bounded floor, no ceiling), so raw
    z-scores assign more extreme values to bad stations than good ones,
    producing more 1s than 10s.  Log-transforming first makes the
    distribution near-symmetric and yields roughly equal extremes.

    Formula:
        z     = (log(minutes) - log_mean) / log_std
        score = clip(round(5.5 - z / SCORE_RANGE_SD_LOG * 4.5), 1, 10)

    At z = 0 (geometric-mean commute): raw score = 5.5, rounds to 5 or 6.
    At z = ±SCORE_RANGE_SD_LOG: raw score = 1 or 10.
    """
    log_m = np.log(commute_minutes)
    mean  = log_m.mean()
    std   = log_m.std()
    z     = (log_m - mean) / std
    raw   = 5.5 - z / SCORE_RANGE_SD_LOG * 4.5
    return raw.clip(1, 10).round().astype(int)


def calculate_commute_scores(
    year_month: str = ANALYSIS_MONTH,
    station_scope: str = "residential",
    weight_method: str = "destinations",
    scoring_method: str = "log",
    verbose: bool = True,
    start_hour: int = DESTINATIONS_START_HOUR,
    end_hour: int = DESTINATIONS_END_HOUR,
    custom_weekday_window: tuple[int, int] | None = None,
    custom_weekend_window: tuple[int, int] | None = None,
    custom_weekend_weight_ratio: float = 0.4,
    tap: str = "out",
) -> pd.DataFrame:
    """
    Assign each MRT station a 1-10 commute score based on its expected
    commute time in raw minutes (not the penalty score).

    Parameters
    ----------
    year_month : str
        Month to analyse.
    station_scope : str
        Which stations to score.
        "residential" (default) — all residential MRT stations.
        "hdb"                   — HDB estate MRT stations only.
        "all"                   — every station in the travel-time dataset.
    weight_method : str
        Passed through to calculate_data_driven_ratings.
        "destinations"       — real-world destinations weighting (default).
        "morning_peak"       — weekday morning tap-out weights.
        "all_hours_weighted" — full-day, day-count-weighted tap-out weights.
        "custom"             — user-defined windows; see custom_* params.
    scoring_method : str
        How to assign 1-10 scores from expected commute minutes.
        "log"    — z-scores of log(minutes); symmetric 1s and 10s (default).
        "linear" — z-scores of raw minutes; produces more 1s than 10s due to
                   the natural right-skew of commute-time distributions.
    verbose : bool
        If True (default), print distribution summary, score boundaries, and
        full station listing.  Set to False to suppress all output.
    custom_weekday_window : (int, int) or None
        For "custom": (from_hour, to_hour) for weekday tap-outs.
    custom_weekend_window : (int, int) or None
        For "custom": (from_hour, to_hour) for weekend/PH tap-outs.
    custom_weekend_weight_ratio : float
        For "custom": weekend weight relative to weekday (default 0.4).
    tap : str
        Which passenger flow to use as destination weights.
        "out"  — tap-outs (arrivals at destination); default.
        "in"   — tap-ins (departures from destination).
        "both" — sum of tap-ins and tap-outs.

    Returns a DataFrame indexed by station name with columns:
        commute_score, expected_commute_minutes
    """
    # Compute expected commute minutes for ALL stations (so the centroid uses
    # the full travel-time matrix regardless of scope), then filter to the
    # requested scope before z-scoring so the 1-10 range is fully utilised.
    results_all = calculate_data_driven_ratings(
        year_month=year_month,
        station_scope="all",
        verbose=False,
        weight_method=weight_method,
        start_hour=start_hour,
        end_hour=end_hour,
        custom_weekday_window=custom_weekday_window,
        custom_weekend_window=custom_weekend_window,
        custom_weekend_weight_ratio=custom_weekend_weight_ratio,
        tap=tap,
    )

    # Filter to scope — expected commute minutes are the same for each station
    # across all scopes because the computation always used all weights.
    if station_scope == "residential":
        scope_stations = set(get_residential_mrt_stations())
        results = results_all[results_all.index.isin(scope_stations)]
    elif station_scope == "hdb":
        scope_stations = set(get_hdb_mrt_stations())
        results = results_all[results_all.index.isin(scope_stations)]
    else:
        results = results_all

    minutes = results[COLUMN_EXPECTED_COMMUTE_MINUTES]
    mean    = minutes.mean()
    std     = minutes.std()

    scope_labels = {"residential": "residential only", "hdb": "HDB only", "all": "all stations"}
    scope_label = scope_labels.get(station_scope, station_scope)

    if verbose:
        print("=" * 60)
        print(f"Distribution of expected commute times ({year_month}, {scope_label})")
        print("=" * 60)
        print(f"  count  : {len(minutes)}")
        print(f"  mean   : {mean:.1f} min")
        print(f"  std    : {std:.1f} min")
        print(f"  min    : {minutes.min():.1f} min  ({minutes.idxmin()})")
        print(f"  25th % : {minutes.quantile(0.25):.1f} min")
        print(f"  median : {minutes.median():.1f} min")
        print(f"  75th % : {minutes.quantile(0.75):.1f} min")
        print(f"  max    : {minutes.max():.1f} min  ({minutes.idxmax()})")
        print()

    # Score boundary table.
    # Inverted from round(5.5 - z/SD * 4.5) = s:
    #   boundary between score s and s-1 is where raw = s - 0.5
    #   → z_boundary = (5.5 - (s - 0.5)) / (4.5 / SD) = (6 - s) * SD / 4.5
    # For "linear": t = mean + z * std
    # For "log":    t = exp(log_mean + z * log_std)
    if scoring_method == "log":
        scores  = assign_commute_scores_log(minutes)
        SD      = SCORE_RANGE_SD_LOG
        log_m   = np.log(minutes)
        lmean   = log_m.mean()
        lstd    = log_m.std()
        K       = 4.5 / SD
        def t_boundary(s_offset: float) -> float:
            return float(np.exp(lmean + s_offset / K * lstd))
        method_label = f"log(minutes), SCORE_RANGE_SD_LOG = {SD}"
    elif scoring_method == "linear":
        scores  = assign_commute_scores(minutes)
        SD      = SCORE_RANGE_SD
        K       = 4.5 / SD
        def t_boundary(s_offset: float) -> float:
            return mean + s_offset / K * std
        method_label = f"raw minutes, SCORE_RANGE_SD = {SD}"
    else:
        raise ValueError(
            f"Unknown scoring_method: {scoring_method!r}. "
            f"Use 'log' or 'linear'."
        )

    if verbose:
        print(f"  Score boundaries  ({method_label})")
        print(f"  {'score':>5}  {'commute range':>22}  {'n':>3}")
        print(f"  {'-----':>5}  {'--------------------':>22}  {'--':>3}")
        for s in range(10, 0, -1):
            t_upper = t_boundary(6 - s)   # upper minute bound (inclusive) for score s
            t_lower = t_boundary(5 - s)   # lower minute bound (exclusive) for score s
            if s == 10:
                range_str = f"<= {t_upper:.1f} min"
            elif s == 1:
                range_str = f">  {t_lower:.1f} min"
            else:
                range_str = f"{t_lower:.1f} – {t_upper:.1f} min"
            n = (scores == s).sum()
            print(f"  {s:>5}  {range_str:>22}  {n:>3}")
        print()

    # Full station listing (scoped)
    results = results.copy()
    results["commute_score"] = scores
    results = results.sort_values(
        ["commute_score", COLUMN_EXPECTED_COMMUTE_MINUTES],
        ascending=[False, True],
    )

    if verbose:
        print("=" * 60)
        print(f"Station commute scores  ({year_month}, {scope_label})")
        print("=" * 60)
        print(
            results[["commute_score", COLUMN_EXPECTED_COMMUTE_MINUTES]]
            .rename(columns={COLUMN_EXPECTED_COMMUTE_MINUTES: "exp_commute_min"})
            .to_string()
        )

    return results[["commute_score", COLUMN_EXPECTED_COMMUTE_MINUTES]]


# ---------------------------------------------------------------------------
# Monthly normalization factors
# ---------------------------------------------------------------------------

# Each entry maps a YYYYMM string to (effective_weekday_count, effective_non_weekday_count).
# These are the divisors used to convert monthly totals into daily averages.
#
# The counts must account for:
#   - Calendar weekdays and non-weekdays in the month
#   - Public holidays that fall on weekdays (they reduce the effective weekday count)
#   - Public holidays that fall on weekends, whose off-in-lieu Monday is inconsistently
#     observed across Singapore companies (apply a 0.5 / 0.5 split)
#
# Note: the LTA data labels each day as WEEKDAY or WEEKENDS/HOLIDAY based on the
# official calendar.  A Monday that is not officially declared a public holiday will
# appear in the WEEKDAY bucket even if many workers took it as off-in-lieu.  The
# effective counts below correct for this by treating such a Monday as contributing
# only 0.5 to the weekday count (and 0.5 to the non-weekday count).
_MONTH_NORMALIZATION_FACTORS = {

    # --- June 2025 ---
    # Calendar: 21 weekdays (Mon–Fri), 9 non-weekdays (Sat–Sun).
    # Hari Raya Haji fell on Saturday 7 Jun — already a non-weekday; no direct
    # reduction of the weekday count.
    # Off-in-lieu for Monday 9 Jun is inconsistent:
    #   50% of companies treated Mon 9 Jun as OIL (holiday behaviour)
    #   50% treated Mon 9 Jun as a normal workday
    # → Mon 9 Jun counts as 0.5 effective weekdays + 0.5 effective non-weekdays.
    # Effective weekday count    = 21 − 0.5 = 20.5
    # Effective non-weekday count =  9 + 0.5 =  9.5
    "202506": (20.5, 9.5),

    # --- November 2025 ---
    # Calendar: 30 days. Nov 1 = Saturday.
    # No public holidays in November 2025.
    # Weekdays (Mon–Fri): 3–7, 10–14, 17–21, 24–28 = 20 days
    # Non-weekdays (Sat–Sun): 1–2, 8–9, 15–16, 22–23, 29–30 = 10 days
    "202511": (20.0, 10.0),

    # --- December 2025 ---
    # Calendar: 31 days. Dec 1 = Monday.
    # Public holiday: Christmas Day, 25 Dec (Thursday) — falls on a weekday.
    # No off-in-lieu complexity (PH is on a weekday, not a weekend).
    # Weekdays (Mon–Fri) before PH adjustment: 23 days.  Minus Dec 25 = 22.
    # Non-weekdays (Sat–Sun): 8 days.  Plus Dec 25 PH = 9.
    "202512": (22.0, 9.0),

    # --- January 2026 ---
    # Calendar: 31 days. Jan 1 = Thursday.
    # Public holiday: New Year's Day, 1 Jan (Thursday) — falls on a weekday.
    # Chinese New Year 2026 falls on 17 Feb — not in January.
    # No off-in-lieu complexity (PH is on a weekday, not a weekend).
    # Weekdays (Mon–Fri) before PH adjustment: 22 days.  Minus Jan 1 = 21.
    # Non-weekdays (Sat–Sun): 9 days.  Plus Jan 1 PH = 10.
    "202601": (21.0, 10.0),
}


def get_normalization_factors(year_month: str) -> tuple[float, float]:
    """
    Return (effective_weekday_count, effective_non_weekday_count) for a month.

    Raises NotImplementedError if the month has no entry yet — add one to
    _MONTH_NORMALIZATION_FACTORS above.
    """
    if year_month not in _MONTH_NORMALIZATION_FACTORS:
        raise NotImplementedError(
            f"Normalization factors for '{year_month}' are not defined. "
            f"Add an entry to _MONTH_NORMALIZATION_FACTORS in data_driven_rankings.py."
        )
    return _MONTH_NORMALIZATION_FACTORS[year_month]


# ---------------------------------------------------------------------------
# Processed data output
# ---------------------------------------------------------------------------

PROCESSED_DATA_DIR = os.path.join(_REPO_ROOT, "mrt_volume", "data", "processed")

# Column names for the processed (normalized) output CSV
COLUMN_STATION_CODES = "station_codes"    # original combined code, e.g. "EW16/NE3/TE17"
# COLUMN_DAILY_AVG_TAP_IN and COLUMN_DAILY_AVG_TAP_OUT are defined in the constants section above


def write_processed_daily_averages(year_month: str) -> str:
    """
    Normalize a raw monthly station-volume CSV into per-day averages and
    write the result as a clean, pre-processed CSV.

    Output format — one row per station × day_type × hour:

        station_name   : human-readable name (no code lookup required downstream)
        station_codes  : original combined source code (e.g. "EW16/NE3/TE17"),
                         preserved so future code can look up any individual line
                         code and still find the right station row
        day_type       : WEEKDAY or WEEKENDS/HOLIDAY
        time_per_hour  : hour of day, 0–23
        daily_avg_tap_in  : monthly total ÷ effective days of that day type
        daily_avg_tap_out : monthly total ÷ effective days of that day type

    Returns the path of the written file.
    """
    raw_path = os.path.join(
        _REPO_ROOT, "mrt_volume", "data", "station_volumes",
        f"transport_node_train_{year_month}.csv",
    )
    df = load_volume_data(raw_path)

    weekday_count, non_weekday_count = get_normalization_factors(year_month)
    day_type_to_count = {WEEKDAY: weekday_count, NOT_WEEKDAY: non_weekday_count}

    df[COLUMN_DAILY_AVG_TAP_IN] = df.apply(
        lambda row: row[TOTAL_TAP_IN_VOLUME]  / day_type_to_count[row[DAY_TYPE]], axis=1
    )
    df[COLUMN_DAILY_AVG_TAP_OUT] = df.apply(
        lambda row: row[TOTAL_TAP_OUT_VOLUME] / day_type_to_count[row[DAY_TYPE]], axis=1
    )

    output_df = (
        df[[STATION_NAME, STATION_CODE, DAY_TYPE, TIME_OF_DAY,
            COLUMN_DAILY_AVG_TAP_IN, COLUMN_DAILY_AVG_TAP_OUT]]
        .rename(columns={STATION_CODE: COLUMN_STATION_CODES})
        .sort_values([STATION_NAME, DAY_TYPE, TIME_OF_DAY])
        .reset_index(drop=True)
    )

    os.makedirs(PROCESSED_DATA_DIR, exist_ok=True)
    output_path = os.path.join(
        PROCESSED_DATA_DIR, f"transport_node_train_{year_month}_daily_avg.csv"
    )
    output_df.to_csv(output_path, index=False)
    print(f"Processed data written to: {output_path}")
    print(f"  Rows: {len(output_df)}  |  "
          f"Weekday divisor: {weekday_count}  |  Non-weekday divisor: {non_weekday_count}")
    return output_path


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Rank Singapore MRT stations by expected commute time.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python data_driven_rankings.py\n"
            "  python data_driven_rankings.py --display log --scope residential\n"
            "  python data_driven_rankings.py --display minutes --scope all\n"
            "  python data_driven_rankings.py --display linear --scope hdb"
        ),
    )
    parser.add_argument(
        "--display",
        choices=["minutes", "linear", "log"],
        default="log",
        help=(
            "minutes  — show raw expected commute time only\n"
            "linear   — 1-10 score using z-scores of raw minutes\n"
            "log      — 1-10 score using z-scores of log(minutes) [default]"
        ),
    )
    parser.add_argument(
        "--scope",
        choices=["hdb", "residential", "all"],
        default="residential",
        help=(
            "hdb         — HDB estate stations only\n"
            "residential — all residential stations [default]\n"
            "all         — every station in the travel-time dataset"
        ),
    )
    args = parser.parse_args()

    if args.display == "minutes":
        calculate_data_driven_ratings(
            weight_method="destinations",
            station_scope=args.scope,
            verbose=True,
        )
    else:
        calculate_commute_scores(
            weight_method="destinations",
            scoring_method=args.display,
            station_scope=args.scope,
        )
