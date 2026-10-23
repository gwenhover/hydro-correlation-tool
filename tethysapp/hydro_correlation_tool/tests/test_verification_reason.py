"""
Tests for the Save and Verify reason check (model.clean_verification_reason),
which save_and_verify runs once for NWM and once for GEOGLOWS.

    python -m unittest -v tethysapp.hydro_correlation_tool.tests.test_verification_reason
"""
import unittest

from tethysapp.hydro_correlation_tool.model import (
    VERIFICATION_COMMENT_MAX, VERIFICATION_OTHER, VERIFICATION_REASONS, clean_verification_reason,
)


class VerificationReasonTest(unittest.TestCase):

    def test_offered_reasons(self):
        self.assertEqual(VERIFICATION_REASONS, (
            'Matched KGE only',
            'Matched DA only',
            'Matched both KGE and DA',
            'Visual match (map location / hydrograph)',
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


if __name__ == "__main__":
    unittest.main()
