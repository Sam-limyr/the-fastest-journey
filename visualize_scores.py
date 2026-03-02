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

_REPO_ROOT            = os.path.dirname(os.path.abspath(__file__))
OUTPUT_HTML           = os.path.join(_REPO_ROOT, "mrt_commute_scores.html")
COORDS_CACHE_PATH     = os.path.join(_REPO_ROOT, "station_coords_cache.json")
MRT_LINES_CACHE_PATH  = os.path.join(_REPO_ROOT, "mrt_lines_cache.geojson")
FUTURE_MRT_CACHE_PATH = os.path.join(_REPO_ROOT, "future_mrt_cache.geojson")

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
# MRT line colour mapping (matched against OSM name / ref tags)
# ---------------------------------------------------------------------------
_MRT_LINE_COLORS: dict[str, str] = {
    "north south":  "#D42E12",
    "nsl":          "#D42E12",
    "east west":    "#009645",
    "ewl":          "#009645",
    "circle":       "#FA9E0D",
    "ccl":          "#FA9E0D",
    "downtown":     "#005EC4",
    "dtl":          "#005EC4",
    "thomson":      "#9D5B25",
    "tel":          "#9D5B25",
    "north east":   "#9900AA",
    "nel":          "#9900AA",
    "jurong":       "#0099AA",
    "jrl":          "#0099AA",
    "cross island": "#009999",
    "crl":          "#009999",
}

_OVERPASS_URL = "https://overpass-api.de/api/interpreter"
_SG_BBOX      = "1.15,103.59,1.47,104.05"


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

_MANUAL_COORDS: dict[str, tuple[float, float]] = {
    # New TEL station not yet in the DataMall shapefile
    "Gardens by the Bay": (1.27833, 103.86806),  # 1°16′42″N 103°52′05″E
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
# MRT network line data from OpenStreetMap via Overpass (with local cache)
# ---------------------------------------------------------------------------

def _get_line_color(tags: dict) -> str:
    """Determine a line colour from OSM way tags."""
    raw = tags.get("colour") or tags.get("color") or ""
    if raw:
        return raw if raw.startswith("#") else f"#{raw}"
    text = ((tags.get("name") or "") + " " + (tags.get("ref") or "")).lower()
    for keyword, colour in _MRT_LINE_COLORS.items():
        if keyword in text:
            return colour
    return "#888888"


def _overpass_to_geojson(elements: list) -> dict:
    """Convert Overpass JSON way elements (with geometry) to a GeoJSON FeatureCollection.

    Ways with a 'service' tag (yard, siding, crossover, depot tracks) are skipped
    so that only main running lines appear on the map.
    """
    features = []
    for el in elements:
        if el.get("type") != "way":
            continue
        geom = el.get("geometry", [])
        if len(geom) < 2:
            continue
        tags = el.get("tags", {})
        # Skip depot/yard/siding/crossover tracks
        if tags.get("service"):
            continue
        coords = [[pt["lon"], pt["lat"]] for pt in geom]
        features.append({
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": coords},
            "properties": {
                "color": _get_line_color(tags),
                "name":  tags.get("name", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features}


def fetch_mrt_lines() -> dict | None:
    """Fetch Singapore MRT/LRT track geometries from Overpass API (cached locally)."""
    if os.path.exists(MRT_LINES_CACHE_PATH):
        print(f"Loading MRT lines from cache: {MRT_LINES_CACHE_PATH}")
        with open(MRT_LINES_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)

    print("Fetching MRT line data from Overpass API …")
    query = (
        f"[out:json][timeout:60];"
        f"("
        f'way["railway"="subway"][!"service"]({_SG_BBOX});'
        f'way["railway"="light_rail"][!"service"]({_SG_BBOX});'
        f'way["railway"="monorail"][!"service"]({_SG_BBOX});'
        f");out geom;"
    )
    try:
        resp = requests.get(_OVERPASS_URL, params={"data": query}, timeout=90)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception as exc:
        print(f"[!] Could not fetch MRT lines from Overpass: {exc}")
        return None

    geojson = _overpass_to_geojson(elements)
    if not geojson["features"]:
        print("[!] Overpass returned no MRT line features.")
        return None

    with open(MRT_LINES_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    print(f"MRT lines cached ({len(geojson['features'])} segments) → {MRT_LINES_CACHE_PATH}")
    return geojson


def fetch_future_mrt_lines() -> dict | None:
    """Fetch under-construction / proposed MRT lines from Overpass API (cached locally)."""
    if os.path.exists(FUTURE_MRT_CACHE_PATH):
        print(f"Loading future MRT lines from cache: {FUTURE_MRT_CACHE_PATH}")
        with open(FUTURE_MRT_CACHE_PATH, encoding="utf-8") as f:
            return json.load(f)

    print("Fetching future MRT line data from Overpass API …")
    query = (
        f"[out:json][timeout:60];"
        f"("
        f'way["railway"="construction"]["construction"~"subway|light_rail|monorail"][!"service"]({_SG_BBOX});'
        f'way["railway"="proposed"]["proposed"~"subway|light_rail|monorail"][!"service"]({_SG_BBOX});'
        f");out geom;"
    )
    try:
        resp = requests.get(_OVERPASS_URL, params={"data": query}, timeout=90)
        resp.raise_for_status()
        elements = resp.json().get("elements", [])
    except Exception as exc:
        print(f"[!] Could not fetch future MRT lines: {exc}")
        return None

    geojson = _overpass_to_geojson(elements)
    if not geojson["features"]:
        print("[!] No future MRT line features found in Overpass response.")
        return None

    with open(FUTURE_MRT_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(geojson, f)
    print(f"Future MRT lines cached ({len(geojson['features'])} segments) → {FUTURE_MRT_CACHE_PATH}")
    return geojson


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
        raw_df = calculate_data_driven_ratings(
            year_month=year_month, station_scope="all", verbose=False, weight_method=weight_method,
        )
        if station_scope == "residential":
            raw_df = raw_df[raw_df.index.isin(set(get_residential_mrt_stations()))]
        elif station_scope == "hdb":
            raw_df = raw_df[raw_df.index.isin(set(get_hdb_mrt_stations()))]
        minutes_series = raw_df[COLUMN_EXPECTED_COMMUTE_MINUTES]
        mn, mx = minutes_series.min(), minutes_series.max()
        scores_df = raw_df[[COLUMN_EXPECTED_COMMUTE_MINUTES]].copy()
        scores_df["commute_score"] = (
            (1 + 9 * (mx - minutes_series) / (mx - mn))
            .round().clip(1, 10).astype(int)
        )
        display_label = "raw minutes"
    else:
        scores_df = calculate_commute_scores(
            year_month=year_month, station_scope=station_scope,
            weight_method=weight_method, scoring_method=scoring_method, verbose=False,
        )
        display_label = f"{scoring_method} score"

    # -----------------------------------------------------------------------
    # Per-score metadata: percentile range + absolute minutes range
    # "percentile" = fraction of stations with a LONGER commute (higher = better)
    # -----------------------------------------------------------------------
    all_minutes = scores_df[COLUMN_EXPECTED_COMMUTE_MINUTES]
    n_total = len(scores_df)
    score_meta: dict[int, dict | None] = {}
    for s in range(1, 11):
        grp_mins = all_minutes[scores_df["commute_score"] == s]
        if grp_mins.empty:
            score_meta[s] = None
            continue
        mn_m = grp_mins.min()
        mx_m = grp_mins.max()
        # pct_low  = % of stations with strictly longer commute than worst in group
        # pct_high = % of stations with commute >= best in group  (capped at 100)
        pct_lo = int(100 * (all_minutes > mx_m).sum() / n_total)
        pct_hi = int(100 * (all_minutes >= mn_m).sum() / n_total)
        score_meta[s] = {
            "pct_low":  pct_lo,
            "pct_high": pct_hi,
            "min_m":    round(mn_m),
            "max_m":    round(mx_m),
        }

    # -----------------------------------------------------------------------
    # Fetch assets
    # -----------------------------------------------------------------------
    coords = fetch_station_coords()
    missing = [s for s in scores_df.index if s not in coords]
    if missing:
        print(f"[!] No coordinates found for {len(missing)} stations: {missing}")

    mrt_geojson    = fetch_mrt_lines()
    future_geojson = fetch_future_mrt_lines()
    has_mrt    = bool(mrt_geojson    and mrt_geojson.get("features"))
    has_future = bool(future_geojson and future_geojson.get("features"))

    # -----------------------------------------------------------------------
    # Map
    # -----------------------------------------------------------------------
    m = folium.Map(
        location=[1.3521, 103.8198],
        zoom_start=12,
        tiles="CartoDB positron",
        control_scale=True,
    )

    # MRT line layers added first so they sit underneath the station circles
    mrt_fg    = folium.FeatureGroup(name="MRT Lines",        show=True)
    future_fg = folium.FeatureGroup(name="Future MRT Lines", show=False)

    if has_mrt:
        folium.GeoJson(
            mrt_geojson,
            style_function=lambda f: {
                "color":   f["properties"].get("color", "#888888"),
                "weight":  3,
                "opacity": 0.85,
            },
        ).add_to(mrt_fg)

    if has_future:
        folium.GeoJson(
            future_geojson,
            style_function=lambda f: {
                "color":     f["properties"].get("color", "#888888"),
                "weight":    2,
                "opacity":   0.65,
                "dashArray": "6 4",
            },
        ).add_to(future_fg)

    mrt_fg.add_to(m)
    future_fg.add_to(m)

    # One FeatureGroup per score level — enables min/max filtering via JS
    score_groups: dict[int, folium.FeatureGroup] = {}
    for s in range(1, 11):
        fg = folium.FeatureGroup(name=f"Score {s}", show=True)
        score_groups[s] = fg
        fg.add_to(m)

    # Station markers
    for station, row in scores_df.iterrows():
        if station not in coords:
            continue
        lat, lng = coords[station]
        score    = int(row["commute_score"])
        minutes  = row[COLUMN_EXPECTED_COMMUTE_MINUTES]
        bg       = score_to_color(score)
        fg_txt   = text_color_for(score)

        tooltip_html = (
            f"<b>{station}</b><br>"
            + (
                f"Expected commute: <b>{minutes:.0f} min</b>"
                if scoring_method == "minutes"
                else f"Score: <b>{score}/10</b><br>Expected commute: {minutes:.0f} min"
            )
        )
        target = score_groups[score]

        folium.CircleMarker(
            location=[lat, lng],
            radius=14,
            color=bg,
            fill=True,
            fill_color=bg,
            fill_opacity=0.92,
            weight=1.5,
            tooltip=folium.Tooltip(tooltip_html, sticky=False),
        ).add_to(target)

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
                    f'color:{fg_txt};'
                    f'">{score}</div>'
                ),
                icon_size=(22, 22),
                icon_anchor=(11, 11),
            ),
            tooltip=folium.Tooltip(tooltip_html, sticky=False),
        ).add_to(target)

    # -----------------------------------------------------------------------
    # Control panel (legend + filter + overlay toggles)
    # -----------------------------------------------------------------------

    # Build score rows (10 → 1)
    score_rows_html = ""
    for s in range(10, 0, -1):
        bg     = score_to_color(s)
        fg_txt = text_color_for(s)
        label  = "Best" if s == 10 else "Worst" if s == 1 else ""
        meta   = score_meta.get(s)
        if meta:
            pct_str = (
                f"{meta['pct_low']}th pct"
                if meta["pct_low"] == meta["pct_high"]
                else f"{meta['pct_low']}-{meta['pct_high']}th pct"
            )
            min_str = (
                f"{meta['min_m']} min"
                if meta["min_m"] == meta["max_m"]
                else f"{meta['min_m']}-{meta['max_m']} min"
            )
            detail = f"{pct_str} &middot; {min_str}"
        else:
            detail = "&mdash;"

        score_rows_html += (
            f'<div style="display:flex;align-items:flex-start;margin:2px 0;">'
            f'<div style="width:20px;height:20px;border-radius:50%;background:{bg};'
            f'border:1px solid rgba(0,0,0,0.3);display:inline-flex;align-items:center;'
            f'justify-content:center;font-weight:bold;font-size:10px;color:{fg_txt};'
            f'flex-shrink:0;">{s}</div>'
            f'<div style="margin-left:6px;line-height:1.4;">'
            f'{label}'
            f'<div class="score-detail" style="display:none;font-size:10px;color:#555;">'
            f'{detail}</div>'
            f'</div></div>\n'
        )

    min_opts = "".join(f'<option value="{s}">{s}</option>' for s in range(1, 11))
    max_opts = "".join(
        f'<option value="{s}"{" selected" if s == 10 else ""}>{s}</option>'
        for s in range(1, 11)
    )

    overlay_rows = ""
    if has_mrt:
        overlay_rows += (
            '<label style="cursor:pointer;">'
            '<input type="checkbox" id="mrtToggle" onchange="toggleMrtLines()" checked>'
            " MRT lines</label><br>"
        )
    if has_future:
        overlay_rows += (
            '<label style="cursor:pointer;">'
            '<input type="checkbox" id="futureToggle" onchange="toggleFutureLines()">'
            " Future lines</label><br>"
        )
    overlays_section = (
        f"<br><b>Overlays:</b><br>{overlay_rows}"
        if (has_mrt or has_future) else ""
    )

    control_html = f"""
<div id="ctrl-panel" style="
    position:fixed;bottom:30px;right:15px;z-index:1000;
    background:white;border:1px solid #bbb;border-radius:6px;
    padding:10px 14px;font-family:Arial,sans-serif;font-size:12px;
    box-shadow:2px 2px 6px rgba(0,0,0,0.25);min-width:165px;
    max-height:80vh;overflow-y:auto;
">
<b>Commute score</b><br>
<span style="font-size:10px;color:#666;">lower = longer commute</span><br>
<label style="font-size:11px;cursor:pointer;">
  <input type="checkbox" id="detailsToggle" onchange="toggleDetails()"> Show details
</label><br><br>
{score_rows_html}
<br><b>Filter:</b><br>
<span style="font-size:11px;">
  Min:&nbsp;<select id="minScore" onchange="applyScoreFilter()"
             style="width:40px;font-size:11px;">{min_opts}</select>
  &nbsp;Max:&nbsp;<select id="maxScore" onchange="applyScoreFilter()"
                          style="width:40px;font-size:11px;">{max_opts}</select>
</span>
{overlays_section}
</div>
"""

    # -----------------------------------------------------------------------
    # JavaScript — references Folium's auto-generated layer variable names
    # -----------------------------------------------------------------------
    sg_entries   = ", ".join(f"{s}: {score_groups[s].get_name()}" for s in range(1, 11))
    mrt_fg_js    = mrt_fg.get_name()    if has_mrt    else "null"
    future_fg_js = future_fg.get_name() if has_future else "null"
    map_var      = m.get_name()

    control_js = f"""
<script>
window.addEventListener('load', function() {{
    var mapObj      = {map_var};
    var scoreGroups = {{ {sg_entries} }};
    var mrtLayer    = {mrt_fg_js};
    var futureLayer = {future_fg_js};

    // Guarantee initial visibility matches checkboxes
    // (future lines hidden regardless of Folium show= behaviour)
    if (futureLayer) mapObj.removeLayer(futureLayer);

    window.applyScoreFilter = function() {{
        var mn = parseInt(document.getElementById('minScore').value);
        var mx = parseInt(document.getElementById('maxScore').value);
        if (mn > mx) {{ var t = mn; mn = mx; mx = t; }}
        for (var s = 1; s <= 10; s++) {{
            var grp = scoreGroups[s];
            if (!grp) continue;
            if (s >= mn && s <= mx) {{
                if (!mapObj.hasLayer(grp)) grp.addTo(mapObj);
            }} else {{
                if (mapObj.hasLayer(grp)) mapObj.removeLayer(grp);
            }}
        }}
    }};

    window.toggleDetails = function() {{
        var show = document.getElementById('detailsToggle').checked;
        document.querySelectorAll('.score-detail').forEach(function(el) {{
            el.style.display = show ? 'block' : 'none';
        }});
    }};

    window.toggleMrtLines = function() {{
        if (!mrtLayer) return;
        if (document.getElementById('mrtToggle').checked) {{
            mrtLayer.addTo(mapObj);
        }} else {{
            mapObj.removeLayer(mrtLayer);
        }}
    }};

    window.toggleFutureLines = function() {{
        if (!futureLayer) return;
        if (document.getElementById('futureToggle').checked) {{
            futureLayer.addTo(mapObj);
        }} else {{
            mapObj.removeLayer(futureLayer);
        }}
    }};
}});
</script>
"""

    m.get_root().html.add_child(folium.Element(control_html))
    m.get_root().html.add_child(folium.Element(control_js))

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
