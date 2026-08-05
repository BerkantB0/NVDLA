from __future__ import annotations

import unittest

from nvdla_test_framework.campaign import _change_percentage, _energy_label, _mean_summary


class CampaignReportTests(unittest.TestCase):
    def test_mean_summary_preserves_session_spread(self) -> None:
        summary = _mean_summary([1.0, 2.0, 3.0, 4.0, 5.0])
        self.assertEqual(summary["session_count"], 5)
        self.assertEqual(summary["mean"], 3.0)
        self.assertEqual(summary["median"], 3.0)
        self.assertAlmostEqual(summary["standard_deviation"], 2.5**0.5)

    def test_reader_facing_energy_units_and_relative_change(self) -> None:
        self.assertEqual(_energy_label(1781.0), "1.78 J")
        self.assertAlmostEqual(_change_percentage(3.0, 4.0), -25.0)


if __name__ == "__main__":
    unittest.main()
