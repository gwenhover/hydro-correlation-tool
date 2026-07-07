# Hydro Correlation Tool

> **Status: in active development (Week 4 of 10).** This README grows as the app is built — some sections describe features that don't exist yet and are marked accordingly.

A single-user scientific workbench (Tethys Platform app) for building and maintaining a high-quality cross-mapping table that links each active **USGS streamflow gage** to its corresponding **NWM v3 reach** (`feature_id`) and **GEOGLOWS v2 river** (`river_id`).

Because these three datasets use different IDs and geometries for the same rivers, a curated cross-mapping table is needed to connect them. The tool works in two stages:

1. **Offline seeding** — a standalone Python/GeoPandas notebook produces a first-pass mapping (~70% accurate) by spatially matching gages to reaches.
2. **Interactive verification** — a researcher reviews each gage in the web app: inspecting hydrographs, correcting wrong reach assignments (by typing an ID or clicking the correct reach on the map), and marking each record verified. The corrected, human-verified table is the deliverable.

## Tech stack

- **Tethys Platform** (Django-based) with PostgreSQL/PostGIS *(database arrives Week 7)*
- **OpenLayers** for the map (via the Tethys MapView gizmo)
- **MapBox Vector Tiles** for the NWM / GEOGLOWS stream networks
- **Plotly** for hydrographs *(Weeks 5–6)*
- **Bootstrap 5** + jQuery

## Local setup

> Confirm the exact commands for your Tethys version (the flow below is the standard shape).

1. Install Tethys and create/activate its conda environment.
2. From the repo root, install the app in develop mode:
   ```
   tethys install -d
   ```
3. Start the dev server and open the portal:
   ```
   tethys manage start
   ```
   The app lives at `http://localhost:8000/apps/hydro-correlation-tool/`.
4. **Set the MapBox token** (required, or stream tiles fail with 401):
   Portal → **Settings** → the app's **Custom Settings** → paste your MapBox public token into **`MapBox PK Token`** and save.

## Gotchas

- **JS/CSS edits not showing up?** This install serves *collected* static files, so a browser refresh isn't enough — run:
  ```
  tethys manage collectstatic
  ```
  then hard-refresh (Ctrl+Shift+R).
- **Stream tiles blank / 401?** The MapBox token custom setting isn't set (see setup step 4).
- **Projection convention:** all *data* stays in **EPSG:4326** (gage GeoJSON, coordinates); the *map view* runs in **EPSG:3857** (required by the vector tiles). Transform 4326 → 3857 only at the display boundary.

## Data & preprocessing

- The gage layer loads from `tethysapp/hydro_correlation_tool/public/data/merged_gages.geojson` (~9,100 active CONUS gages).
- Regenerate that file with the notebook in [`preprocessing/`](preprocessing/), which merges the USGS gage list with reach IDs, filters to CONUS, and prefixes each id as `USGS-…`.

## Roadmap

| Week | Deliverable | Status |
|------|-------------|--------|
| 1 | Local app + full-screen CONUS map | ✅ Done |
| 2 | Two-pane layout + ~9,000 clickable USGS gages | ✅ Done |
| 3 | NWM stream network (MapBox tiles) + network/basemap toggles | ✅ Done |
| 4 | Click a gage → metadata panel + highlight ring | ✅ Done |
| 5 | Observed USGS daily hydrograph (Plotly) | ⬜ Planned |
| 6 | Full three-series hydrograph (USGS + NWM + GEOGLOWS) + unit/chart toggles | ⬜ Planned |
| 7 | PostGIS database + seeded ~70% mapping, gages colored by status | ⬜ Planned |
| 8 | Edit workflow: type or pick-from-map, Save & Verify, status recolor | ⬜ Planned |
| 9 | Export to CSV, headwater filtering, performance polish | ⬜ Planned |
| 10 | QA, data-validation audit, deployment | ⬜ Planned |

## Not yet implemented

- Persistent database / schema (Week 7)
- Hydrograph data fetchers — USGS `dataretrieval`, NWM BigQuery, GEOGLOWS (Weeks 5–6); will require API keys/endpoints
- Editing, verification, and CSV export (Weeks 8–9)
- Deployment (Week 10)

## Project context

Part of a CIROH / BYU / Aquaveo effort. PI: Dan Ames · Lead architect: Sudip · Implementation: Gwen Hover.
