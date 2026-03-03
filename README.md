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
                  [--weight-method <method>] [--from <hour>] [--to <hour>]
```

| Flag | Choices / type | Default | Description |
|------|---------------|---------|-------------|
| `--display` | `log`, `linear`, `minutes` | `log` | How scores are computed and coloured |
| `--scope` | `residential`, `hdb`, `all` | `residential` | Which stations appear on the map |
| `--weight-method` | `destinations`, `morning_peak`, `all_hours_weighted` | `destinations` | How destination weights are derived |
| `--from` | integer (0–23) | `7` | Start hour of the tap-out window (for `destinations` only) |
| `--to` | integer (0–23) | `19` | Exclusive end hour of the window (for `destinations` only) |

**Display modes**

- `log` — 1–10 score using z-scores of log-transformed expected commute time.
  Amplifies differences at the short end of the distribution (recommended).
- `linear` — 1–10 score using z-scores of raw expected commute minutes.
- `minutes` — colours by raw expected commute minutes with no 1–10 scoring.
- `weights` — destination weights map (writes `mrt_destination_weights.html`
  instead of the usual commute scores map; see below).

**Scope**

Controls which stations appear on the map. The underlying commute calculation
always uses the full network as work destinations — scope only filters the
output.

- `residential` — all stations near residential areas (default)
- `hdb` — stations near HDB estates only (subset of residential)
- `all` — every station in the travel-time dataset

All 9 combinations of `--display` and `--scope` are valid.

**Weight methods**

Controls how each destination station is weighted in the expected-commute
calculation. Each method produces a different view of "where people go".

- `destinations` (alias: `real_world_commuter_destinations`) — combines
  weekday tap-outs with weekend/public-holiday tap-outs in a **5:2 ratio**,
  using a configurable time window (default **07:00–18:59**).  Each component
  is normalised to 1.0 independently before combining, so the ratio reflects
  relative importance rather than raw volume.  The window is tuned to capture
  outbound trips — commuting, shopping, errands — while excluding the evening
  "going home" flow.  **(default)**

  The window can be adjusted with `--from` and `--to`:

  ```
  python analyse.py --from 7 --to 19     # default — full daytime
  python analyse.py --from 7 --to 10     # restrict to morning rush only
  python analyse.py --from 8 --to 22     # extend into the evening
  ```

- `morning_peak` — uses only weekday morning (7–10 am) tap-out volumes.
  Focuses purely on work destinations; ignores weekend activity entirely.
- `all_hours_weighted` — sums tap-outs across all hours and both day types,
  weighted by the effective number of weekday and non-weekday days in the
  month.  Reflects overall station throughput including evening and mixed-use
  traffic.

### Destination weights map

```
python analyse.py --display weights [--scope <scope>]
                  [--weight-method <method>] [--from <hour>] [--to <hour>]
```

Generates `mrt_destination_weights.html` — a standalone map showing how much
each station contributes to the commute-score centroid.

- Each circle label shows the station's **actual percentage share** of total
  network tap-out volume (e.g. `"2.3%"`).
- Colour is scaled relative to the **highest-weighted station** (= 100 on an
  internal 1–100 scale), using the same red→yellow→green gradient as the
  commute score map.  The heaviest destination always appears green; lighter
  destinations grade toward red.
- Stations are grouped into **10 bands** by relative weight (band 10 = top 10%
  of the 1–100 scale; band 1 = bottom 10%) for optional filter control.
- All `--weight-method`, `--from`, and `--to` flags apply as for the commute
  score map.
- The legend shows the actual weight% range of stations in each band.

### Inspecting destination weights

```
python analyse.py --weights [--top N] [--bottom N]
                  [--weight-method <method>] [--from <hour>] [--to <hour>]
```

Prints the normalised tap-out weights that form the scoring centroid — i.e.
how much each station contributes to the "expected commute" calculation,
with a running cumulative total. `--display` and `--scope` are ignored.

| Flag | Description |
|------|-------------|
| `--weights` | Enter weights mode (print all stations, descending) |
| `--top N` | Show only the N highest-weighted stations (descending) |
| `--bottom N` | Show only the N lowest-weighted stations (ascending) |
| `--weight-method` | Which weight method to inspect (default: `destinations`) |
| `--from` / `--to` | Time window for `destinations` weighting (default: 7 / 19) |

`--top` and `--bottom` can be combined to print both ends in one run.
Either flag also implies `--weights`, so `--weights` itself can be omitted.

---

## Methodology

1. **Work destination weights** — tap-out volumes from the LTA DataMall
   passenger volume dataset are aggregated per station using the selected
   weight method (default: `destinations`) and normalised to probability
   weights that sum to 1.0. Stations with higher tap-out volumes are treated
   as more likely destinations.

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
| `mrt_commute_scores.html` | Generated commute score map (not committed). |
| `mrt_destination_weights.html` | Generated destination weights map (not committed). |
