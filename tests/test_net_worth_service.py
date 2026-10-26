import unittest
from bridge_app.services.net_worth_service import (
    compute_net_worth_analytics,
    generate_net_worth_png,
)


class TestNetWorthService(unittest.TestCase):
    def test_empty_and_filtered_snapshots(self):
        self.assertEqual(
            compute_net_worth_analytics([]),
            {"summary": {}, "history": []},
        )
        # Snapshots before start_date_filter or missing balance should be excluded
        snapshots = [
            {"date": "2023-12-31", "balance": 100000.0},
            {"date": "2024-01-05", "balance": None},
        ]
        self.assertEqual(
            compute_net_worth_analytics(snapshots, start_date_filter="2024-01-01"),
            {"summary": {}, "history": []},
        )

    def test_multi_month_calculation_and_schema(self):
        # Provide unsorted snapshots across multiple months
        snapshots = [
            {"date": "2024-02-29", "balance": 2093334.48},
            {"date": "2024-01-01", "balance": 1950794.07},
            {"date": "2024-01-15", "balance": 1980000.00},
            {"date": "2024-01-31", "balance": 1998643.82},
            {"date": "2024-02-10", "balance": 2010000.00},
            {"date": "2024-03-15", "balance": 1900000.00},
            {"date": "2024-03-31", "balance": 1950794.07},
        ]
        res = compute_net_worth_analytics(
            snapshots,
            start_date_filter="2024-01-01",
            updated_at_iso="2026-04-04T15:30:00+02:00",
        )

        self.assertIn("summary", res)
        self.assertIn("history", res)

        summary = res["summary"]
        history = res["history"]
        self.assertEqual(len(history), 3)

        # Verify Jan 2024
        jan = history[0]
        self.assertEqual(jan["month"], "2024-01")
        self.assertEqual(jan["start_balance"], 1950794.07)
        self.assertEqual(jan["end_balance"], 1998643.82)
        self.assertEqual(jan["gain_amount"], 47849.75)
        self.assertEqual(jan["gain_percent"], 2.45)
        self.assertEqual(jan["cumulative_gain_amount"], 47849.75)
        self.assertEqual(jan["cumulative_gain_percent"], 2.45)
        self.assertEqual(jan["end_date"], "2024-01-31")

        # Verify Feb 2024 (start_balance = Jan's end_balance)
        feb = history[1]
        self.assertEqual(feb["month"], "2024-02")
        self.assertEqual(feb["start_balance"], 1998643.82)
        self.assertEqual(feb["end_balance"], 2093334.48)
        self.assertEqual(feb["gain_amount"], 94690.66)
        self.assertEqual(feb["gain_percent"], 4.74)
        self.assertEqual(feb["cumulative_gain_amount"], 142540.41)
        self.assertEqual(feb["cumulative_gain_percent"], 7.31)
        self.assertEqual(feb["end_date"], "2024-02-29")

        # Verify Mar 2024 (drawdown back to initial balance)
        mar = history[2]
        self.assertEqual(mar["month"], "2024-03")
        self.assertEqual(mar["start_balance"], 2093334.48)
        self.assertEqual(mar["end_balance"], 1950794.07)
        self.assertEqual(mar["gain_amount"], -142540.41)
        self.assertEqual(mar["gain_percent"], -6.81)
        self.assertEqual(mar["cumulative_gain_amount"], 0.0)
        self.assertEqual(mar["cumulative_gain_percent"], 0.0)
        self.assertEqual(mar["end_date"], "2024-03-31")

        # Verify Summary KPIs
        expected_summary_keys = {
            "start_date",
            "latest_date",
            "starting_net_worth",
            "current_net_worth",
            "total_gain_amount",
            "total_gain_percent",
            "peak_net_worth",
            "peak_month",
            "best_month",
            "best_month_percent",
            "worst_month",
            "worst_month_percent",
            "avg_monthly_gain_percent",
            "updated_at",
        }
        self.assertEqual(set(summary.keys()), expected_summary_keys)
        self.assertEqual(summary["start_date"], "2024-01-01")
        self.assertEqual(summary["latest_date"], "2024-03-31")
        self.assertEqual(summary["starting_net_worth"], 1950794.07)
        self.assertEqual(summary["current_net_worth"], 1950794.07)
        self.assertEqual(summary["total_gain_amount"], 0.0)
        self.assertEqual(summary["total_gain_percent"], 0.0)
        self.assertEqual(summary["peak_net_worth"], 2093334.48)
        self.assertEqual(summary["peak_month"], "2024-02")
        self.assertEqual(summary["best_month"], "2024-02")
        self.assertEqual(summary["best_month_percent"], 4.74)
        self.assertEqual(summary["worst_month"], "2024-03")
        self.assertEqual(summary["worst_month_percent"], -6.81)
        self.assertEqual(
            summary["avg_monthly_gain_percent"],
            round((2.45 + 4.74 - 6.81) / 3, 2),
        )
        self.assertEqual(summary["updated_at"], "2026-04-04T15:30:00+02:00")

    def test_zero_initial_balance_handling(self):
        snapshots = [
            {"date": "2024-01-01", "balance": 0.0},
            {"date": "2024-01-31", "balance": 5000.0},
        ]
        res = compute_net_worth_analytics(snapshots)
        self.assertEqual(res["history"][0]["gain_percent"], 0.0)
        self.assertEqual(res["history"][0]["cumulative_gain_percent"], 0.0)
        self.assertEqual(res["summary"]["total_gain_percent"], 0.0)

    def test_generate_png_graceful(self):
        snapshots = [
            {"date": "2024-01-01", "balance": 100000.0},
            {"date": "2024-01-31", "balance": 110000.0},
            {"date": "2024-02-29", "balance": 105000.0},
        ]
        payload = compute_net_worth_analytics(snapshots)
        png_bytes = generate_net_worth_png(payload)
        if png_bytes is not None:
            self.assertTrue(png_bytes.startswith(b"\x89PNG"))

    def test_exact_target_schema_calculations(self):
        # Verify January 2024 exact numbers from Section 1.2 specification
        snapshots = [
            {"date": "2024-01-01", "balance": 5949529.45},
            {"date": "2024-01-31", "balance": 6098548.96},
        ]
        res = compute_net_worth_analytics(snapshots, start_date_filter="2024-01-01")
        jan = res["history"][0]
        self.assertEqual(jan["month"], "2024-01")
        self.assertEqual(jan["start_balance"], 5949529.45)
        self.assertEqual(jan["end_balance"], 6098548.96)
        self.assertEqual(jan["gain_amount"], 149019.51)
        self.assertEqual(jan["gain_percent"], 2.50)
        self.assertEqual(jan["cumulative_gain_amount"], 149019.51)
        self.assertEqual(jan["cumulative_gain_percent"], 2.50)
        self.assertEqual(jan["end_date"], "2024-01-31")


if __name__ == "__main__":
    unittest.main()
