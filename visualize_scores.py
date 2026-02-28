# -*- coding: utf-8 -*-
"""
visualize_scores.py

Produces an interactive HTML map of Singapore MRT stations coloured by
1-10 commute score (deep red = 1, deep green = 10).

Run from the repo root:
    python visualize_scores.py
"""

from __future__ import annotations

import io
import json
import math
import os
import sys
import zipfile

import folium
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_driven_rankings import (
    calculate_commute_scores,
    calculate_data_driven_ratings,
    ANALYSIS_MONTH,
    COLUMN_EXPECTED_COMMUTE_MINUTES,
    get_residential_mrt_stations,
    get_hdb_mrt_stations,
)

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
OUTPUT_HTML       = os.path.join(_REPO_ROOT, "mrt_commute_scores.html")
COORDS_CACHE_PATH = os.path.join(_REPO_ROOT, "station_coords_cache.json")

API_KEY = "RomoaQ6ATPuLJ2PyRmXi2g=="

# ---------------------------------------------------------------------------
# Shapefile name → our station name corrections (capitalisation differences)
# ---------------------------------------------------------------------------
_SHP_NAME_OVERRIDES = {
    "Harbourfront": "HarbourFront",
    "Macpherson":   "MacPherson",
    "One-North":    "one-north",
}


# ---------------------------------------------------------------------------
# SVY21 → WGS84 conversion
# ---------------------------------------------------------------------------

def _svy21_to_wgs84(N: float, E: float) -> tuple[float, float]:
    """Convert SVY21 (northing, easting) metres to WGS84 (lat, lng) degrees."""
    a = 6378137.0
    f = 1 / 298.257223563
    b = a * (1 - f)
    e2 = 1 - (b / a) ** 2

    No, Eo, k0 = 38744.572, 28001.642, 1.0
    phi0 = math.radians(1 + 22 / 60)       # 1°22'N
    lam0 = math.radians(103 + 50 / 60)     # 103°50'E

    N_ = N - No
    E_ = E - Eo

    def M(phi: float) -> float:
        return a * (
            (1 - e2/4 - 3*e2**2/64 - 5*e2**3/256) * phi
            - (3*e2/8 + 3*e2**2/32 + 45*e2**3/1024) * math.sin(2*phi)
            + (15*e2**2/256 + 45*e2**3/1024) * math.sin(4*phi)
            - (35*e2**3/3072) * math.sin(6*phi)
        )

    M0 = M(phi0)
    phi1 = (N_ / k0 + M0) / a
    for _ in range(10):
        phi1 = (
            (N_ / k0 + M0 - a * (
                (1-e2/4-3*e2**2/64-5*e2**3/256) * phi1
                - (3*e2/8+3*e2**2/32+45*e2**3/1024) * math.sin(2*phi1)
                + (15*e2**2/256+45*e2**3/1024) * math.sin(4*phi1)
                - (35*e2**3/3072) * math.sin(6*phi1)
            )) / a + phi1
        )

    sin1, cos1, tan1 = math.sin(phi1), math.cos(phi1), math.tan(phi1)
    eta2 = e2 / (1 - e2) * cos1**2
    nu   = a / math.sqrt(1 - e2 * sin1**2)
    rho  = a * (1 - e2) / (1 - e2 * sin1**2) ** 1.5
    T1, C1 = tan1**2, eta2
    D = E_ / (nu * k0)

    lat = phi1 - (nu * tan1 / rho) * (
        D**2 / 2
        - (5 + 3*T1 + 10*C1 - 4*C1**2 - 9*eta2) * D**4 / 24
        + (61 + 90*T1 + 298*C1 + 45*T1**2 - 252*eta2 - 3*C1**2) * D**6 / 720
    )
    lon = lam0 + (
        D
        - (1 + 2*T1 + C1) * D**3 / 6
        + (5 - 2*C1 + 28*T1 - 3*C1**2 + 8*eta2 + 24*T1**2) * D**5 / 120
    ) / cos1

    return math.degrees(lat), math.degrees(lon)


# ---------------------------------------------------------------------------
# Manual coordinate overrides for stations absent from the DataMall shapefile
# ---------------------------------------------------------------------------

# Stations that are missing from (or misnamed in) the LTA shapefile can be
# added here.  These are applied on top of the shapefile data and also
# persisted into the local cache so they survive across runs.
_MANUAL_COORDS: dict[str, tuple[float, float]] = {
    # New TEL station not yet in the DataMall shapefile
    "Gardens By The Bay": (1.27833, 103.86806),  # 1°16′42″N 103°52′05″E
}


# ---------------------------------------------------------------------------
# Fetch station coordinates from LTA DataMall (with local cache)
# ---------------------------------------------------------------------------

def fetch_station_coords() -> dict[str, tuple[float, float]]:
    """
    Return a dict mapping station name → (lat, lng) in WGS84.

    On the first call the LTA GeospatialWholeIsland TrainStation shapefile is
    downloaded, parsed, and saved to COORDS_CACHE_PATH as JSON.  Subsequent
    calls load directly from the cache — delete the file to force a refresh.

    Manual overrides in _MANUAL_COORDS are always applied (and saved into the
    cache) so stations absent from the shapefile are still available.
    """
    if os.path.exists(COORDS_CACHE_PATH):
        print(f"Loading station coordinates from cache: {COORDS_CACHE_PATH}")
        with open(COORDS_CACHE_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        # JSON stores lists; convert back to tuples
        coords = {name: tuple(latlon) for name, latlon in raw.items()}
        # Always apply manual overrides in case the cache pre-dates them
        coords.update(_MANUAL_COORDS)
        return coords

    print("Fetching station coordinates from LTA DataMall …")
    import shapefile  # pyshp

    headers = {"AccountKey": API_KEY, "accept": "application/json"}
    url = "https://datamall2.mytransport.sg/ltaodataservice/GeospatialWholeIsland?ID=TrainStation"
    link = requests.get(url, headers=headers).json()["value"][0]["Link"]
    data = requests.get(link).content
    z    = zipfile.ZipFile(io.BytesIO(data))

    nl = z.namelist()
    sf = shapefile.Reader(
        shp=io.BytesIO(z.read(next(n for n in nl if n.endswith(".shp")))),
        dbf=io.BytesIO(z.read(next(n for n in nl if n.endswith(".dbf")))),
        shx=io.BytesIO(z.read(next(n for n in nl if n.endswith(".shx")))),
    )

    fields = [f[0] for f in sf.fields[1:]]
    coords: dict[str, tuple[float, float]] = {}

    for sr in sf.shapeRecords():
        rec  = dict(zip(fields, sr.record))
        if rec["TYP_CD_DES"] != "MRT":
            continue
        raw_name = rec["STN_NAM_DE"].replace(" MRT STATION", "").title()
        name = _SHP_NAME_OVERRIDES.get(raw_name, raw_name)
        bbox = sr.shape.bbox
        cx = (bbox[0] + bbox[2]) / 2
        cy = (bbox[1] + bbox[3]) / 2
        coords[name] = _svy21_to_wgs84(cy, cx)

    coords.update(_MANUAL_COORDS)

    with open(COORDS_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(coords, f, indent=2)
    print(f"Coordinates cached to: {COORDS_CACHE_PATH}")

    return coords


# ---------------------------------------------------------------------------
# Colour mapping  (score 1 = deep red → score 10 = deep green)
# ---------------------------------------------------------------------------

def score_to_color(score: int) -> str:
    """Map integer score 1-10 to a hex colour on a red→yellow→green scale."""
    t = (score - 1) / 9          # 0.0 (worst) → 1.0 (best)
    if t <= 0.5:
        # Red (#cc2200) → Yellow (#ddcc00)
        r = 204
        g = int(t * 2 * 180)
        b = 0
    else:
        # Yellow → Green (#006600)
        r = int((1 - (t - 0.5) * 2) * 180)
        g = 180
        b = 0
    return f"#{r:02x}{g:02x}{b:02x}"


def text_color_for(score: int) -> str:
    """White text for low scores (dark bg), black for mid, white for high."""
    return "#ffffff" if score <= 3 or score >= 9 else "#111111"


# ---------------------------------------------------------------------------
# Build the map
# ---------------------------------------------------------------------------

def build_map(
    year_month: str = ANALYSIS_MONTH,
    weight_method: str = "all_hours_weighted",
    scoring_method: str = "log",
    station_scope: str = "residential",
) -> str:
    """
    Compute commute scores, fetch station coordinates, and write an HTML map.

    Parameters
    ----------
    year_month : str
        Month to analyse (must have a normalization entry), e.g. "202601".
    weight_method : str
        "morning_peak" or "all_hours_weighted" (default).
    scoring_method : str
        "log" (default), "linear", or "minutes" (raw minutes, no 1-10 scale).
    station_scope : str
        "residential" (default), "hdb", or "all".

    Returns the path of the written HTML file.
    """
    print("Computing commute scores …")
    if scoring_method == "minutes":
        # Raw minutes mode: fetch the full set (so centroid uses all weights),
        # filter to scope, then compute the colour scale within the subset so
        # the 1-10 range is always fully utilised.
        raw_df = calculate_data_driven_ratings(
            year_month=year_month,
            station_scope="all",
            verbose=False,
            weight_method=weight_method,
        )
        if station_scope == "residential":
            scope_stations = set(get_residential_mrt_stations())
            raw_df = raw_df[raw_df.index.isin(scope_stations)]
        elif station_scope == "hdb":
            scope_stations = set(get_hdb_mrt_stations())
            raw_df = raw_df[raw_df.index.isin(scope_stations)]
        minutes_series = raw_df[COLUMN_EXPECTED_COMMUTE_MINUTES]
        mn, mx = minutes_series.min(), minutes_series.max()
        # Map minutes linearly to 1-10 so the colour scale is still usable
        scores_df = raw_df[[COLUMN_EXPECTED_COMMUTE_MINUTES]].copy()
        scores_df["commute_score"] = (
            (1 + 9 * (mx - minutes_series) / (mx - mn))
            .round()
            .clip(1, 10)
            .astype(int)
        )
        display_label = "raw minutes"
    else:
        scores_df = calculate_commute_scores(
            year_month=year_month,
            station_scope=station_scope,
            weight_method=weight_method,
            scoring_method=scoring_method,
            verbose=False,
        )
        display_label = f"{scoring_method} score"

    coords = fetch_station_coords()

    missing = [s for s in scores_df.index if s not in coords]
    if missing:
        print(f"[!] No coordinates found for {len(missing)} stations: {missing}")

    # Map centred on Singapore
    m = folium.Map(
        location=[1.3521, 103.8198],
        zoom_start=12,
        tiles="CartoDB positron",
        control_scale=True,
    )

    for station, row in scores_df.iterrows():
        if station not in coords:
            continue
        lat, lng = coords[station]
        score    = int(row["commute_score"])
        minutes  = row[COLUMN_EXPECTED_COMMUTE_MINUTES]
        bg       = score_to_color(score)
        fg       = text_color_for(score)

        # Invisible anchor marker so the tooltip has a proper location
        folium.CircleMarker(
            location=[lat, lng],
            radius=14,
            color=bg,
            fill=True,
            fill_color=bg,
            fill_opacity=0.92,
            weight=1.5,
            tooltip=folium.Tooltip(
                f"<b>{station}</b><br>"
                + (
                    f"Expected commute: <b>{minutes:.0f} min</b>"
                    if scoring_method == "minutes"
                    else f"Score: <b>{score}/10</b><br>Expected commute: {minutes:.0f} min"
                ),
                sticky=False,
            ),
        ).add_to(m)

        # Score number centred on the circle
        tooltip_text = (
            f"<b>{station}</b><br>"
            + (
                f"Expected commute: <b>{minutes:.0f} min</b>"
                if scoring_method == "minutes"
                else f"Score: <b>{score}/10</b><br>Expected commute: {minutes:.0f} min"
            )
        )
        folium.Marker(
            location=[lat, lng],
            icon=folium.DivIcon(
                html=(
                    f'<div style="'
                    f'width:22px;height:22px;'
                    f'border-radius:50%;'
                    f'background:{bg};'
                    f'border:1.5px solid rgba(0,0,0,0.35);'
                    f'display:flex;align-items:center;justify-content:center;'
                    f'font-family:Arial,sans-serif;font-size:11px;font-weight:bold;'
                    f'color:{fg};'
                    f'">{score}</div>'
                ),
                icon_size=(22, 22),
                icon_anchor=(11, 11),
            ),
            tooltip=folium.Tooltip(tooltip_text, sticky=False),
        ).add_to(m)

    # -----------------------------------------------------------------------
    # Legend
    # -----------------------------------------------------------------------
    legend_html = """
    <div style="
        position: fixed;
        bottom: 30px; right: 15px;
        z-index: 1000;
        background: white;
        border: 1px solid #bbb;
        border-radius: 6px;
        padding: 10px 14px;
        font-family: Arial, sans-serif;
        font-size: 12px;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.25);
        min-width: 130px;
    ">
    <b>Commute score</b><br>
    <span style="font-size:10px;color:#666;">lower = longer commute</span><br><br>
    """
    for s in range(10, 0, -1):
        bg = score_to_color(s)
        fg = text_color_for(s)
        legend_html += (
            f'<div style="display:flex;align-items:center;margin:2px 0;">'
            f'<div style="width:20px;height:20px;border-radius:50%;background:{bg};'
            f'border:1px solid rgba(0,0,0,0.3);display:inline-flex;align-items:center;'
            f'justify-content:center;font-weight:bold;font-size:10px;color:{fg};'
            f'margin-right:6px;">{s}</div>'
            f'{"Best" if s == 10 else "Worst" if s == 1 else ""}'
            f'</div>\n'
        )
    legend_html += "</div>"
    m.get_root().html.add_child(folium.Element(legend_html))

    # -----------------------------------------------------------------------
    # Title
    # -----------------------------------------------------------------------
    scope_labels = {"residential": "residential", "hdb": "HDB", "all": "all stations"}
    title_html = f"""
    <div style="
        position: fixed;
        top: 10px; left: 50%; transform: translateX(-50%);
        z-index: 1000;
        background: rgba(255,255,255,0.92);
        border: 1px solid #bbb;
        border-radius: 6px;
        padding: 6px 16px;
        font-family: Arial, sans-serif;
        font-size: 14px;
        font-weight: bold;
        box-shadow: 2px 2px 6px rgba(0,0,0,0.2);
        pointer-events: none;
    ">
        Singapore MRT Commute Score &nbsp;|&nbsp;
        <span style="font-weight:normal;font-size:12px;">
            {year_month[:4]}-{year_month[4:]} &middot; all-hours weighted &middot;
            {display_label} &middot; {scope_labels.get(station_scope, station_scope)} stations
        </span>
    </div>
    """
    m.get_root().html.add_child(folium.Element(title_html))

    m.save(OUTPUT_HTML)
    print(f"\nMap saved to: {OUTPUT_HTML}")
    return OUTPUT_HTML


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML map of Singapore MRT commute scores.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python visualize_scores.py\n"
            "  python visualize_scores.py --display log --scope residential\n"
            "  python visualize_scores.py --display minutes --scope all\n"
            "  python visualize_scores.py --display linear --scope hdb"
        ),
    )
    parser.add_argument(
        "--display",
        choices=["minutes", "linear", "log"],
        default="log",
        help=(
            "minutes  — colour by raw expected commute time (no 1-10 scale)\n"
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

    build_map(scoring_method=args.display, station_scope=args.scope)
