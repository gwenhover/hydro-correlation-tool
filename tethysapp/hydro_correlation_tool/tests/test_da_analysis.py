"""
Tests for da_analysis.py (pure calculation -- no database or network needed).

    python -m unittest -v tethysapp.hydro_correlation_tool.tests.test_da_analysis
"""
import math
import unittest

import pandas as pd

from tethysapp.hydro_correlation_tool.da_analysis import (
    DA_EXPORT_COLUMNS, DA_TOLERANCE, FAIL, MARGINAL, NO_DATA, PASS,
    build_da_columns, classify, geoglows_us_ds, nwm_us_ds, sq_mi_to_km2,
)


class ClassifyTest(unittest.TestCase):
    # Reach with US = 90 km2, DS = 100 km2 and t = 0.10:
    #   PASS band     81 .. 110
    #   MARGINAL band 72 .. 120

    def test_tolerance_is_ten_percent(self):
        self.assertEqual(DA_TOLERANCE, 0.10)

    def test_pass_inside_reach(self):
        self.assertEqual(classify(95, 90, 100)[1], PASS)

    def test_pass_boundaries_are_inclusive(self):
        self.assertEqual(classify(110, 90, 100)[1], PASS)
        self.assertEqual(classify(81, 90, 100)[1], PASS)

    def test_marginal_just_outside_pass(self):
        self.assertEqual(classify(110.5, 90, 100)[1], MARGINAL)
        self.assertEqual(classify(80.5, 90, 100)[1], MARGINAL)

    def test_marginal_boundaries_are_inclusive(self):
        self.assertEqual(classify(120, 90, 100)[1], MARGINAL)
        self.assertEqual(classify(72, 90, 100)[1], MARGINAL)

    def test_fail_outside_marginal(self):
        self.assertEqual(classify(120.5, 90, 100)[1], FAIL)
        self.assertEqual(classify(71.5, 90, 100)[1], FAIL)

    def test_ratio_is_ds_over_usgs(self):
        ratio, _ = classify(80, 70, 100)
        self.assertAlmostEqual(ratio, 1.25)

    def test_headwater_us_zero_is_allowed(self):
        self.assertEqual(classify(5, 0, 10)[1], PASS)
        self.assertEqual(classify(11.5, 0, 10)[1], MARGINAL)
        self.assertEqual(classify(13, 0, 10)[1], FAIL)

    def test_no_data_when_missing_or_not_positive(self):
        for usgs, us, ds in [
            (None, 90, 100), (95, None, 100), (95, 90, None),
            (float("nan"), 90, 100), (pd.NA, 90, 100),
            (0, 90, 100), (-1, 90, 100), (95, 90, 0), (95, -5, 100),
        ]:
            ratio, da_class = classify(usgs, us, ds)
            self.assertEqual(da_class, NO_DATA, (usgs, us, ds))
            self.assertIsNone(ratio, (usgs, us, ds))


class ConversionTest(unittest.TestCase):

    def test_sq_mi_to_km2(self):
        self.assertAlmostEqual(sq_mi_to_km2(1), 2.58999)
        self.assertIsNone(sq_mi_to_km2(None))

    def test_nwm_us_is_total_minus_local(self):
        self.assertEqual(nwm_us_ds(100.0, 12.5), (87.5, 100.0))

    def test_nwm_headwater_rounding_becomes_zero(self):
        self.assertEqual(nwm_us_ds(3.2, 3.205), (0.0, 3.2))

    def test_nwm_clearly_negative_us_is_kept_for_no_data(self):
        us, ds = nwm_us_ds(3.0, 4.0)
        self.assertLess(us, 0)
        self.assertEqual(classify(3, us, ds)[1], NO_DATA)

    def test_geoglows_m2_to_km2(self):
        us, ds = geoglows_us_ds(2_000_000, 5_000_000)
        self.assertAlmostEqual(us, 2.0)
        self.assertAlmostEqual(ds, 5.0)


class ExportColumnsTest(unittest.TestCase):

    def setUp(self):
        self.df = pd.DataFrame({
            "usgs_id": ["USGS-1", "USGS-2", "USGS-3"],
            "gage_name": ["a", "b", "c"],
            "nwm_feature_id": pd.array([11, 12, None], dtype="Int64"),
            "geoglows_river_id": pd.array([21, None, 99], dtype="Int64"),
            "verification_status": ["Verified", "Edited", "Unverified"],
        })
        self.nwm = pd.DataFrame({"totdasqkm": [100.0, 500.0], "areasqkm": [10.0, 5.0]}, index=pd.Index([11, 12], name="comid"))
        self.geo = pd.DataFrame({"USContArea": [95e6], "DSContArea": [105e6]}, index=pd.Index([21], name="LINKNO"))
        self.usgs = {"USGS-1": 100.0, "USGS-2": 100.0}

    def test_existing_columns_kept_in_order_and_da_appended(self):
        out = build_da_columns(self.df, self.usgs, self.nwm, self.geo)
        self.assertEqual(list(out.columns), list(self.df.columns) + DA_EXPORT_COLUMNS)
        pd.testing.assert_frame_equal(out[self.df.columns], self.df)

    def test_values_and_classes(self):
        out = build_da_columns(self.df, self.usgs, self.nwm, self.geo).set_index("usgs_id")
        self.assertEqual(out.loc["USGS-1", "nwm_da_class"], PASS)
        self.assertEqual(out.loc["USGS-1", "nwm_da_us_km2"], 90.0)
        self.assertEqual(out.loc["USGS-1", "nwm_da_ds_km2"], 100.0)
        self.assertEqual(out.loc["USGS-1", "geoglows_da_class"], PASS)
        self.assertEqual(out.loc["USGS-1", "geoglows_da_ds_km2"], 105.0)
        self.assertEqual(out.loc["USGS-2", "nwm_da_class"], FAIL)        # 100 km2 gage on a 495-500 km2 reach
        self.assertEqual(out.loc["USGS-2", "geoglows_da_class"], NO_DATA)  # no GEOGLOWS id
        self.assertEqual(out.loc["USGS-3", "nwm_da_class"], NO_DATA)     # no USGS DA, no NWM id
        self.assertEqual(out.loc["USGS-3", "geoglows_da_class"], NO_DATA)  # id not in reference table
        self.assertTrue(math.isnan(out.loc["USGS-3", "usgs_da_km2"]))

    def test_empty_reference_tables_give_no_data(self):
        empty_nwm = pd.DataFrame(columns=["totdasqkm", "areasqkm"])
        empty_geo = pd.DataFrame(columns=["USContArea", "DSContArea"])
        out = build_da_columns(self.df, {}, empty_nwm, empty_geo)
        self.assertEqual(set(out["nwm_da_class"]), {NO_DATA})
        self.assertEqual(set(out["geoglows_da_class"]), {NO_DATA})


if __name__ == "__main__":
    unittest.main()
