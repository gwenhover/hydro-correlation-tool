# Preprocessing

**You do not need to run any of this to run the app.** Everything in here is already committed in `tethysapp/hydro_correlation_tool/public/data/`. Running it regenerates those files from scratch.

The one thing you gain by running part of it is speed: the cache loader notebook pre-fills the retrospective cache so gages load instantly instead of fetching on first click. An empty cache does not mean a broken app, just a slower one. It fills as the app is used. The zarr backfills are the original source for the cache loader notebook, and may be used instead (though time consuming and resource intensive).

All commands run from inside `preprocessing/` with the Tethys environment activated. Paths in the notebooks are relative to this folder.

## Pipeline

Run in this order. Each step's output is the next step's input.

1. **`Hydro_Correlation_Tool_Preproccesing.ipynb`**
   Reads `usgsid_comid_results_2025.csv` + `site_info_active.csv`. Merges them, filters to CONUS, prefixes each id as `USGS-…`.
   Writes `public/data/merged_gages.geojson`.

2. **`GEOGLOWS_River_ID_Seeding.ipynb`**
   Reads `public/data/merged_gages.geojson` + the GEOGLOWS v2 hydrofabric (public S3, no credentials). Snaps each gage to its nearest reach.
   Writes `seed.csv`. See [`GEOGLOWS_Seeding_Explained.md`](GEOGLOWS_Seeding_Explained.md) for the method.

3. **`cache_loader_notebook.ipynb`** - RECOMMENDED
   Reads a public hydroshare resource containing the cached streamflow data as of 8/18/2026
   Writes to the retrospective_cache table, making the app run much faster. Currently has an expected bytes variable, based on the cache export notebook, to check if the download worked properly. 

   OR

   **`aws_zarr_download_nwm.ipynb`** and **`aws_zarr_download_geoglows.ipynb`**
   Read `seed.csv`. Pull 2018–2022 retrospective series from S3 and write them into the `retrospective_cache` table. Both are slow and safe to re-run — they skip reaches already cached.

   AND

   **`retrospective_download_usgs.ipynb`**
   Reads `seed.csv`. Same thing for the USGS side. Needs `usgs_token.txt` in this folder (gitignored).

4. **`rebuild_unreachable_gages.py`**
   Reads `seed.csv`. Asks USGS directly which gages reported discharge in 2018–2022. It never touches the cache.
   Writes `unreachable_gages.rebuilt.csv` (the greyed-out list) and `unreachable_audit.csv` (the evidence). It deliberately does not write into `tethysapp/` — copy the list over `public/data/unreachable_gages.csv` yourself once you've read the audit.

   This step is a script, not a notebook — `python rebuild_unreachable_gages.py`. Budget about 90 minutes, and expect it to use up an hour of USGS quota. If it finishes with gages marked undetermined, rerun it with `--resume` once the quota resets; that only re-asks about those.

   Read the docstring before changing anything in here. The old version defined "unreachable" as "missing from my cache", which quietly condemned 23 gages that had five full years of data. Two rules it explains: never let a fetch error count as "no data", and cross-check the output against non-empty cache rows before shipping — USGS under-reports some gages and the cache is the only thing that catches it.

## Refreshing the shipped crosswalk

`public/data/gage_mapping.csv` is the curated crosswalk — verification statuses, KGE ratings, and any IDs a curator corrected by hand. It's what `syncstores` loads into a fresh database, so it's how someone installing the app inherits our work instead of starting from the raw seed.

It's a snapshot of the portal's `gage_mapping` table, not a live connection. Regenerate it with **`gage_mapping_export.ipynb`**, then update the date in the root README.

Note the two `seeded_*` columns. `nwm_feature_id` is the current answer, `seeded_nwm_feature_id` is what the seeding notebook guessed. Where they differ, a human corrected it. Don't overwrite `seed.csv` with this file — the whole point is to keep the machine guess and the human answer separate.

## Republishing the cache

`cache_export.ipynb` is the other half of step 3. It reads the whole `retrospective_cache` table and writes `hydro_correlation_retrospective_cache_2018_2022.parquet` — every reach, zstd-compressed, about 97 MB. That file is what `cache_loader_notebook.ipynb` downloads, so this is how a newer cache reaches the people installing the app.

It lives on HydroShare as resource [`8c1ebe560d06475e9804ce8107d84142`](https://www.hydroshare.org/resource/8c1ebe560d06475e9804ce8107d84142/). To publish a newer one, replace the file on that resource rather than creating a new one — the loader's URL is hard-coded to that ID. The resource is deliberately shared as **Public, not Published**: HydroShare's "Publish" mints a DOI and makes the resource permanently immutable, which would make replacing the file impossible.

Nothing to update in the loader after re-uploading. It asks HydroShare for the file size instead of holding a constant, and re-downloads whenever the local copy doesn't match, so a republish reaches people on its own. It used to carry an `EXPECTED_BYTES` constant, which went stale on every republish and then failed for everyone with a size error that didn't say "your copy is old".

Two things worth knowing before you re-export: the table is 483 MB of JSONB and roughly 4.35 GB once it's Python objects, so the notebook streams it a chunk at a time and never holds the whole thing — don't "simplify" that into a single `fetchall()`. And the export keeps a `dates` column instead of reconstructing dates from `start_date`, because 1,523 of the 24,893 rows have gaps in their daily series and would be silently corrupted otherwise.

## Migrations

**`update_table.ipynb`** backfills `seeded_nwm_feature_id` and `seeded_geoglows_river_id` from `seed.csv`. Only needed for a database created before those columns existed — `model.py` populates them on a fresh install. It doesn't add the columns, so an older database needs an `ALTER TABLE` first.

## The unreachable list

**`unreachable_audit.csv`** is the evidence behind `unreachable_gages.csv` — one row per seed gage, with the declared record, the batched count, the individual count, first and last observation, the verdict, and which measurement decided it. Keep it. It's what stops the next person re-deriving the list from the cache.

830 gages have no discharge in 2018–2022 and are greyed out in the app. Every one of them was fetched individually to confirm it, and all 830 sit in `retrospective_cache` as empty rows tagged `no_data`, so clicking a greyed gage costs no API call.

Two of them, `USGS-01150900` and `USGS-03331500`, are *not* on the list even though USGS currently returns almost nothing for them — the cache holds 1,826 real days each. That's the under-reporting the script's docstring warns about.

## Known limitations

- **The shipped cache is a frozen snapshot**, taken 2026-08-19. It holds 24,893 reaches (GEOGLOWS 8,309 · NWM 7,471 · USGS 9,113) over 2018–2022. Anything cached after that date isn't in it and will be fetched live until someone republishes. 848 rows carry empty value arrays — 830 are the USGS gages we confirmed have nothing in the window, the rest are reach IDs someone mistyped into the app. They're kept on purpose so they aren't re-requested forever.
- **The backfills key off the seeded IDs**, so the handful of reaches a curator corrected by hand may have no cache entry and will fetch live on first click.
- **Bulk NWM data comes from Zarr, not the API, on purpose.** A 500-call test averaged 5.4s per reach — about 41 hours for a full backfill. The API is still the right tool for single on-demand reaches, which is how the app uses it in `fetchers.py`.

## Inputs

Both come from outside this repo and can't be regenerated by anything here:

- **`usgsid_comid_results_2025.csv`** (`USGSID,COMID`) — Iman's crosswalk. `COMID` is the NWM v3 `feature_id`.
- **`site_info_active.csv`** — a USGS NWIS site service export of active discharge gages (`parm_cd` `00060`).

`seed.csv` stays in this folder and is read by the backfill notebooks in step 3 and by step 4. The cache loader doesn't need it — it keys off the Parquet. `seed.csv` is not loaded by the app.
