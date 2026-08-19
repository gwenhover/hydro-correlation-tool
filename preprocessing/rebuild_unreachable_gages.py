"""Rebuild unreachable_gages.csv from USGS directly, with no reliance on the cache.

Why this exists
---------------
The previous list came from `build_unreachable_gages.ipynb`, which defined
"unreachable" as *absent from the local retrospective_cache*. That conflates
three different things -- never fetched, fetch failed, genuinely empty -- and it
condemned 22 gages that do have scoreable 2018-2022 discharge. A list that says
"no data" must be built from USGS saying "no data", never from our DB not
knowing.

Method
------
Universe is every gage in seed.csv. The cache is not consulted at any point.

  Screen         get_daily over exactly the model window -> observations per
                 gage inside it. Fast, but batched, and batching is what failed
                 last time, so this only ever nominates gages; it never condemns.

  Determinism    The screen runs twice at different chunk sizes. Chunk size is
                 an implementation detail of our loop and must not change the
                 answer; if it does, the fetch is losing rows and the run aborts
                 rather than shipping a list.

  Adjudication   Every gage the screen reports as zero is re-fetched ONE AT A
                 TIME over a WIDE window, and that answer decides. It differs
                 from the screen in both batching and window, so it is a genuinely
                 independent measurement, and it yields the gage's real first/last
                 for the audit trail. Row loss can only ever delete observations,
                 never invent them, so re-verifying the zero set covers the entire
                 dangerous direction: every gage on the shipped list has been
                 asked about by itself.

Known hole: the individual fetch can under-report too
-----------------------------------------------------
Adjudicating a gage alone reduces exposure to the multi-series problem below but
does not eliminate it. On 2026-08-19 this script condemned USGS-01150900
(Ottauquechee R, VT) and USGS-03331500 (Tippecanoe R nr Ora, IN) -- both
long-established stream gages -- because every window it asked for returned only
33 days starting July 2026. The existing retrospective_cache held 1,826 complete
days for each, at magnitudes consistent with their drainage areas.

So: cross-check the output against non-empty retrospective_cache rows before
shipping. The cache can RESCUE a gage but must never condemn one -- 1,826 days of
plausible discharge cannot be invented, while absence from the cache proves
nothing (that conflation is the bug this script exists to undo).

Do NOT reintroduce get_time_series_metadata as an authority here
---------------------------------------------------------------
It reports only a gage's CURRENT time series. For gages whose series was
re-registered it under-reports the record catastrophically -- it puts
USGS-05570000's record as beginning 2025-12-08 when the gage has 13,149 daily
values back to 1990. The stale list's row for that gage started on exactly that
date, which is how the original run went wrong. Declared metadata is recorded in
the audit file for information only and is never allowed to condemn a gage.

Outputs
-------
  unreachable_gages.rebuilt.csv       the list: one 'gages' column, same shape
                                      as the file the app loads
  unreachable_audit.csv               per-gage evidence for all 9k gages
                                      (declared span, batch count, individual
                                      count, verdict and why)

Nothing is written into tethysapp/. Copying the rebuilt list over the shipped
one is a separate, deliberate step.

Run from preprocessing/:  python rebuild_unreachable_gages.py
"""

from __future__ import annotations

import os
import sys
import time

import pandas as pd
from dataretrieval import waterdata

WINDOW_START = "2018-01-01"
WINDOW_END = "2022-12-31"

# Window for the individual adjudicating fetch. Deliberately wider than the model
# window so a gage's whole record is visible in the audit trail.
WIDE_START = "1900-01-01"
WIDE_END = "2026-12-31"

PARAM = "00060"          # discharge
STAT = "00003"           # daily mean -- must match fetchers.get_usgs_daily_discharge
PROPS = ["monitoring_location_id", "time", "value"]

META_CHUNK = 1000        # gages per metadata request
PASS_A_CHUNK = 400       # gages per observation request, pass 1
PASS_B_CHUNK = 250       # ...and pass 2, deliberately different

# Individual adjudication fires one request per gage and will trip the USGS rate
# limiter without pacing. A 429 is not an answer, so we slow down and retry
# rather than let it become a verdict.
THROTTLE_SECONDS = 0.35  # gap between individual requests
RETRY_ATTEMPTS = 6
RETRY_BASE_SECONDS = 5

SEED_CSV = "seed.csv"
OUT_LIST = "unreachable_gages.rebuilt.csv"
OUT_AUDIT = "unreachable_audit.csv"

# Prefer a token already in the environment (lets you run against a different
# key without editing or overwriting usgs_token.txt); fall back to the file.
if not os.environ.get("API_USGS_PAT"):
    os.environ["API_USGS_PAT"] = open("usgs_token.txt").read().strip()


def log(msg: str) -> None:
    print(msg, flush=True)


def declared_spans(gage_ids: list[str]) -> pd.DataFrame:
    """Declared 00060/00003 period of record. INFORMATIONAL ONLY.

    A gage may expose several daily-mean discharge series (sublocations), so the
    declared span is the union: earliest begin to latest end.
    """
    frames = []
    for i in range(0, len(gage_ids), META_CHUNK):
        chunk = gage_ids[i:i + META_CHUNK]
        meta, _ = waterdata.get_time_series_metadata(
            monitoring_location_id=chunk,
            parameter_code=PARAM,
            statistic_id=STAT,
            skip_geometry=True,
        )
        log(f"  metadata chunk {i // META_CHUNK + 1}: "
            f"{len(chunk)} gages -> {len(meta)} series")
        if len(meta):
            frames.append(meta[["monitoring_location_id", "begin", "end"]])

    if not frames:
        return pd.DataFrame(columns=["declared_begin", "declared_end"])

    meta = pd.concat(frames, ignore_index=True)
    meta["begin"] = pd.to_datetime(meta["begin"]).dt.tz_localize(None)
    meta["end"] = pd.to_datetime(meta["end"]).dt.tz_localize(None)
    return (
        meta.groupby("monitoring_location_id")
        .agg(declared_begin=("begin", "min"), declared_end=("end", "max"))
    )


def observed_counts(gage_ids: list[str], chunk_size: int, label: str) -> pd.Series:
    """Screen: in-window daily-mean discharge observations per gage.

    Aggregates chunk by chunk and drops each frame immediately -- the full window
    across 9k gages is tens of millions of rows and does not need to be resident.
    """
    counts: dict[str, int] = {}
    n_chunks = (len(gage_ids) + chunk_size - 1) // chunk_size
    t_start = time.perf_counter()

    for i in range(0, len(gage_ids), chunk_size):
        chunk = gage_ids[i:i + chunk_size]
        df, _ = waterdata.get_daily(
            monitoring_location_id=chunk,
            parameter_code=PARAM,
            statistic_id=STAT,
            time=(WINDOW_START, WINDOW_END),
            skip_geometry=True,
            properties=PROPS,
        )
        if len(df):
            df = df.dropna(subset=["value"])
            # Guard against the API widening the window on us; the count is only
            # meaningful if every row really is inside it.
            df = df[df["time"].between(WINDOW_START, WINDOW_END)]
            for gage_id, n in df.groupby("monitoring_location_id").size().items():
                counts[gage_id] = counts.get(gage_id, 0) + int(n)

        # A returned gage that was never requested means the id mapping is wrong.
        stray = set(counts) - set(gage_ids)
        if stray:
            sys.exit(f"ABORT: response contained unrequested gages: {sorted(stray)[:5]}")

        log(f"  [{label}] chunk {i // chunk_size + 1}/{n_chunks} "
            f"({len(chunk)} gages) -- {len(counts)} gages with data so far, "
            f"{time.perf_counter() - t_start:.0f}s")

    return pd.Series(counts, dtype="int64").reindex(gage_ids).fillna(0).astype("int64")


def adjudicate(gage_id: str) -> dict:
    """Decide ONE gage on its own, over the full record.

    Fetched alone (no chunk-mates to interfere) and over a wide window (so a
    window-edge or multi-series effect cannot hide the record the way it did in
    the run that produced the stale list). Returns the in-window count that
    decides the verdict, plus the surrounding evidence for the audit file.

    Retries on 429. Firing ~800 individual requests trips the USGS rate limiter,
    and a rate-limited request tells us NOTHING about the gage -- treating it as
    an answer is the original bug. Backoff until the window rolls over, or raise
    and let the caller record the gage as undetermined.
    """
    delay = RETRY_BASE_SECONDS
    for attempt in range(RETRY_ATTEMPTS):
        try:
            df, _ = waterdata.get_daily(
                monitoring_location_id=gage_id,
                parameter_code=PARAM,
                statistic_id=STAT,
                time=(WIDE_START, WIDE_END),
                skip_geometry=True,
                properties=PROPS,
            )
            break
        except Exception as exc:
            if "429" not in repr(exc) or attempt == RETRY_ATTEMPTS - 1:
                raise
            time.sleep(delay)
            delay *= 2
    if len(df) == 0:
        return {"in_window": 0, "total_days": 0, "first": pd.NaT, "last": pd.NaT}
    df = df.dropna(subset=["value"])
    if len(df) == 0:
        return {"in_window": 0, "total_days": 0, "first": pd.NaT, "last": pd.NaT}
    return {
        "in_window": int(df["time"].between(WINDOW_START, WINDOW_END).sum()),
        "total_days": len(df),
        "first": df["time"].min(),
        "last": df["time"].max(),
    }


def main() -> None:
    seed = pd.read_csv(SEED_CSV)
    gage_ids = sorted(seed["usgs_id"].dropna().astype(str).unique())
    log(f"seed gages: {len(gage_ids)}   window: {WINDOW_START} .. {WINDOW_END}\n")

    log("declared metadata (informational only -- never condemns)")
    declared = declared_spans(gage_ids)
    log(f"  {len(declared)} of {len(gage_ids)} gages publish a {PARAM}/{STAT} series\n")

    log("screen pass 1 -- observed in-window counts")
    pass_a = observed_counts(gage_ids, PASS_A_CHUNK, "pass 1")
    log("")
    log("screen pass 2 -- same question, different chunk size")
    pass_b = observed_counts(gage_ids, PASS_B_CHUNK, "pass 2")
    log("")

    # Chunk size is an implementation detail of OUR loop. If it changes the
    # answer, the transport is losing rows and no output from this run is safe.
    disagree = pass_a.index[pass_a != pass_b]
    if len(disagree):
        sample = pd.DataFrame({"pass_1": pass_a[disagree], "pass_2": pass_b[disagree]})
        sys.exit(
            f"ABORT: {len(disagree)} gages disagree between chunk sizes "
            f"{PASS_A_CHUNK} and {PASS_B_CHUNK}. The batch fetch is dropping "
            f"rows -- this is exactly the failure that produced the bad list.\n"
            f"{sample.head(20).to_string()}"
        )
    log(f"determinism check PASSED: identical counts at chunk {PASS_A_CHUNK} "
        f"and {PASS_B_CHUNK} for all {len(gage_ids)} gages\n")

    audit = pd.DataFrame({"usgs_id": gage_ids}).set_index("usgs_id")
    audit = audit.join(declared)          # informational only -- see module docstring
    audit["screen_in_window"] = pass_a

    # Adjudicate, individually, every gage the screen wants to condemn.
    zero_set = audit.index[audit["screen_in_window"] == 0].tolist()
    log(f"adjudicating {len(zero_set)} zero-count gages individually "
        f"over {WIDE_START}..{WIDE_END} (this is the slow part)")
    rows: dict[str, dict] = {}
    errors: list[str] = []
    t0 = time.perf_counter()
    for n, gage_id in enumerate(zero_set, 1):
        time.sleep(THROTTLE_SECONDS)
        try:
            rows[gage_id] = adjudicate(gage_id)
        except Exception as exc:
            # An error is NOT evidence of no data. Conflating the two is the
            # entire bug this script exists to undo, so an unresolved gage is
            # recorded as undetermined and kept OFF the list.
            errors.append(gage_id)
            rows[gage_id] = {"in_window": -1, "total_days": -1,
                             "first": pd.NaT, "last": pd.NaT}
            log(f"  ! {gage_id} errored: {exc!r}")
        if n % 50 == 0 or n == len(zero_set):
            rate = (time.perf_counter() - t0) / n
            log(f"  {n}/{len(zero_set)}  ({rate:.2f}s/gage, "
                f"~{rate * (len(zero_set) - n) / 60:.1f} min left)")

    ind = pd.DataFrame.from_dict(rows, orient="index")
    ind.columns = ["individual_in_window", "individual_total_days",
                   "individual_first", "individual_last"]
    audit = audit.join(ind)

    # Verdict source: the individual measurement wherever one was taken, the
    # screen otherwise (row loss can delete observations, never invent them, so
    # a positive screen result needs no second opinion).
    audit["in_window_days"] = (
        audit["individual_in_window"].fillna(audit["screen_in_window"]).astype(int)
    )

    errored = audit.index[audit["in_window_days"] < 0]
    unreachable = audit.index[audit["in_window_days"] == 0]
    rescued = audit.index[(audit["screen_in_window"] == 0)
                          & (audit["in_window_days"] > 0)]

    audit["verdict"] = "reachable"
    audit.loc[unreachable, "verdict"] = "unreachable"
    audit.loc[errored, "verdict"] = "undetermined (fetch error)"
    audit["decided_by"] = "screen"
    audit.loc[zero_set, "decided_by"] = "individual fetch"

    audit.reset_index().to_csv(OUT_AUDIT, index=False)
    pd.DataFrame({"gages": sorted(unreachable)}).to_csv(OUT_LIST, index=False)

    scoreable = int((audit["in_window_days"] >= 365).sum())
    thin = audit.index[(audit["in_window_days"] > 0) & (audit["in_window_days"] < 365)]
    has_record_but_not_in_window = int(
        (audit.loc[unreachable, "individual_total_days"] > 0).sum()
    )

    log(f"\n{'=' * 62}")
    log(f"seed gages:                        {len(gage_ids)}")
    log(f"  reachable (>=1 in-window day):   {int((audit['verdict'] == 'reachable').sum())}")
    log(f"    of those, >=365 days:          {scoreable}")
    log(f"    of those, 1-364 days (thin):   {len(thin)}")
    log(f"  unreachable (0 in-window days):  {len(unreachable)}")
    log(f"  undetermined (fetch error):      {len(errored)}  <- kept OFF the list")
    log(f"\n  rescued by individual fetch:     {len(rescued)}  <- the screen was wrong "
        f"about these; the old list's failure mode")
    if len(rescued):
        log("    " + ", ".join(sorted(rescued)[:30]))
    log(f"\nwithin the {len(unreachable)} unreachable:")
    log(f"  have {PARAM}/{STAT} data outside the window:  {has_record_but_not_in_window}")
    log(f"  no {PARAM}/{STAT} data anywhere, ever:        "
        f"{len(unreachable) - has_record_but_not_in_window}")
    log(f"\nwrote {OUT_LIST} ({len(unreachable)} gages)")
    log(f"wrote {OUT_AUDIT} ({len(audit)} rows)")
    log("cache untouched; tethysapp/ untouched.")


def resume() -> None:
    """Re-adjudicate only the gages a previous run left undetermined.

    A 429 storm during adjudication leaves gages with no verdict. They are held
    off the list, which is safe but incomplete -- shipping that list would
    un-grey a few hundred gages on no evidence. This re-asks about exactly those
    gages, paced, and rewrites both outputs. The screen results and every
    already-decided verdict are reused unchanged.
    """
    audit = pd.read_csv(OUT_AUDIT).set_index("usgs_id")
    pending = audit.index[audit["verdict"].str.startswith("undetermined")].tolist()
    log(f"resuming: {len(pending)} undetermined gages, "
        f"{THROTTLE_SECONDS}s pacing, {RETRY_ATTEMPTS} retries on 429\n")

    still_failing = []
    t0 = time.perf_counter()
    for n, gage_id in enumerate(pending, 1):
        time.sleep(THROTTLE_SECONDS)
        try:
            r = adjudicate(gage_id)
            audit.loc[gage_id, "individual_in_window"] = r["in_window"]
            audit.loc[gage_id, "individual_total_days"] = r["total_days"]
            audit.loc[gage_id, "individual_first"] = r["first"]
            audit.loc[gage_id, "individual_last"] = r["last"]
        except Exception as exc:
            still_failing.append(gage_id)
            log(f"  ! {gage_id} still failing: {exc!r}")
        if n % 50 == 0 or n == len(pending):
            rate = (time.perf_counter() - t0) / n
            log(f"  {n}/{len(pending)}  ({rate:.2f}s/gage, "
                f"~{rate * (len(pending) - n) / 60:.1f} min left)")

    audit["in_window_days"] = (
        audit["individual_in_window"].fillna(audit["screen_in_window"]).astype(int)
    )
    errored = audit.index[audit["in_window_days"] < 0]
    unreachable = audit.index[audit["in_window_days"] == 0]

    audit["verdict"] = "reachable"
    audit.loc[unreachable, "verdict"] = "unreachable"
    audit.loc[errored, "verdict"] = "undetermined (fetch error)"

    audit.reset_index().to_csv(OUT_AUDIT, index=False)
    pd.DataFrame({"gages": sorted(unreachable)}).to_csv(OUT_LIST, index=False)

    log(f"\n{'=' * 62}")
    log(f"resolved this pass:            {len(pending) - len(still_failing)}")
    log(f"still undetermined:            {len(still_failing)}  <- kept OFF the list")
    log(f"unreachable (final):           {len(unreachable)}")
    log(f"reachable (final):             {int((audit['verdict'] == 'reachable').sum())}")
    log(f"\nrewrote {OUT_LIST} and {OUT_AUDIT}")
    if still_failing:
        log("\nNOT SHIPPABLE until these resolve -- rerun with --resume:")
        log("  " + ", ".join(still_failing[:20]))


if __name__ == "__main__":
    if "--resume" in sys.argv:
        resume()
    else:
        main()
