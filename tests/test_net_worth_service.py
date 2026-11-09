import unittest
from bridge_app.services.net_worth_service import (
    classify_account,
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

    def test_retirement_return_decomposition_no_double_counting(self):
        # Month with $1,000,000 start, $1,002,500 end (+$2,500 net worth change)
        # Dividends=$3,000, Interest=$1,000, Cap Gains=$500 (inv_income=$4,500)
        # Living spending=$6,000, Transfer=$10,000 (ignored)
        snapshots = [
            {"date": "2024-01-01", "balance": 1000000.0},
            {"date": "2024-01-31", "balance": 1002500.0},
        ]
        categories = [
            {"id": "c_div", "name": "Dividends", "group": {"type": "income"}},
            {"id": "c_int", "name": "Interest", "group": {"type": "income"}},
            {"id": "c_cg", "name": "Capital Gains", "group": {"type": "income"}},
            {"id": "c_exp", "name": "Groceries", "group": {"type": "expense"}},
            {"id": "c_trf", "name": "Transfer", "group": {"type": "transfer"}},
        ]
        transactions = [
            {"date": "2024-01-10", "amount": 3000.0, "category": {"id": "c_div"}},
            {"date": "2024-01-12", "amount": 1000.0, "category": {"id": "c_int"}},
            {"date": "2024-01-15", "amount": 500.0, "category": {"id": "c_cg"}},
            {"date": "2024-01-20", "amount": -6000.0, "category": {"id": "c_exp"}},
            {"date": "2024-01-25", "amount": -10000.0, "category": {"id": "c_trf"}},
        ]
        res = compute_net_worth_analytics(
            snapshots,
            start_date_filter="2024-01-01",
            transactions=transactions,
            categories=categories,
        )
        jan = res["history"][0]
        self.assertEqual(jan["gain_amount"], 2500.0)
        self.assertEqual(jan["dividends"], 3000.0)
        self.assertEqual(jan["interest"], 1000.0)
        self.assertEqual(jan["capital_gains"], 500.0)
        self.assertEqual(jan["inv_income"], 4500.0)
        self.assertEqual(jan["other_income"], 0.0)
        self.assertEqual(jan["spending"], 6000.0)
        self.assertEqual(jan["net_external_flow"], -6000.0)
        self.assertEqual(jan["total_return_dollars"], 8500.0)
        self.assertEqual(jan["price_change_dollars"], 4000.0)
        # Zero double-counting identity:
        self.assertEqual(
            round(
                jan["interest"]
                + jan["dividends"]
                + jan["capital_gains"]
                + jan["price_change_dollars"]
                + jan["other_income"]
                - jan["spending"],
                2,
            ),
            jan["gain_amount"],
        )

    def test_modified_dietz_twr_and_chain_linking(self):
        # Month 1: $1,000,000 start, $50,000 withdrawal, investment return +$19,500 (+2.0% on $975k avg capital)
        # End balance = $969,500 (raw gain_percent = -3.05%, but TWR = +2.00%)
        # Month 2: $969,500 start, $0 flow, +5.0% return -> end balance = $1,017,975.0
        # Chain-linked TWR = (1.02 * 1.05 - 1) * 100 = +7.10%
        snapshots = [
            {"date": "2024-01-01", "balance": 1000000.0},
            {"date": "2024-01-31", "balance": 969500.0},
            {"date": "2024-02-29", "balance": 1017975.0},
        ]
        transactions = [
            {
                "date": "2024-01-15",
                "amount": -50000.0,
                "category": {"id": "c1", "name": "Living Expenses", "group": {"type": "expense"}},
            }
        ]
        res = compute_net_worth_analytics(snapshots, transactions=transactions)
        jan = res["history"][0]
        feb = res["history"][1]

        self.assertEqual(jan["gain_percent"], -3.05)
        self.assertEqual(jan["twr_percent"], 2.0)
        self.assertEqual(jan["cumulative_twr_percent"], 2.0)

        self.assertEqual(feb["twr_percent"], 5.0)
        self.assertEqual(feb["cumulative_twr_percent"], 7.1)
        self.assertEqual(res["retirement_analytics"]["twr_total_percent"], 7.1)

    def test_withdrawal_rate_thresholds_and_coverage(self):
        # 12 months with $2,000,000 investable net worth ($200k cash + $1,800k brokerage)
        # Monthly spending = $5,000 ($60,000/yr -> 3.0% SWR -> "safe")
        # Monthly dividends = $2,500 ($30,000/yr -> 50.0% income coverage, 1.5% yield)
        # Cash buffer = $200,000 / $5,000 = 40.0 months
        snapshots = [{"date": "2024-01-01", "balance": 2000000.0}]
        snapshots_by_type = []
        transactions = []
        for m in range(1, 13):
            m_str = f"2024-{m:02d}"
            snapshots.append({"date": f"{m_str}-28", "balance": 2000000.0})
            snapshots_by_type.append(
                {"accountType": "depository", "month": m_str, "balance": 200000.0}
            )
            snapshots_by_type.append(
                {"accountType": "brokerage", "month": m_str, "balance": 1800000.0}
            )
            transactions.append(
                {
                    "date": f"{m_str}-10",
                    "amount": 2500.0,
                    "category": {"name": "Dividends", "group": {"type": "income"}},
                }
            )
            transactions.append(
                {
                    "date": f"{m_str}-20",
                    "amount": -5000.0,
                    "category": {"name": "Travel", "group": {"type": "expense"}},
                }
            )

        res_safe = compute_net_worth_analytics(
            snapshots,
            snapshots_by_type=snapshots_by_type,
            transactions=transactions,
        )
        ret = res_safe["retirement_analytics"]
        self.assertEqual(ret["trailing_12m_spending"], 60000.0)
        self.assertEqual(ret["trailing_12m_inv_income"], 30000.0)
        self.assertEqual(ret["withdrawal_rate_percent"], 3.0)
        self.assertEqual(ret["withdrawal_rate_status"], "safe")
        self.assertEqual(ret["income_coverage_percent"], 50.0)
        self.assertEqual(ret["yield_on_investable_percent"], 1.5)
        self.assertEqual(ret["cash_buffer_months"], 40.0)

        # Increase spending to $7,500/mo ($90,000/yr -> 4.5% SWR -> "caution")
        tx_caution = [
            {
                "date": f"2024-{m:02d}-20",
                "amount": -7500.0,
                "category": {"name": "Living", "group": {"type": "expense"}},
            }
            for m in range(1, 13)
        ]
        res_caution = compute_net_worth_analytics(
            snapshots,
            snapshots_by_type=snapshots_by_type,
            transactions=tx_caution,
        )
        self.assertEqual(res_caution["retirement_analytics"]["withdrawal_rate_percent"], 4.5)
        self.assertEqual(res_caution["retirement_analytics"]["withdrawal_rate_status"], "caution")

        # Increase spending to $10,000/mo ($120,000/yr -> 6.0% SWR -> "high")
        tx_high = [
            {
                "date": f"2024-{m:02d}-20",
                "amount": -10000.0,
                "category": {"name": "Living", "group": {"type": "expense"}},
            }
            for m in range(1, 13)
        ]
        res_high = compute_net_worth_analytics(
            snapshots,
            snapshots_by_type=snapshots_by_type,
            transactions=tx_high,
        )
        self.assertEqual(res_high["retirement_analytics"]["withdrawal_rate_percent"], 6.0)
        self.assertEqual(res_high["retirement_analytics"]["withdrawal_rate_status"], "high")

    def test_account_type_scopes_and_drawdown(self):
        # Month 1 (2024-01): Total = $2,000,000 (Cash $100k, Brokerage $1,500k, Real Estate $450k, Credit -$10k, Loan -$40k)
        #   Investable = $1,600,000, Liquid = $1,590,000, Illiquid = $400,000
        # Month 2 (2024-02): Market drop -> Total = $1,800,000 (-10% drawdown), spending = $5,000 (withdrawing at a loss!)
        #   Cash $100k, Brokerage $1,300k, Real Estate $450k, Credit -$10k, Loan -$40k
        snapshots = [
            {"date": "2024-01-01", "balance": 2000000.0},
            {"date": "2024-01-31", "balance": 2000000.0},
            {"date": "2024-02-29", "balance": 1800000.0},
        ]
        snapshots_by_type = [
            {"accountType": "depository", "month": "2024-01", "balance": 100000.0},
            {"accountType": "brokerage", "month": "2024-01", "balance": 1500000.0},
            {"accountType": "real_estate", "month": "2024-01", "balance": 450000.0},
            {"accountType": "credit", "month": "2024-01", "balance": 10000.0},
            {"accountType": "loan", "month": "2024-01", "balance": 40000.0},
            {"accountType": "depository", "month": "2024-02", "balance": 100000.0},
            {"accountType": "brokerage", "month": "2024-02", "balance": 1300000.0},
            {"accountType": "real_estate", "month": "2024-02", "balance": 450000.0},
            {"accountType": "credit", "month": "2024-02", "balance": 10000.0},
            {"accountType": "loan", "month": "2024-02", "balance": 40000.0},
        ]
        account_types = [
            {"name": "depository", "group": "asset"},
            {"name": "brokerage", "group": "asset"},
            {"name": "real_estate", "group": "asset"},
            {"name": "credit", "group": "liability"},
            {"name": "loan", "group": "liability"},
        ]
        transactions = [
            {
                "date": "2024-02-15",
                "amount": -5000.0,
                "category": {"name": "Living", "group": {"type": "expense"}},
            }
        ]
        res = compute_net_worth_analytics(
            snapshots,
            snapshots_by_type=snapshots_by_type,
            account_types=account_types,
            transactions=transactions,
        )
        jan, feb = res["history"][0], res["history"][1]
        ret = res["retirement_analytics"]

        self.assertEqual(jan["investable_end_balance"], 1600000.0)
        self.assertEqual(jan["liquid_end_balance"], 1590000.0)
        self.assertEqual(feb["investable_end_balance"], 1400000.0)
        self.assertEqual(feb["liquid_end_balance"], 1390000.0)
        self.assertEqual(feb["drawdown_percent"], -10.0)
        self.assertEqual(feb["investable_drawdown_percent"], -12.5)

        self.assertTrue(ret["has_type_breakdown"])
        self.assertTrue(ret["has_transactions"])
        self.assertEqual(ret["investable_net_worth"], 1400000.0)
        self.assertEqual(ret["liquid_net_worth"], 1390000.0)
        self.assertEqual(ret["illiquid_net_worth"], 400000.0)
        self.assertEqual(ret["cash_balance"], 100000.0)
        self.assertEqual(ret["investments_balance"], 1300000.0)
        self.assertEqual(ret["real_estate_balance"], 450000.0)
        self.assertEqual(ret["liabilities_balance"], -50000.0)
        self.assertEqual(ret["max_drawdown_percent"], -10.0)
        self.assertEqual(ret["max_drawdown_month"], "2024-02")
        self.assertEqual(ret["investable_max_drawdown_percent"], -12.5)
        self.assertEqual(ret["months_withdrawing_at_loss"], 1)

    def test_classify_account(self):
        # Retirement by subtype
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "brokerage"},
                    "subtype": {"name": "ira"},
                    "displayName": "Vanguard IRA",
                }
            ),
            "retirement",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "brokerage"},
                    "subtype": {"name": "roth_ira"},
                    "displayName": "Fidelity Roth",
                }
            ),
            "retirement",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "brokerage"},
                    "subtype": {"name": "st_401k"},
                    "displayName": "Employer Plan",
                }
            ),
            "retirement",
        )
        # Retirement by displayName when subtype is generic brokerage
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "brokerage"},
                    "subtype": {"name": "brokerage"},
                    "displayName": "Schwab Rollover 401(k)",
                }
            ),
            "retirement",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": "investment",
                    "subtype": "brokerage",
                    "name": "Federal TSP Account",
                }
            ),
            "retirement",
        )
        # Taxable brokerage
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "brokerage"},
                    "subtype": {"name": "brokerage"},
                    "displayName": "Schwab Individual Brokerage",
                }
            ),
            "taxable_brokerage",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": "investment",
                    "subtype": "individual",
                    "name": "Interactive Brokers",
                }
            ),
            "taxable_brokerage",
        )
        # Depository, Credit, Loan, Other
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "depository"},
                    "subtype": {"name": "checking"},
                    "displayName": "Chase Checking",
                }
            ),
            "depository",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "credit"},
                    "subtype": {"name": "credit_card"},
                    "displayName": "Amex Platinum",
                }
            ),
            "credit",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "loan"},
                    "subtype": {"name": "mortgage"},
                    "displayName": "Home Mortgage",
                }
            ),
            "loan",
        )
        self.assertEqual(
            classify_account(
                {
                    "type": {"name": "real_estate"},
                    "subtype": {"name": "home"},
                    "displayName": "Primary Residence",
                }
            ),
            "other",
        )

    def test_taxable_and_retirement_breakdown(self):
        snapshots = [
            {"date": "2024-01-01", "balance": 1600000.0},
            {"date": "2024-01-31", "balance": 1600000.0},
            {"date": "2024-02-29", "balance": 1750000.0},
        ]
        snapshots_by_type = [
            {"accountType": "depository", "month": "2024-01", "balance": 100000.0},
            {"accountType": "brokerage", "month": "2024-01", "balance": 1500000.0},
            {"accountType": "depository", "month": "2024-02", "balance": 100000.0},
            {"accountType": "brokerage", "month": "2024-02", "balance": 1650000.0},
        ]
        # Provide account_monthly_balances for 2024-02 (600k taxable, 1,050k retirement -> sum 1,650k)
        # For 2024-01, test earliest-known-ratio distribution against 1,500k brokerage
        account_monthly_balances = {
            "2024-02": {
                "taxable_brokerage": 600000.0,
                "retirement": 1050000.0,
            }
        }
        res = compute_net_worth_analytics(
            snapshots,
            snapshots_by_type=snapshots_by_type,
            account_monthly_balances=account_monthly_balances,
        )
        jan, feb = res["history"][0], res["history"][1]

        # Feb has exact account_monthly_balances
        self.assertEqual(feb["balances_by_type"]["taxable_brokerage"], 600000.0)
        self.assertEqual(feb["balances_by_type"]["retirement"], 1050000.0)
        self.assertEqual(feb["balances_by_type"]["brokerage"], 1650000.0)
        self.assertEqual(feb["investable_end_balance"], 1750000.0)
        self.assertEqual(feb["taxable_end_balance"], 700000.0)
        self.assertEqual(feb["retirement_end_balance"], 1050000.0)
        self.assertEqual(feb["taxable_start_balance"], jan["taxable_end_balance"])
        self.assertEqual(feb["retirement_start_balance"], jan["retirement_end_balance"])

        # Jan distributed by earliest known ratio (600 / 1650 = 4/11 of 1,500,000)
        self.assertEqual(
            round(
                jan["balances_by_type"]["taxable_brokerage"]
                + jan["balances_by_type"]["retirement"],
                2,
            ),
            1500000.0,
        )
        self.assertEqual(jan["balances_by_type"]["brokerage"], 1500000.0)
        self.assertEqual(jan["investable_end_balance"], 1600000.0)
        self.assertEqual(
            jan["taxable_end_balance"],
            round(100000.0 + jan["balances_by_type"]["taxable_brokerage"], 2),
        )
        self.assertEqual(
            jan["retirement_end_balance"],
            jan["balances_by_type"]["retirement"],
        )
        self.assertEqual(jan["taxable_start_balance"], jan["taxable_end_balance"])
        self.assertEqual(jan["retirement_start_balance"], jan["retirement_end_balance"])

        # Verify summary & retirement_analytics fields
        self.assertTrue(res["summary"]["has_taxable_retirement_breakdown"])
        self.assertEqual(res["summary"]["taxable_investments_balance"], 600000.0)
        self.assertEqual(res["summary"]["retirement_balance"], 1050000.0)
        self.assertTrue(res["retirement_analytics"]["has_taxable_retirement_breakdown"])
        self.assertEqual(
            res["retirement_analytics"]["taxable_investments_balance"], 600000.0
        )
        self.assertEqual(res["retirement_analytics"]["retirement_balance"], 1050000.0)
        self.assertEqual(
            res["retirement_analytics"]["taxable_accessible_balance"], 700000.0
        )
        self.assertEqual(
            res["retirement_analytics"]["retirement_accounts_balance"], 1050000.0
        )

        # Verify daily_history and weekly_history include retirement_balance and taxable_balance
        self.assertEqual(res["daily_history"][-1]["date"], "2024-02-29")
        self.assertEqual(res["daily_history"][-1]["retirement_balance"], 1050000.0)
        self.assertEqual(res["daily_history"][-1]["taxable_balance"], 700000.0)
        self.assertEqual(res["weekly_history"][-1]["retirement_balance"], 1050000.0)
        self.assertEqual(res["weekly_history"][-1]["taxable_balance"], 700000.0)

        # Also test explicit account_daily_balances override
        account_daily_balances = {
            "2024-01-01": {"retirement_balance": 950000.0, "taxable_balance": 650000.0},
            "2024-01-31": {"retirement_balance": 960000.0, "taxable_balance": 640000.0},
            "2024-02-29": {"retirement_balance": 1050000.0, "taxable_balance": 700000.0},
        }
        res_daily = compute_net_worth_analytics(
            snapshots,
            snapshots_by_type=snapshots_by_type,
            account_monthly_balances=account_monthly_balances,
            account_daily_balances=account_daily_balances,
        )
        self.assertEqual(res_daily["daily_history"][0]["retirement_balance"], 950000.0)
        self.assertEqual(res_daily["daily_history"][0]["taxable_balance"], 650000.0)
        self.assertEqual(res_daily["weekly_history"][0]["retirement_balance"], 950000.0)
        self.assertEqual(res_daily["weekly_history"][0]["taxable_balance"], 650000.0)


if __name__ == "__main__":
    unittest.main()
