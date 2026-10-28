import unittest
from bridge_app.services.net_worth_service import (
    compute_net_worth_analytics,
    generate_net_worth_png,
)


class TestNetWorthService(unittest.TestCase):
    def test_empty_and_filtered_snapshots(self):
        expected_empty = {
            "summary": {},
            "history": [],
            "weekly_history": [],
            "daily_history": [],
        }
        self.assertEqual(
            compute_net_worth_analytics([]),
            expected_empty,
        )
        # Snapshots before start_date_filter or missing balance should be excluded
        snapshots = [
            {"date": "2023-12-31", "balance": 100000.0},
            {"date": "2024-01-05", "balance": None},
        ]
        self.assertEqual(
            compute_net_worth_analytics(snapshots, start_date_filter="2024-01-01"),
            expected_empty,
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
            "annualized_gain_percent",
            "avg_monthly_gain_percent",
            "weekly_history",
            "daily_history",
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
        self.assertEqual(summary["annualized_gain_percent"], 0.0)
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

    def test_annualized_gain_calculation(self):
        # 12-month period with 10% gain
        snaps_12m = [
            {"date": f"2024-{m:02d}-28", "balance": 100000.0 * (1 + 0.10 * (m / 12))}
            for m in range(1, 13)
        ]
        snaps_12m.insert(0, {"date": "2024-01-01", "balance": 100000.0})
        res_12m = compute_net_worth_analytics(snaps_12m)
        self.assertEqual(len(res_12m["history"]), 12)
        self.assertEqual(res_12m["summary"]["annualized_gain_percent"], 10.0)

        # 24-month period with 21% cumulative gain -> exactly 10.0% CAGR
        snaps_24m = [
            {"date": "2024-01-01", "balance": 100000.0},
            {"date": "2024-12-31", "balance": 110000.0},
            {"date": "2025-12-31", "balance": 121000.0},
        ]
        # Include monthly points so history length is 24 months
        all_24m = [{"date": "2024-01-01", "balance": 100000.0}]
        for y in (2024, 2025):
            for m in range(1, 13):
                frac = ((y - 2024) * 12 + m) / 24.0
                all_24m.append({"date": f"{y}-{m:02d}-28", "balance": 100000.0 * (1.21 ** frac)})
        res_24m = compute_net_worth_analytics(all_24m)
        self.assertEqual(len(res_24m["history"]), 24)
        self.assertEqual(res_24m["summary"]["annualized_gain_percent"], 10.0)

        # 12-month period with 10% loss
        snaps_loss = [
            {"date": f"2024-{m:02d}-28", "balance": 100000.0 * (1 - 0.10 * (m / 12))}
            for m in range(1, 13)
        ]
        snaps_loss.insert(0, {"date": "2024-01-01", "balance": 100000.0})
        res_loss = compute_net_worth_analytics(snaps_loss)
        self.assertEqual(res_loss["summary"]["annualized_gain_percent"], -10.0)

    def test_weekly_and_daily_sampling(self):
        # Multi-day snapshots across 3 distinct calendar weeks
        snaps = [
            {"date": "2024-01-01", "balance": 100000.0},  # 2024-W01 Monday
            {"date": "2024-01-03", "balance": 102000.0},  # 2024-W01 Wednesday
            {"date": "2024-01-07", "balance": 103000.0},  # 2024-W01 Sunday (latest in W01)
            {"date": "2024-01-08", "balance": 104000.0},  # 2024-W02 Monday
            {"date": "2024-01-14", "balance": 105000.0},  # 2024-W02 Sunday (latest in W02)
            {"date": "2024-01-15", "balance": 106000.0},  # 2024-W03 Monday (latest in W03)
        ]
        res = compute_net_worth_analytics(snaps)
        weekly = res["weekly_history"]
        daily = res["daily_history"]

        self.assertEqual(len(weekly), 3)
        self.assertEqual(weekly[0]["week"], "2024-W01")
        self.assertEqual(weekly[0]["date"], "2024-01-07")
        self.assertEqual(weekly[0]["balance"], 103000.0)

        self.assertEqual(weekly[1]["week"], "2024-W02")
        self.assertEqual(weekly[1]["date"], "2024-01-14")
        self.assertEqual(weekly[1]["balance"], 105000.0)

        self.assertEqual(weekly[2]["week"], "2024-W03")
        self.assertEqual(weekly[2]["date"], "2024-01-15")
        self.assertEqual(weekly[2]["balance"], 106000.0)

        self.assertEqual(len(daily), 6)
        self.assertEqual(daily[0]["date"], "2024-01-01")
        self.assertEqual(daily[-1]["date"], "2024-01-15")


if __name__ == "__main__":
    unittest.main()
