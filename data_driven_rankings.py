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

import pandas as pd

from mrt_distance.mrt_distance import (
    read_travel_time_data,
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

# Result column names
COLUMN_MORNING_TAP_IN  = "weekday_morning_tap_in"
COLUMN_MORNING_TAP_OUT = "weekday_morning_tap_out"
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
    number of lines).  Instead we extract only the first component as the
    canonical lookup key, which uniquely identifies the physical station.
    """
    df = pd.read_csv(file_path)

    # Use only the first code from combined interchange codes (e.g. "EW16" from
    # "EW16/NE3/TE17") — this avoids the overcounting that would result from
    # exploding the codes into separate rows.
    df[CANONICAL_CODE] = df[STATION_CODE].str.split("/").str[0]

    # Enrich with human-readable station names
    mapping_df = get_station_code_to_name_mapping()
    mapping_df = mapping_df.rename(columns={STATION_CODE: CANONICAL_CODE})
    df = df.merge(mapping_df, on=CANONICAL_CODE)

    # Exclude LRT lines — their structural traffic patterns differ from MRT
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
    Filter to weekday morning rows and aggregate tap-in / tap-out totals
    per station across the analysis window.

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
                COLUMN_MORNING_TAP_IN:  (TOTAL_TAP_IN_VOLUME,  "sum"),
                COLUMN_MORNING_TAP_OUT: (TOTAL_TAP_OUT_VOLUME, "sum"),
            }
        )
        .reset_index()
        .sort_values(COLUMN_MORNING_TAP_OUT, ascending=False)
    )
    return aggregated


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
# Ranking
# ---------------------------------------------------------------------------

def calculate_data_driven_ratings() -> pd.DataFrame:
    """
    Main entry point.  Prints diagnostic tables and returns a DataFrame of
    HDB stations ranked by expected commute time (ascending).
    """
    # 1. Load and enrich volume data
    volume_df = load_volume_data(DATA_PATH__PASSENGER_VOLUME_BY_TRAIN_STATIONS)

    # 2. Aggregate to weekday morning window
    morning_df = get_weekday_morning_aggregates(volume_df)

    # 3. Print diagnostics: where people work and where they live
    print("=" * 60)
    print(f"Top {DIAGNOSTIC_TOP_N} Work Destinations  "
          f"(weekday morning tap-OUTs, hours {WEEKDAY_MORNING_START_HOUR}–{WEEKDAY_MORNING_END_HOUR - 1})")
    print("=" * 60)
    print(
        morning_df[[STATION_NAME, COLUMN_MORNING_TAP_OUT]]
        .head(DIAGNOSTIC_TOP_N)
        .to_string(index=False)
    )

    print()
    print("=" * 60)
    print(f"Top {DIAGNOSTIC_TOP_N} Residential Origins  "
          f"(weekday morning tap-INs, hours {WEEKDAY_MORNING_START_HOUR}–{WEEKDAY_MORNING_END_HOUR - 1})")
    print("=" * 60)
    print(
        morning_df[[STATION_NAME, COLUMN_MORNING_TAP_IN]]
        .sort_values(COLUMN_MORNING_TAP_IN, ascending=False)
        .head(DIAGNOSTIC_TOP_N)
        .to_string(index=False)
    )
    print()

    # 4. Derive destination weights from tap-out volumes
    travel_times = read_travel_time_data()
    travel_station_names = set(travel_times[COLUMN_FROM_STATION_NAME].unique())
    work_weights = derive_work_destination_weights(morning_df, travel_station_names)

    # 5. Filter travel-time matrix to the stations we have weights for,
    #    and restrict destinations to HDB residential stations only
    residential_stations = set(get_hdb_mrt_stations())
    travel_times = travel_times[
        travel_times[COLUMN_FROM_STATION_NAME].isin(work_weights)
    ]
    travel_times = travel_times[
        travel_times[COLUMN_TO_STATION_NAME].isin(residential_stations)
    ]

    # 6. Compute weighted trip durations
    travel_times = travel_times.copy()
    travel_times[COLUMN_WEIGHT] = (
        travel_times[COLUMN_FROM_STATION_NAME].map(work_weights)
    )
    travel_times[COLUMN_WEIGHTED_TRIP_DURATION] = (
        travel_times[COLUMN_WEIGHT] * travel_times[COLUMN_TRIP_DURATION_IN_MINUTES]
    )

    # 7. Aggregate per residential station and rank
    results = (
        travel_times[[COLUMN_TO_STATION_NAME, COLUMN_WEIGHTED_TRIP_DURATION]]
        .groupby(COLUMN_TO_STATION_NAME)
        .sum()
        .rename(columns={COLUMN_WEIGHTED_TRIP_DURATION: COLUMN_EXPECTED_COMMUTE_MINUTES})
        .sort_values(COLUMN_EXPECTED_COMMUTE_MINUTES, ascending=True)
    )
    results.index.name = "residential_station"

    print("=" * 60)
    print("HDB Station Rankings — Expected Commute Time")
    print(f"(destination weights from weekday morning tap-outs, "
          f"hours {WEEKDAY_MORNING_START_HOUR}–{WEEKDAY_MORNING_END_HOUR - 1})")
    print("Lower score = shorter expected commute based on real MRT usage")
    print("=" * 60)
    print(results.to_string())

    return results


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
COLUMN_STATION_CODES    = "station_codes"    # original combined code, e.g. "EW16/NE3/TE17"
COLUMN_DAILY_AVG_TAP_IN  = "daily_avg_tap_in"
COLUMN_DAILY_AVG_TAP_OUT = "daily_avg_tap_out"


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
    write_processed_daily_averages("202506")
    print()
    calculate_data_driven_ratings()
