# Singapore MRT Commute Analyser

Ranks Singapore MRT stations by expected weekday commute time, using real
passenger tap-in/tap-out volumes from the LTA DataMall API to derive
data-driven destination weights.

The output is an interactive HTML map where each station is labelled with a
score on a red-to-green colour scale.

---

## Quickstart

```
python analyse.py
```

This generates `mrt_commute_scores.html` and opens it automatically in your
browser.

### Options

```
python analyse.py [--display <mode>] [--scope <scope>]
```

| Flag | Choices | Default | Description |
|------|---------|---------|-------------|
| `--display` | `log`, `linear`, `minutes` | `log` | How scores are computed and coloured |
| `--scope` | `residential`, `hdb`, `all` | `residential` | Which stations appear on the map |

**Display modes**

- `log` — 1–10 score using z-scores of log-transformed expected commute time.
  Amplifies differences at the short end of the distribution (recommended).
- `linear` — 1–10 score using z-scores of raw expected commute minutes.
- `minutes` — colours by raw expected commute minutes with no 1–10 scoring.

**Scope**

Controls which stations appear on the map. The underlying commute calculation
always uses the full network as work destinations — scope only filters the
output.

- `residential` — all stations near residential areas (default)
- `hdb` — stations near HDB estates only (subset of residential)
- `all` — every station in the travel-time dataset

All 9 combinations of `--display` and `--scope` are valid.

---

## Methodology

1. **Work destination weights** — weekday morning (07:00–09:59) tap-out
   volumes from the LTA DataMall passenger volume dataset are summed per
   station and normalised to probability weights that sum to 1.0. Stations
   with higher tap-out volumes during this window are treated as more likely
   work destinations.

2. **Expected commute time** — for each residential station, the weighted
   average travel time to all work destinations is computed using a pairwise
   travel-time matrix (scraped from mrt.sg). This gives one "expected commute
   minutes" figure per station.

3. **Scoring** — stations are z-scored within the chosen scope and mapped to
   an integer 1–10 scale. The best station in the scope always scores 10 and
   the worst always scores 1.

4. **Map** — scores are rendered as coloured circles on a Folium/Leaflet map.
   Hovering a station shows its score and expected commute time.

---

## Refreshing caches

Several assets are fetched from external APIs on first run and cached locally
to avoid repeated network calls. Delete the relevant file to force a refresh
on the next run.

| Cache file | Source | When to refresh |
|------------|--------|-----------------|
| `station_coords_cache.json` | LTA DataMall shapefile | New stations open or coordinates change |
| `mrt_lines_cache.geojson` | OpenStreetMap (Overpass API) | MRT network map data has been updated |
| `future_mrt_cache.geojson` | OpenStreetMap (Overpass API) | Construction/proposed lines have changed |

To delete all caches at once:

```bash
rm station_coords_cache.json mrt_lines_cache.geojson future_mrt_cache.geojson
```

---

## Key files

| File | Purpose |
|------|---------|
| `analyse.py` | **Entrypoint.** Parses CLI arguments and calls `build_map()`. |
| `visualize_scores.py` | Fetches station coordinates, computes scores, and writes the HTML map. |
| `data_driven_rankings.py` | Core ranking logic: loads volumes, builds weights, computes expected commute times, and z-scores. |
| `mrt_distance/mrt_distance.py` | Reads `travel_times.csv`; defines residential and HDB station lists. |
| `mrt_volume/explore.py` | Loads and normalises the LTA passenger volume CSV. |
| `mrt_distance/travel_times.csv` | ~20 k rows of pairwise MRT travel times. |
| `mrt_volume/data/station_volumes/transport_node_train_202506.csv` | Hourly tap-in/out volumes (June 2025). |
| `station_coords_cache.json` | Cached WGS84 station coordinates (auto-generated; delete to refresh). |
| `mrt_lines_cache.geojson` | Cached MRT/LRT track geometries from OpenStreetMap (auto-generated; delete to refresh). |
| `future_mrt_cache.geojson` | Cached under-construction/proposed lines from OpenStreetMap (auto-generated; delete to refresh). |
| `mrt_commute_scores.html` | Generated map output (not committed). |
