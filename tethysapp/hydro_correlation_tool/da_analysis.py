"""
DA Analysis: an advisory drainage-area check of a gage's NWM and GEOGLOWS reaches.

Pure calculation -- no web, database or network code, so it can be tested
without starting Tethys. fetchers.py supplies the source values and
controllers.py wires them to the panel and the CSV export.

For each model, with t = DA_TOLERANCE:
    ratio    = DS / USGS_DA
    PASS     if US*(1-t)  <= USGS_DA <= DS*(1+t)
    MARGINAL if not PASS and US*(1-2t) <= USGS_DA <= DS*(1+2t)
    FAIL     otherwise
    NO DATA  if USGS_DA, US or DS is missing or <= 0 (headwater reach: US = 0 is allowed)
All areas are in km2.
"""
import math

import pandas as pd

DA_TOLERANCE = 0.10

SQ_MI_TO_KM2 = 2.58999
# GEOGLOWS v2 USContArea/DSContArea are not documented. Checked against USGS
# drainage areas at large gages (see docs/methodology.md): they are in m2.
GEOGLOWS_AREA_TO_KM2 = 1e-6
# NHDPlus rounding can leave TotDASqKM - AreaSqKM a hair below zero on a
# headwater reach; anything within this of zero is treated as US = 0.
HEADWATER_ROUNDING_KM2 = 0.01

PASS = "PASS"
MARGINAL = "MARGINAL"
FAIL = "FAIL"
NO_DATA = "NO DATA"

DA_EXPORT_COLUMNS = [
    "usgs_da_km2",
    "nwm_da_us_km2", "nwm_da_ds_km2", "nwm_da_ratio", "nwm_da_class",
    "geoglows_da_us_km2", "geoglows_da_ds_km2", "geoglows_da_ratio", "geoglows_da_class",
]


def _number(value):
    # None for anything missing (None, NaN, pd.NA), otherwise a float.
    if value is None or pd.isna(value):
        return None
    return float(value)


def sq_mi_to_km2(sq_mi):
    sq_mi = _number(sq_mi)
    return None if sq_mi is None else sq_mi * SQ_MI_TO_KM2


def nwm_us_ds(totdasqkm, areasqkm):
    # NWM_DS = TotDASqKM, NWM_US = TotDASqKM - AreaSqKM
    ds = _number(totdasqkm)
    area = _number(areasqkm)
    if ds is None or area is None:
        return None, ds
    us = ds - area
    if -HEADWATER_ROUNDING_KM2 < us < 0:
        us = 0.0
    return us, ds


def geoglows_us_ds(us_cont_area, ds_cont_area):
    # GEO_US = USContArea, GEO_DS = DSContArea, converted to km2
    us = _number(us_cont_area)
    ds = _number(ds_cont_area)
    return (
        None if us is None else us * GEOGLOWS_AREA_TO_KM2,
        None if ds is None else ds * GEOGLOWS_AREA_TO_KM2,
    )


def _within(value, low, high):
    # Inclusive on both ends; isclose keeps a value sitting exactly on a
    # boundary (e.g. 110 vs 100 * 1.1 = 110.00000000000001) from flipping class.
    return (low <= value <= high) or math.isclose(value, low) or math.isclose(value, high)


def classify(usgs_da, us, ds, tolerance=DA_TOLERANCE):
    """Return (ratio, class) for one model. ratio is None when it can't be computed."""
    usgs_da, us, ds = _number(usgs_da), _number(us), _number(ds)
    if usgs_da is None or usgs_da <= 0 or ds is None or ds <= 0 or us is None or us < 0:
        return None, NO_DATA
    ratio = ds / usgs_da
    if _within(usgs_da, us * (1 - tolerance), ds * (1 + tolerance)):
        return ratio, PASS
    if _within(usgs_da, us * (1 - 2 * tolerance), ds * (1 + 2 * tolerance)):
        return ratio, MARGINAL
    return ratio, FAIL


def da_result(usgs_da, us, ds):
    ratio, da_class = classify(usgs_da, us, ds)
    return {
        "usgs_da_km2": _number(usgs_da),
        "us_km2": _number(us),
        "ds_km2": _number(ds),
        "ratio": ratio,
        "class": da_class,
    }


def build_da_columns(df, usgs_da_km2, nwm_reference, geoglows_reference):
    """
    Return a copy of the export table with DA_EXPORT_COLUMNS appended.

    df: has usgs_id, nwm_feature_id and geoglows_river_id columns.
    usgs_da_km2: {usgs_id: km2 or None}.
    nwm_reference: indexed by COMID, with totdasqkm and areasqkm.
    geoglows_reference: indexed by LINKNO, with USContArea and DSContArea.
    """
    rows = []
    for usgs_id, nwm_id, geo_id in zip(df["usgs_id"], df["nwm_feature_id"], df["geoglows_river_id"]):
        usgs_da = usgs_da_km2.get(usgs_id)

        nwm_us = nwm_ds = None
        if not pd.isna(nwm_id) and int(nwm_id) in nwm_reference.index:
            ref = nwm_reference.loc[int(nwm_id)]
            nwm_us, nwm_ds = nwm_us_ds(ref["totdasqkm"], ref["areasqkm"])

        geo_us = geo_ds = None
        if not pd.isna(geo_id) and int(geo_id) in geoglows_reference.index:
            ref = geoglows_reference.loc[int(geo_id)]
            geo_us, geo_ds = geoglows_us_ds(ref["USContArea"], ref["DSContArea"])

        nwm_ratio, nwm_class = classify(usgs_da, nwm_us, nwm_ds)
        geo_ratio, geo_class = classify(usgs_da, geo_us, geo_ds)
        rows.append([
            usgs_da,
            nwm_us, nwm_ds, nwm_ratio, nwm_class,
            geo_us, geo_ds, geo_ratio, geo_class,
        ])

    da = pd.DataFrame(rows, columns=DA_EXPORT_COLUMNS, index=df.index)
    area_columns = ["usgs_da_km2", "nwm_da_us_km2", "nwm_da_ds_km2", "geoglows_da_us_km2", "geoglows_da_ds_km2"]
    da[area_columns] = da[area_columns].astype(float).round(2)
    da[["nwm_da_ratio", "geoglows_da_ratio"]] = da[["nwm_da_ratio", "geoglows_da_ratio"]].astype(float).round(4)
    return pd.concat([df, da], axis=1)
