# Understanding the GEOGLOWS Seeding Notebook

*A study guide for `GEOGLOWS_River_ID_Seeding.ipynb`. Read it with the
notebook open in a second tab, and run the exercises — reading about
geospatial code teaches you vocabulary; running it teaches you the subject.*

---

## 1. The problem, in one sentence

Every USGS gage is a **point** sitting on a river. GEOGLOWS represents every
river as a **line** with an ID (`LINKNO`). The notebook answers, for 9,113
points: *which line is this point sitting on?* — by finding the nearest line
within 500 meters and copying its ID onto the gage.

That's the whole algorithm. Everything else in the notebook is either
(a) getting the data, (b) making "nearest" and "500 meters" mean what you
think they mean, or (c) checking the answers.

---

## 2. Vector data and GeoDataFrames

Geospatial "vector" data comes in three shapes: **points** (gages), **lines**
(rivers), and **polygons** (VPU regions, lakes, states). A file like
`merged_gages.geojson` or `streams_712.gpkg` is essentially a spreadsheet
where one column holds a shape.

GeoPandas makes that literal: a `GeoDataFrame` **is** a pandas DataFrame with
one special `geometry` column, plus a `.crs` property saying what the
coordinates mean. Every pandas skill you have (filtering, merging,
`value_counts`) works unchanged; GeoPandas adds spatial abilities on top
(`.to_crs()`, `sjoin`, `.total_bounds`, buffering, distance).

File formats you touched:

- **GeoJSON** (`merged_gages.geojson`) — plain text, human-readable, fine for
  ~10k points, slow and bloated for millions of features.
- **GeoPackage / .gpkg** (`streams_712.gpkg`) — a SQLite database with
  geometry inside. Binary, indexed, made for big data. The spatial index it
  carries is *why* a bounding-box read is fast (section 5).
- **Parquet** — columnar tables (GEOGLOWS's metadata tables); fast, no
  geometry required.

---

## 3. Coordinate Reference Systems — the concept that caused two of our three bugs

A CRS answers: *what do the numbers in the coordinates mean?* The same spot
on the Colorado River is:

| CRS | Coordinates | Units | Used for |
|---|---|---|---|
| **EPSG:4326** (WGS84) | `(-112.1, 36.1)` | degrees lon/lat | GPS, GeoJSON, "the interchange format" |
| **EPSG:3857** (Web Mercator) | `(-12,479,000, 4,311,000)` | meters-ish | web map *tiles* (your OpenLayers view!) |
| **EPSG:5070** (Albers CONUS) | `(-1,235,000, 1,568,000)` | real meters | measuring distance/area across CONUS |

Three rules worth internalizing:

1. **Never measure distance in degrees.** A degree of longitude is ~111 km at
   the equator but ~78 km in Montana — so "within 0.005°" means different
   real distances for a Texas gage and a Minnesota gage. That's why the
   notebook converts everything to EPSG:5070 (equal-area, meters, designed
   for CONUS) *before* `sjoin_nearest`, and why the plan document says
   "do not buffer in degrees."
2. **Every geometry operation between two layers requires the same CRS.**
   GeoPandas errors if you join layers with mismatched CRS — a *helpful*
   crash. The sneaky failures are the ones where nothing checks (rule 3).
3. **Coordinates are just numbers — nothing stops you using them in the wrong
   CRS.** Our first bug: I passed a bounding box in degrees
   (`-125 … -66`) to filter a file whose coordinates are in meters. To the
   file, "-125 to -66" is a 60-meter sliver near the origin — off the coast
   of West Africa. Zero streams matched, and the *symptom* (a crash in a
   later join) appeared far from the *cause*. This is THE classic geospatial
   bug. When spatial code selects nothing or everything, check the CRS first.

Aside: your app already lives this split — my map-projection note for the
webapp is "view in 3857 (tiles demand it), data in 4326, transform at the
display boundary." Same discipline, different codebase.

---

## 4. Cell-by-cell walkthrough

### Config cell

Three numbers deserve understanding, not acceptance:

- `CRS_METERS = "EPSG:5070"` — see section 3.
- `MAX_SNAP_M = 500` — the leash. A gage with no reach within 500 m gets
  **no match** (`NaN`) instead of a far-away wrong match. Nulls are honest;
  confident wrong answers are poison for a verification workflow. The plan
  said "start 100–500 m, tune empirically" — the QA histogram (section 4,
  QA cell) is the empirical part.
- `MIN_STREAM_ORDER = 2` — **Strahler stream order**: headwater creeks are
  order 1; two order-1s meeting make an order 2; two 2s make a 3, and so on
  (a 1 joining a 2 stays 2 — order only increments when equals meet). The
  Mississippi is ~10. Gages sit on rivers people care about, rarely order-1
  trickles, and a big-river gage placed slightly off-line could otherwise
  snap to a tiny tributary entering nearby. Filtering order-1 reaches removes
  that trap. *Trade-off:* a genuine order-1 gage now gets null instead of a
  match — acceptable, because a human verifies every record anyway.

### Cell: "Which VPUs cover CONUS?"

GEOGLOWS splits the world into 125 **VPUs** (Vector Processing Units —
hydrology's term for "regional chunk"). Rivers don't respect state lines, so
the chunks follow drainage basins. CONUS turned out to be VPUs 701–715
(the 7xx block is North America).

Two techniques in this cell:

- **`/vsicurl/`** — a GDAL trick that reads a *remote* file over HTTP as if
  local, fetching only the byte ranges it needs. The boundaries file is
  1.9 GB, but because a GeoPackage carries a spatial index, "give me
  polygons intersecting CONUS" downloads only a few MB. Free lesson: cloud
  geodata is often queryable in place; check before downloading.
- **Spatial join** (`gpd.sjoin`, `predicate="within"`) — the spatial version
  of a SQL join: instead of matching key columns, it matches *geometric
  relationships* ("point within polygon"). Result: each gage row gains the
  VPU code of the polygon containing it.

The stray-gage fallback exists because 93 coastal/island gages fall *just*
outside every VPU polygon (drawn along coastlines, and points on the boundary
line don't count as "within"). For them, `sjoin_nearest` against the polygons
assigns the closest region instead of dropping them.

### Cell: downloads

Nothing spatial — just "don't re-download 5 GB on re-runs." The
`if dest.exists(): continue` pattern is worth stealing for any pipeline
with big inputs.

### Cell: the snap loop (the heart)

Per VPU, four moves:

1. **Bounding-box read.** `read_file(path, bbox=...)` asks the GeoPackage's
   spatial index for features in a rectangle, instead of loading the whole
   file. Note `file_crs = pyogrio.read_info(...)["crs"]` and the conversion
   of the gage bounds into *the file's* CRS first — that line **is** the fix
   for bug #1, rule 3 in the flesh.
2. **Order filter** — drop order-1 reaches (see config).
3. **Reproject both layers to EPSG:5070** — so the next step measures real
   meters.
4. **`sjoin_nearest(..., max_distance=500, distance_col="geoglows_snap_m")`**
   — for each gage, find the nearest stream line. Naively that's 9,113 gages
   × millions of line segments of distance checks; an **R-tree spatial
   index** (built automatically) makes it fast by pruning to nearby
   candidates first — like a phone book's alphabetical order, but for space.
   The `distance_col` records how far each gage snapped: your per-match
   confidence score, for free.

### Cell: combine and dedupe

`sort_values("geoglows_snap_m").drop_duplicates("USGSID", keep="first")` —
if a gage somehow matched twice (ties, overlapping VPU margins), sort by
snap distance and keep the closest. Then a plain pandas `merge` staples the
results onto the full gage table — gages that matched nothing keep `NaN`,
deliberately.

### QA cell — the researcher's cell

The histogram is the tuning instrument for `MAX_SNAP_M`. Expected shape: a
big spike near 0–30 m (gages genuinely on their line) and a thin tail. Where
the spike ends and the trickle begins tells you what "definitely right"
looks like in this dataset; the worst-20 table is your review list, and your
app is now literally the tool for checking them — click the gage, compare
observed vs GEOGLOWS hydrographs, and judge.

Your actual results, for reference: 8,491/9,113 matched (93%), median snap
**20 m**, 75th percentile 43 m. That's a healthier seed than the plan's ~70%
expectation.

### Save cell

Reshapes to the exact Week 7 database schema from the plan (§5.3):
`usgs_id, gage_name, latitude, longitude, nwm_feature_id,
geoglows_river_id, verification_status`. The NWM column comes from Iman's
COMIDs (already in `merged_gages.geojson`); the GEOGLOWS column is what this
notebook computed; every row starts `Unverified` because no human has looked
yet — that's the app's job.

---

## 5. The three bugs as case studies

Debugging *is* the curriculum. Each bug below is a general lesson wearing a
specific costume:

1. **Degrees vs meters bbox** → *coordinates carry no units; you carry the
   units.* Symptom appeared 30 lines from the cause. First reflex when
   spatial code returns nothing/everything: print both layers' `.crs` and
   `.total_bounds` and look at the magnitudes — degrees are ±180, Web
   Mercator meters are millions.
2. **`"701" == 701` is False** → *data types are part of correctness, not
   pedantry.* The VPU codes looked like numbers, printed like numbers, and
   were strings. The comparison silently produced an empty selection — no
   error, just nothing. Same disease as USGS IDs losing leading zeros when
   read as integers (why your schema stores them as strings!).
   `df.dtypes` is the two-second check.
3. **Stale editor buffer** → *the file on disk and the file in your editor
   are two copies.* The notebook was fixed on disk while VS Code held the old
   version in memory. Not geospatial at all — but "which version am I
   actually running?" is a question worth asking whenever a fix mysteriously
   doesn't take (same energy as your collectstatic ritual).

---

## 6. Exercises (do these — 30–45 minutes total)

Run each in a fresh cell at the bottom of the notebook (fresh kernel: run the
config/load cells first). Answers aren't given — the QA cell and a map are
your answer key.

1. **Verify one gage by hand.** Pick a gage you know from `seed`. Find its
   row, note its `geoglows_river_id` and `geoglows_snap_m`. Then click that
   same gage's neighborhood in your app with the GEOGLOWS network showing —
   does the assigned reach look right?
2. **Feel a CRS.** Take `gages.head(3)`, print `.geometry`, then
   `.to_crs("EPSG:5070").geometry`, then `.to_crs("EPSG:3857").geometry`.
   Same gages, three number systems. Which pair of numbers could you have
   mistaken for the other, and what would break?
3. **Tune the leash.** Re-run the snap loop with `MAX_SNAP_M = 100`, then
   `1000`. How many gages match under each? Which regime produces more *wrong
   but confident* matches, and why is that worse than nulls here?
4. **Question the order filter.** Count how many reaches VPU 712 loses to
   `strmOrder >= 2` (read the file, `value_counts` on `strmOrder`). Then find
   the unmatched gages whose names contain "CREEK" — how many might be
   real order-1 gages the filter sacrificed?
5. **Break it on purpose** (best one). In a scratch cell, redo one VPU's
   `sjoin_nearest` but "forget" the `.to_crs(CRS_METERS)` on one side.
   Read the error. Then instead pass the bbox in 4326 like my bug did and
   watch it select zero streams. You'll never forget rule 3 after producing
   the bug yourself.

---

## 7. Vocabulary you now own

**CRS / EPSG code** · numbered dialect for coordinates (4326°, 3857 web-m,
5070 CONUS-m) — **reproject / `to_crs`** · convert between them —
**GeoDataFrame** · DataFrame + geometry column + crs — **spatial join** ·
join on geometric relationship, not key equality — **`sjoin_nearest`** ·
spatial join by proximity, optional `max_distance` leash — **spatial index /
R-tree** · what makes "nearest among millions" fast — **bbox read** · load
only features in a rectangle — **`/vsicurl/`** · GDAL's read-remote-files-
partially trick — **VPU** · GEOGLOWS's regional chunk (CONUS = 701–715) —
**LINKNO** · GEOGLOWS v2 reach ID (= `river_id`) — **Strahler order** ·
stream size rank; 1 = headwater creek — **snap distance** · gage-to-matched-
line distance; small = confident.

Further reading, in order of payoff: GeoPandas user guide sections on
*Projections* and *Merging data* (docs.geopandas.org) · your plan document
§6 (re-read it now — it should feel obvious) · the "Finding River Numbers"
tutorial at data.geoglows.org (how GEOGLOWS themselves frame river IDs).
