"""
Tests for the Save and Verify reason check (model.clean_verification_reason),
which save_and_verify runs once for NWM and once for GEOGLOWS, and for the
reach ID check that pairs an empty ID with "No matching reach"
(model.clean_reach_id, model.same_reach).

    python -m unittest -v tethysapp.hydro_correlation_tool.tests.test_verification_reason
"""
import unittest

from tethysapp.hydro_correlation_tool.model import (
    VERIFICATION_COMMENT_MAX, VERIFICATION_OTHER, VERIFICATION_REASONS, clean_verification_reason,
)
from tethysapp.hydro_correlation_tool.model import VERIFICATION_NO_MATCH, clean_reach_id, same_reach


class VerificationReasonTest(unittest.TestCase):

    def test_offered_reasons(self):
        self.assertEqual(VERIFICATION_REASONS, (
            'Matched KGE only',
            'Matched DA only',
            'Matched both KGE and DA',
            'Visual match (map location / hydrograph)',
            'No matching reach',
            'Other',
        ))

    def test_each_listed_reason_is_accepted(self):
        for reason in VERIFICATION_REASONS:
            if reason != VERIFICATION_OTHER:
                self.assertEqual(clean_verification_reason(reason, ''), (reason, None))

    def test_reason_is_required(self):
        for missing in (None, '', '   '):
            self.assertEqual(clean_verification_reason(missing, 'anything'), (None, None))

    def test_unknown_reason_is_refused(self):
        self.assertEqual(clean_verification_reason('Looked fine', ''), (None, None))

    def test_comment_dropped_for_non_other_reasons(self):
        self.assertEqual(clean_verification_reason('Matched DA only', 'stray text'), ('Matched DA only', None))

    def test_other_needs_a_comment(self):
        for missing in (None, '', '   '):
            self.assertEqual(clean_verification_reason('Other', missing), (None, None))

    def test_other_comment_is_trimmed_and_kept(self):
        self.assertEqual(clean_verification_reason('Other', '  Gage moved in 2019  '), ('Other', 'Gage moved in 2019'))

    def test_other_comment_length_limit(self):
        self.assertEqual(clean_verification_reason('Other', 'x' * VERIFICATION_COMMENT_MAX), ('Other', 'x' * VERIFICATION_COMMENT_MAX))
        self.assertEqual(clean_verification_reason('Other', 'x' * (VERIFICATION_COMMENT_MAX + 1)), (None, None))


class ReachIdTest(unittest.TestCase):

    def test_id_with_a_normal_reason(self):
        self.assertEqual(clean_reach_id('760653144', 'Matched KGE only'), (True, 760653144))
        self.assertEqual(clean_reach_id(' 23101142 ', 'Other'), (True, 23101142))

    def test_empty_id_needs_no_matching_reach(self):
        for empty in (None, '', '   '):
            self.assertEqual(clean_reach_id(empty, VERIFICATION_NO_MATCH), (True, None))
            self.assertEqual(clean_reach_id(empty, 'Matched DA only'), (False, None))

    def test_no_matching_reach_needs_an_empty_id(self):
        self.assertEqual(clean_reach_id('5055179', VERIFICATION_NO_MATCH), (False, None))

    def test_non_numeric_id_is_refused(self):
        for bad in ('abc', '12.5', '12 34'):
            self.assertEqual(clean_reach_id(bad, 'Matched KGE only'), (False, None))

    def test_same_reach(self):
        self.assertTrue(same_reach(5055179, 5055179))
        self.assertTrue(same_reach('5055179', 5055179))
        self.assertFalse(same_reach(5055179, 5059231))
        self.assertTrue(same_reach(None, None))        # seed had no reach, saved as none
        self.assertFalse(same_reach(5055179, None))    # seeded reach removed
        self.assertFalse(same_reach(None, 5055179))    # reach added where the seed had none


if __name__ == "__main__":
    unittest.main()
