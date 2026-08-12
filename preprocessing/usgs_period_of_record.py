"""Measure the period of record for seed gages that have no USGS cache row.

Read-only with respect to the cache: it queries retrospective_cache to find
which gages are missing, then asks the USGS API for a wide window and reports
each gage's first/last observation. It NEVER writes to retrospective_cache —
mixing windows into a date-blind cache key is the thing we're avoiding.

Output: usgs_period_of_record.csv + a printed summary of which candidate
windows would recover how many gages.

Run from preprocessing/:  python usgs_period_of_record.py
"""
import os
import time as _time

import pandas as pd
from sqlalchemy import create_engine
from dataretrieval import waterdata

WIDE_START = "1990-01-01"
WIDE_END = "2025-12-31"
BATCH_SIZE = 2000

# A gage is only usable if it overlaps the model window enough to score a KGE.
# Candidate windows to evaluate, plus the minimum overlapping days to count.
CANDIDATE_WINDOWS = [
    ("2018-01-01", "2022-12-31"),   # current
    ("2015-01-01", "2022-12-31"),
    ("2010-01-01", "2022-12-31"),
    ("2005-01-01", "2022-12-31"),
    ("2000-01-01", "2022-12-31"),
    ("1990-01-01", "2022-12-31"),
]
MIN_OVERLAP_DAYS = 365

engine = create_engine(
    "postgresql://tethys_super:pass@localhost:5436/hydro_correlation_tool_primary_db"
)

os.environ["API_USGS_PAT"] = open("usgs_token.txt").read().strip()


def find_missing():
    """Seed gages with no USGS cache row, as prefixed API-ready strings."""
    with engine.connect() as conn:
        cached = {
            r[0] for r in conn.execute(
                "SELECT reach_id FROM retrospective_cache WHERE network = 'USGS'"
            ).fetchall()
        }
    seed = set(pd.read_csv("seed.csv")["usgs_id"].dropna().astype(str))
    # cached holds ints (leading zeros already lost); keep the original padded
    # string for the API and derive the int only for the membership test.
    return sorted(s for s in seed if int(s.removeprefix("USGS-")) not in cached)


def fetch_record(gage_ids):
    """Return one row per gage that has any 00060 daily value in the wide window."""
    rows = []
    for i in range(0, len(gage_ids), BATCH_SIZE):
        batch = gage_ids[i:i + BATCH_SIZE]
        print(f"batch {i // BATCH_SIZE + 1}/"
              f"{(len(gage_ids) + BATCH_SIZE - 1) // BATCH_SIZE} "
              f"({len(batch)} gages)")
        t0 = _time.perf_counter()
        df, _ = waterdata.get_daily(
            monitoring_location_id=batch,
            parameter_code="00060",
            statistic_id="00003",
            time=(WIDE_START, WIDE_END),
            skip_geometry=True,
            properties=["monitoring_location_id", "time", "value"],
        )
        print(f"  {len(df):,} rows in {_time.perf_counter() - t0:.1f}s")
        if len(df) == 0:
            continue
        df = df.dropna(subset=["value"])
        for gage_id, data in df.groupby("monitoring_location_id"):
            row = {
                "usgs_id": gage_id,
                "first": data["time"].min(),
                "last": data["time"].max(),
                "n_days": len(data),
            }
            # Count actual observations inside each candidate window, not the
            # span. A record running 1990-2020 may hold only a handful of days
            # after 2018, and span-based counting would call that recovered.
            for win_start, win_end in CANDIDATE_WINDOWS:
                inside = data["time"].between(win_start, win_end).sum()
                row[f"n_{win_start[:4]}"] = int(inside)
            rows.append(row)
    return pd.DataFrame(rows)




def main():
    missing = find_missing()
    print(f"seed gages with no USGS cache row: {len(missing)}\n")

    rec = fetch_record(missing)
    rec.to_csv("usgs_period_of_record.csv", index=False)

    have = len(rec)
    none_ever = len(missing) - have
    print(f"\n{'=' * 58}")
    print(f"gages checked:               {len(missing)}")
    print(f"  have SOME 00060 data:      {have}")
    print(f"  no discharge data at all:  {none_ever}   <- unrecoverable")

    if have == 0:
        print("\nNothing recoverable by widening the window.")
        return

    print(f"\nperiod of record, for the {have} that have data:")
    print(f"  earliest first obs: {rec['first'].min().date()}")
    print(f"  latest last obs:    {rec['last'].max().date()}")
    print("\n  last observation by decade:")
    print(rec["last"].dt.year.floordiv(10).mul(10)
          .value_counts().sort_index().to_string())

    print(f"\ngages recovered per candidate window "
          f"(>= {MIN_OVERLAP_DAYS} observations INSIDE the window):")
    print(f"  {'window':<25} {'gages':>7}  {'of 853':>9}")
    for win_start, win_end in CANDIDATE_WINDOWS:
        n = int((rec[f"n_{win_start[:4]}"] >= MIN_OVERLAP_DAYS).sum())
        print(f"  {win_start} .. {win_end}  {n:>7}  {n / len(missing) * 100:>8.1f}%")

    print(f"\nwrote usgs_period_of_record.csv ({have} rows)")
    print("cache untouched — nothing was written to retrospective_cache.")


if __name__ == "__main__":
    main()
