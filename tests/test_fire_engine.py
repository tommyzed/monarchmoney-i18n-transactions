"""
Unit tests for the Ignite FIRE Engine (Monte Carlo simulation).
"""

import os
import sys
import unittest

# Ensure project root is on path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from bridge_app.services.fire_engine import (
    SimulationInput,
    SimulationResult,
    simulate,
    filter_accounts,
    summarize_tax_buckets,
    calc_monthly_spend,
    extract_cashflow_metrics,
    calculate_social_security_mba,
    adjust_pia_for_early_retirement,
)


class TestFireEngine(unittest.TestCase):
    """Tests for the Monte Carlo simulation engine."""

    def _default_input(self, **overrides):
        defaults = dict(
            current_portfolio=500_000,
            current_age=30,
            retirement_age=55,
            annual_contribution=50_000,
            annual_retirement_spending=40_000,
            risk_tolerance="moderate",
            inflation_rate=0.03,
            iterations=1_000,  # fewer for speed in tests
        )
        defaults.update(overrides)
        return SimulationInput(**defaults)

    def test_simulate_returns_correct_shape(self):
        """Output has correct percentile bands and year count."""
        inp = self._default_input()
        result = simulate(inp)

        self.assertIsInstance(result, SimulationResult)
        # years = current_age to current_age + simulation_years (inclusive)
        expected_len = inp.simulation_years + 1
        self.assertEqual(len(result.years), expected_len)
        self.assertEqual(len(result.percentile_5), expected_len)
        self.assertEqual(len(result.percentile_25), expected_len)
        self.assertEqual(len(result.percentile_50), expected_len)
        self.assertEqual(len(result.percentile_75), expected_len)
        self.assertEqual(len(result.percentile_95), expected_len)

    def test_simulate_probability_range(self):
        """Probability score is between 0 and 100."""
        inp = self._default_input()
        result = simulate(inp)

        self.assertGreaterEqual(result.retirement_probability, 0)
        self.assertLessEqual(result.retirement_probability, 100)

    def test_swr_within_bounds(self):
        """SWR should be between 0 and 15% (sanity check)."""
        inp = self._default_input()
        result = simulate(inp)

        self.assertGreaterEqual(result.swr, 0)
        self.assertLessEqual(result.swr, 15)

    def test_fire_date_reasonable(self):
        """FIRE date age should be >= current age."""
        inp = self._default_input()
        result = simulate(inp)

        self.assertGreaterEqual(result.fire_date_age, inp.current_age)

    def test_risk_tolerance_affects_results(self):
        """Different risk tolerances should produce different median outcomes."""
        lean = simulate(self._default_input(risk_tolerance="lean"))
        moderate = simulate(self._default_input(risk_tolerance="moderate"))
        fat = simulate(self._default_input(risk_tolerance="fat"))

        # The median portfolio at the end should differ
        # More aggressive = higher expected return (on median)
        # We compare the final median value
        self.assertNotEqual(lean.percentile_50[-1], moderate.percentile_50[-1])
        self.assertNotEqual(moderate.percentile_50[-1], fat.percentile_50[-1])

    def test_zero_portfolio(self):
        """Should handle a $0 starting balance without error."""
        inp = self._default_input(current_portfolio=0)
        result = simulate(inp)

        self.assertIsInstance(result, SimulationResult)
        self.assertEqual(result.percentile_50[0], 0)

    def test_first_year_matches_input(self):
        """Year 0 of all percentiles should equal the starting portfolio."""
        inp = self._default_input()
        result = simulate(inp)

        self.assertEqual(result.percentile_5[0], inp.current_portfolio)
        self.assertEqual(result.percentile_50[0], inp.current_portfolio)
        self.assertEqual(result.percentile_95[0], inp.current_portfolio)

    def test_years_start_at_current_age(self):
        """The years list should start at the current age."""
        inp = self._default_input(current_age=35)
        result = simulate(inp)

        self.assertEqual(result.years[0], 35)

    def test_pre_retirement_accumulation_decoupled_from_retirement_spend(self):
        """Pre-retirement savings depend on annual_contribution, not annual_retirement_spending."""
        low_spend = simulate(
            self._default_input(
                current_portfolio=200_000,
                current_age=30,
                retirement_age=50,
                annual_contribution=30_000,
                annual_retirement_spending=40_000,
            )
        )
        high_spend = simulate(
            self._default_input(
                current_portfolio=200_000,
                current_age=30,
                retirement_age=50,
                annual_contribution=30_000,
                annual_retirement_spending=120_000,
            )
        )
        self.assertEqual(low_spend.percentile_50[20], high_spend.percentile_50[20])

    def test_unreachable_fire_date_returns_none(self):
        """Unreachable retirement plans must return None instead of final_age."""
        inp = self._default_input(
            current_portfolio=1_000,
            current_age=40,
            retirement_age=50,
            final_age=85,
            annual_contribution=1_000,
            annual_retirement_spending=250_000,
        )
        res = simulate(inp)
        self.assertIsNone(res.fire_date_age)


class TestFilterAccounts(unittest.TestCase):
    """Tests for the account filtering logic."""

    @classmethod
    def loadTestAccounts(cls):
        import json
        filepath = os.path.join(os.path.dirname(__file__), "get_accounts.json")
        with open(filepath) as f:
            return json.load(f)

    def test_filter_includes_net_worth_accounts(self):
        """Only accounts with includeInNetWorth=True are included."""
        data = self.loadTestAccounts()
        total, breakdown = filter_accounts(data)

        # From test data: all 7 accounts have includeInNetWorth=true
        # but the Brokerage has includeBalanceInNetWorth=false (different field)
        # We use includeInNetWorth flag
        names = [a["name"] for a in breakdown]
        self.assertIn("Checking", names)
        self.assertIn("Roth IRA", names)
        self.assertIn("401.k", names)

    def test_filter_excludes_hidden_syncdisabled(self):
        """Hidden + syncDisabled accounts are excluded."""
        data = self.loadTestAccounts()
        _, breakdown = filter_accounts(data)

        names = [a["name"] for a in breakdown]
        # Credit Card is isHidden=true AND syncDisabled=true
        self.assertNotIn("Credit Card", names)

    def test_total_is_sum_of_balances(self):
        """Total should be sum of all included account balances."""
        data = self.loadTestAccounts()
        total, breakdown = filter_accounts(data)

        expected_total = sum(a["balance"] for a in breakdown)
        self.assertAlmostEqual(total, round(expected_total, 2), places=2)

    def test_breakdown_has_type_info(self):
        """Each account in breakdown should have type and subtype."""
        data = self.loadTestAccounts()
        _, breakdown = filter_accounts(data)

        for acc in breakdown:
            self.assertIn("type", acc)
            self.assertIn("subtype", acc)
            self.assertIn("balance", acc)
            self.assertIn("name", acc)


class TestCalcMonthlySpend(unittest.TestCase):
    """Tests for monthly spend calculation."""

    def test_basic_calculation(self):
        """Correctly divides total expense by 12."""
        mock_data = {
            "summary": [
                {
                    "summary": {
                        "sumIncome": 60000,
                        "sumExpense": -48000,
                        "savings": 12000,
                        "savingsRate": 0.2,
                    }
                }
            ]
        }
        result = calc_monthly_spend(mock_data)
        self.assertEqual(result, 4000.0)

    def test_empty_data(self):
        """Returns 0 for empty cashflow data."""
        self.assertEqual(calc_monthly_spend({}), 0.0)
        self.assertEqual(calc_monthly_spend({"summary": []}), 0.0)


class TestSocialSecurity(unittest.TestCase):
    """Tests for Social Security benefit calculation and integration."""

    def test_calculate_social_security_mba_early(self):
        # FRA = 67, Birth Year = 1980, Month = 11 (Nov)
        # Withdrawal Year = 2042, Month = 11 (Nov) -> Age 62 (60 months early)
        # PIA = 3000
        # Expected Reduction: 36 * 5/900 + 24 * 5/1200 = 0.20 + 0.10 = 0.30 (30% reduction)
        # MBA = 3000 * 0.70 = 2100
        mba = calculate_social_security_mba(
            pia=3000,
            fra=67,
            birth_year=1980,
            birth_month=11,
            withdrawal_year=2042,
            withdrawal_month=11
        )
        self.assertEqual(mba, 2100)

        # Withdrawal Year = 2044, Month = 11 (Nov) -> Age 64 (36 months early)
        # Expected Reduction: 36 * 5/900 = 0.20 (20% reduction)
        # MBA = 3000 * 0.80 = 2400
        mba = calculate_social_security_mba(
            pia=3000,
            fra=67,
            birth_year=1980,
            birth_month=11,
            withdrawal_year=2044,
            withdrawal_month=11
        )
        self.assertEqual(mba, 2400)

    def test_calculate_social_security_mba_delayed(self):
        # FRA = 67, Birth Year = 1980, Month = 11 (Nov)
        # Withdrawal Year = 2050, Month = 11 (Nov) -> Age 70 (36 months late)
        # PIA = 3000
        # Expected Credit: 36 * 2/300 = 0.24 (24% increase)
        # MBA = 3000 * 1.24 = 3720
        mba = calculate_social_security_mba(
            pia=3000,
            fra=67,
            birth_year=1980,
            birth_month=11,
            withdrawal_year=2050,
            withdrawal_month=11
        )
        self.assertEqual(mba, 3720)

    def test_calculate_social_security_mba_delayed_cap(self):
        # Delayed credits cap at age 70 (36 months late for FRA 67)
        # Withdrawal Year = 2055, Month = 11 (Nov) -> Age 75 (96 months late)
        # Expected MBA should be same as age 70: 3720
        mba = calculate_social_security_mba(
            pia=3000,
            fra=67,
            birth_year=1980,
            birth_month=11,
            withdrawal_year=2055,
            withdrawal_month=11
        )
        self.assertEqual(mba, 3720)

    def test_simulate_with_social_security(self):
        # Run simulation with social security enabled vs disabled
        inp_disabled = SimulationInput(
            current_portfolio=2_000_000,
            current_age=50,
            retirement_age=60,
            annual_contribution=50_000,
            annual_retirement_spending=60_000,
            social_security_enabled=False,
            social_security_pia=3000,
            social_security_fra=67,
            social_security_birth_year=1980,
            social_security_birth_month=1,
            social_security_withdrawal_year=1980+62,
            social_security_withdrawal_month=1,
            iterations=100
        )
        res_disabled = simulate(inp_disabled)

        inp_enabled = SimulationInput(
            current_portfolio=2_000_000,
            current_age=50,
            retirement_age=60,
            annual_contribution=50_000,
            annual_retirement_spending=60_000,
            social_security_enabled=True,
            social_security_pia=3000,
            social_security_fra=67,
            social_security_birth_year=1980,
            social_security_birth_month=1,
            social_security_withdrawal_year=1980+62,
            social_security_withdrawal_month=1,
            iterations=100
        )
        res_enabled = simulate(inp_enabled)

        # Enabling Social Security should increase probability of survival/median portfolio at the end
        self.assertGreaterEqual(res_enabled.retirement_probability, res_disabled.retirement_probability)
        self.assertGreater(res_enabled.percentile_50[-1], res_disabled.percentile_50[-1])

    def test_zombie_portfolio_not_resurrected_by_ss(self):
        """A portfolio depleted during retirement must stay ruined even if SS starts later."""
        inp = SimulationInput(
            current_portfolio=50_000,
            current_age=50,
            retirement_age=50,
            annual_contribution=0,
            annual_retirement_spending=40_000,
            final_age=85,
            social_security_enabled=True,
            social_security_pia=5_000,  # $60k/yr > $40k/yr spend starting at 67
            social_security_fra=67,
            social_security_birth_year=1976,
            social_security_birth_month=1,
            social_security_withdrawal_year=1976 + 67,
            social_security_withdrawal_month=1,
            iterations=200,
        )
        res = simulate(inp)
        self.assertEqual(res.retirement_probability, 0.0)
        self.assertEqual(res.percentile_50[-1], 0)
        self.assertEqual(res.percentile_95[-1], 0)

    def test_swr_not_distorted_by_social_security(self):
        """Standalone portfolio SWR must not be inflated to 15% by Social Security."""
        base_kwargs = dict(
            current_portfolio=50_000,
            current_age=50,
            retirement_age=50,
            annual_contribution=0,
            annual_retirement_spending=40_000,
            final_age=85,
            iterations=200,
        )
        res_no_ss = simulate(SimulationInput(**base_kwargs, social_security_enabled=False))
        res_with_ss = simulate(
            SimulationInput(
                **base_kwargs,
                social_security_enabled=True,
                social_security_pia=5_000,
                social_security_fra=67,
                social_security_birth_year=1976,
                social_security_birth_month=1,
                social_security_withdrawal_year=1976 + 67,
                social_security_withdrawal_month=1,
            )
        )
        self.assertLess(res_with_ss.swr, 8.0)
        self.assertEqual(res_with_ss.swr, res_no_ss.swr)

    def test_ss_aligned_by_claiming_age(self):
        """SS cashflows start at the claiming age regardless of birth calendar year."""
        res = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=60,
                retirement_age=60,
                final_age=75,
                annual_contribution=0,
                annual_retirement_spending=50_000,
                social_security_enabled=True,
                social_security_pia=3_000,
                social_security_fra=67,
                social_security_birth_year=1950,
                social_security_birth_month=1,
                social_security_withdrawal_year=1950 + 65,
                social_security_withdrawal_month=1,
                iterations=100,
            )
        )
        res_no_ss = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=60,
                retirement_age=60,
                final_age=75,
                annual_contribution=0,
                annual_retirement_spending=50_000,
                social_security_enabled=False,
                iterations=100,
            )
        )
        # Before Age 65 (indices 0..5 correspond to Ages 60..65 start), trajectories match
        self.assertEqual(res.percentile_50[5], res_no_ss.percentile_50[5])
        # After Age 65 claiming year (index 6 = end of Age 65), SS has increased the portfolio
        self.assertGreater(res.percentile_50[6], res_no_ss.percentile_50[6])

    def test_simulation_input_coercion_handles_nones(self):
        # Verify that SimulationInput coerces all potential NoneType values to default values without raising exceptions
        inp = SimulationInput(
            current_portfolio=500_000,
            current_age=45,
            retirement_age=65,
            annual_contribution=50_000,
            annual_retirement_spending=40_000,
            social_security_enabled=None,
            social_security_pia=None,
            social_security_fra=None,
            social_security_birth_year=None,
            social_security_birth_month=None,
            social_security_withdrawal_year=None,
            social_security_withdrawal_month=None
        )
        self.assertEqual(inp.social_security_enabled, False)
        self.assertEqual(inp.social_security_pia, 0.0)
        self.assertEqual(inp.social_security_fra, 67)
        self.assertEqual(inp.social_security_birth_year, 1980)
        self.assertEqual(inp.social_security_birth_month, 1)
        self.assertEqual(inp.social_security_withdrawal_year, 2047)
        self.assertEqual(inp.social_security_withdrawal_month, 1)
        self.assertEqual(inp.social_security_mba, 0.0)


class TestPhase2FireFeatures(unittest.TestCase):
    """Tests for Phase 2 FIRE engine features and API What-If overrides."""

    def test_tax_bucket_classification_and_summary(self):
        """Accounts from Monarch fixture are bucketed and summarized accurately."""
        import json
        filepath = os.path.join(os.path.dirname(__file__), "get_accounts.json")
        with open(filepath) as f:
            data = json.load(f)

        _, breakdown = filter_accounts(data)
        by_name = {a["name"]: a for a in breakdown}
        self.assertEqual(by_name["401.k"]["taxBucket"], "pre_tax")
        self.assertEqual(by_name["Roth IRA"]["taxBucket"], "tax_free")
        self.assertEqual(by_name["Brokerage"]["taxBucket"], "taxable")
        self.assertEqual(by_name["Checking"]["taxBucket"], "taxable")

        summary = summarize_tax_buckets(breakdown)
        self.assertAlmostEqual(summary["pre_tax"], 100000.35, places=2)
        self.assertAlmostEqual(summary["tax_free"], 10000.43, places=2)
        self.assertGreater(summary["taxable"], 100000.0)
        self.assertGreater(summary["suggested_effective_tax_rate"], 0.0)
        self.assertLess(summary["suggested_effective_tax_rate"], 0.18)

    def test_effective_tax_rate_reduces_safe_spend(self):
        """Higher withdrawal tax drag reduces sustainable after-tax retirement spending."""
        base = SimulationInput(
            current_portfolio=1_000_000,
            current_age=55,
            retirement_age=55,
            final_age=85,
            annual_contribution=0,
            annual_retirement_spending=40_000,
            effective_tax_rate=0.0,
            iterations=200,
        )
        taxed = SimulationInput(
            current_portfolio=1_000_000,
            current_age=55,
            retirement_age=55,
            final_age=85,
            annual_contribution=0,
            annual_retirement_spending=40_000,
            effective_tax_rate=0.20,
            iterations=200,
        )
        res_base = simulate(base)
        res_taxed = simulate(taxed)
        self.assertLess(res_taxed.required_spend_for_target, res_base.required_spend_for_target)
        self.assertLess(res_taxed.percentile_50[-1], res_base.percentile_50[-1])

    def test_pre_medicare_healthcare_bridge_before_and_after_65(self):
        """Pre-65 healthcare increases withdrawals only prior to Age 65."""
        no_hc = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=60,
                retirement_age=60,
                final_age=70,
                annual_contribution=0,
                annual_retirement_spending=40_000,
                pre_medicare_healthcare=0,
                iterations=200,
            )
        )
        with_hc = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=60,
                retirement_age=60,
                final_age=70,
                annual_contribution=0,
                annual_retirement_spending=40_000,
                pre_medicare_healthcare=15_000,
                iterations=200,
            )
        )
        # Age 60..64 are affected, lowering Age 65 portfolio
        self.assertLess(with_hc.percentile_50[5], no_hc.percentile_50[5])

        # Retiring at 65+ should not incur pre-65 healthcare cost
        at_65_no_hc = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=65,
                retirement_age=65,
                final_age=75,
                annual_contribution=0,
                annual_retirement_spending=40_000,
                pre_medicare_healthcare=0,
                iterations=200,
            )
        )
        at_65_with_hc = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=65,
                retirement_age=65,
                final_age=75,
                annual_contribution=0,
                annual_retirement_spending=40_000,
                pre_medicare_healthcare=15_000,
                iterations=200,
            )
        )
        self.assertEqual(at_65_no_hc.percentile_50[-1], at_65_with_hc.percentile_50[-1])

    def test_post_75_spend_shift(self):
        """Negative Age 75+ spend shift preserves more portfolio by final age."""
        flat = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=70,
                retirement_age=70,
                final_age=85,
                annual_contribution=0,
                annual_retirement_spending=50_000,
                post_75_spend_change=0.0,
                iterations=200,
            )
        )
        slow_go = simulate(
            SimulationInput(
                current_portfolio=1_000_000,
                current_age=70,
                retirement_age=70,
                final_age=85,
                annual_contribution=0,
                annual_retirement_spending=50_000,
                post_75_spend_change=-0.20,
                iterations=200,
            )
        )
        # Ages 70..74 identical (index 5 = start of Age 75)
        self.assertEqual(flat.percentile_50[5], slow_go.percentile_50[5])
        # By Age 85, Slow-Go spending reduction yields a higher portfolio
        self.assertGreater(slow_go.percentile_50[-1], flat.percentile_50[-1])

    def test_adjust_pia_for_early_retirement_bend_points(self):
        """SSA bend-point adjuster reduces PIA progressively (but less than linearly) for early FIRE."""
        # Full 35+ working years (Age 57+) keeps PIA unchanged
        self.assertEqual(adjust_pia_for_early_retirement(3000, 57), 3000.0)
        # Retiring at Age 42 (20/35 years) reduces PIA, but progressive bend points cushion the cut
        adj_42 = adjust_pia_for_early_retirement(3000, 42)
        self.assertGreater(adj_42, 3000 * (20 / 35))
        self.assertLess(adj_42, 3000)
        # Retiring at Age 22 (0 years) yields $0
        self.assertEqual(adjust_pia_for_early_retirement(3000, 22), 0.0)

    def test_extract_cashflow_metrics_trims_outliers(self):
        """IQR upper-fence trims one-off spike months from typical monthly spend."""
        cashflow_data = {
            "summary": [
                {
                    "summary": {
                        "sumIncome": 120000,
                        "sumExpense": -72000,
                        "savings": 48000,
                        "savingsRate": 0.4,
                    }
                }
            ]
        }
        # 11 normal months around $5,000 + 1 spike month of $17,000
        monthly = [4900, 5100, 5000, 5200, 4800, 5050, 4950, 5150, 5000, 4850, 5000, 17000]
        metrics = extract_cashflow_metrics(cashflow_data, monthly_expenses=monthly)
        self.assertEqual(metrics["raw_monthly_spend_avg"], 6000.0)
        self.assertEqual(metrics["outlier_months_trimmed"], 1)
        self.assertAlmostEqual(metrics["monthly_spend_avg"], 5000.0, places=2)
        self.assertEqual(metrics["annual_savings_avg"], 48000)

    def test_simulate_endpoint_applies_whatif_overrides_without_mutating_db(self):
        """POST /api/fire/simulate applies request settings transiently without saving to DB."""
        from unittest.mock import AsyncMock, MagicMock, patch
        from fastapi.testclient import TestClient
        from bridge_app import main

        fake_mm = MagicMock()
        fake_mm.get_accounts = AsyncMock(
            return_value={
                "accounts": [
                    {
                        "displayName": "Roth IRA",
                        "displayBalance": 600_000,
                        "includeInNetWorth": True,
                        "isHidden": False,
                        "syncDisabled": False,
                        "isAsset": True,
                        "type": {"display": "Investments"},
                        "subtype": {"name": "roth", "display": "Roth IRA"},
                    }
                ]
            }
        )
        fake_mm.get_cashflow_summary = AsyncMock(
            return_value={
                "summary": [
                    {"summary": {"sumIncome": 100_000, "sumExpense": -60_000, "savings": 40_000, "savingsRate": 0.4}}
                ]
            }
        )
        fake_creds = MagicMock(id=1)

        with (
            patch.object(main, "UNLOCK_SECRET", "test_secret"),
            patch.object(main, "COOKIE_VALUE", "test_cookie_val"),
            patch.object(main, "get_latest_credentials", AsyncMock(return_value=fake_creds)),
            patch.object(main, "get_monarch_client", AsyncMock(return_value=fake_mm)),
        ):
            client = TestClient(main.app)
            client.cookies.set(main.DEVICE_TOKEN_COOKIE, "test_cookie_val")

            # Persist baseline spending = 45000
            put_res = client.put(
                "/api/fire/settings",
                json={"annual_retirement_spending": 45000, "effective_tax_rate": 0.05},
            )
            self.assertEqual(put_res.status_code, 200)

            # Simulate transient What-If override = 70000 / 0.15 tax
            sim_res = client.post(
                "/api/fire/simulate",
                json={
                    "settings": {"annual_retirement_spending": 70000, "effective_tax_rate": 0.15},
                    "is_demo": False,
                },
            )
            self.assertEqual(sim_res.status_code, 200)
            body = sim_res.json()
            self.assertEqual(body["settings"]["annual_retirement_spending"], 70000)
            self.assertAlmostEqual(body["settings"]["effective_tax_rate"], 0.15)
            self.assertEqual(body["saved_settings"]["annual_retirement_spending"], 45000)
            self.assertIn("tax_bucket_summary", body)
            self.assertIn("cashflow_metrics", body)

            # Confirm DB baseline remained 45000
            get_res = client.get("/api/fire/settings")
            self.assertEqual(get_res.status_code, 200)
            self.assertEqual(get_res.json()["annual_retirement_spending"], 45000)
            self.assertAlmostEqual(get_res.json()["effective_tax_rate"], 0.05)

    def test_dynamic_tax_scales_with_withdrawal_size(self):
        """Higher withdrawal / Safe Spend pushes effective tax rate up via brackets & NIIT."""
        breakdown = [
            {"name": "401k", "balance": 1_500_000, "isAsset": True, "taxBucket": "pre_tax"},
            {"name": "Brokerage", "balance": 3_500_000, "isAsset": True, "taxBucket": "taxable"},
        ]
        modest_inp = SimulationInput(
            current_portfolio=5_000_000,
            current_age=45,
            retirement_age=55,
            annual_contribution=20_000,
            annual_retirement_spending=60_000,
            tax_regime="us_federal",
            iterations=100,
        )
        fat_inp = SimulationInput(
            current_portfolio=5_000_000,
            current_age=45,
            retirement_age=55,
            annual_contribution=20_000,
            annual_retirement_spending=300_000,
            tax_regime="us_federal",
            iterations=100,
        )
        modest_sum = summarize_tax_buckets(breakdown, inp=modest_inp, safe_spend=60_000)
        fat_sum = summarize_tax_buckets(breakdown, inp=fat_inp, safe_spend=300_000)
        self.assertGreater(fat_sum["suggested_effective_tax_rate"], modest_sum["suggested_effective_tax_rate"])
        self.assertLess(modest_sum["suggested_effective_tax_rate"], 0.03)
        self.assertGreater(fat_sum["suggested_effective_tax_rate"], 0.12)

    def test_tax_regimes_ordering(self):
        """State & German regimes order sensibly and DE Abgeltung reflects 26.375% on gains."""
        breakdown = [
            {"name": "401k", "balance": 600_000, "isAsset": True, "taxBucket": "pre_tax"},
            {"name": "Brokerage", "balance": 2_400_000, "isAsset": True, "taxBucket": "taxable"},
        ]

        def _rate_for(regime):
            inp = SimulationInput(
                current_portfolio=3_000_000,
                current_age=40,
                retirement_age=55,
                annual_contribution=30_000,
                annual_retirement_spending=180_000,
                tax_regime=regime,
                iterations=100,
            )
            return summarize_tax_buckets(breakdown, inp=inp, safe_spend=180_000)["suggested_effective_tax_rate"]

        r_us_fed = _rate_for("us_federal")
        r_us_mod = _rate_for("us_mod_state")
        r_us_high = _rate_for("us_high_state")
        r_de_etf = _rate_for("de_etf")
        r_de_abg = _rate_for("de_abgeltung")

        self.assertLess(r_us_fed, r_us_mod)
        self.assertLess(r_us_mod, r_us_high)
        self.assertLess(r_de_etf, r_de_abg)
        self.assertGreater(r_de_abg, 0.18)

    def test_auto_tax_rate_is_idempotent(self):
        """Re-running simulation with the suggested auto tax rate yields the same suggested rate."""
        breakdown = [
            {"name": "401k", "balance": 500_000, "isAsset": True, "taxBucket": "pre_tax"},
            {"name": "Roth IRA", "balance": 300_000, "isAsset": True, "taxBucket": "tax_free"},
            {"name": "Brokerage", "balance": 2_200_000, "isAsset": True, "taxBucket": "taxable"},
        ]
        inp1 = SimulationInput(
            current_portfolio=3_000_000,
            current_age=42,
            retirement_age=55,
            annual_contribution=40_000,
            annual_retirement_spending=120_000,
            effective_tax_rate=0.0,
            tax_regime="us_high_state",
            iterations=200,
        )
        res1 = simulate(inp1)
        sum1 = summarize_tax_buckets(breakdown, inp=inp1, safe_spend=res1.required_spend_for_target)
        tau = sum1["suggested_effective_tax_rate"]

        inp2 = SimulationInput(
            current_portfolio=3_000_000,
            current_age=42,
            retirement_age=55,
            annual_contribution=40_000,
            annual_retirement_spending=120_000,
            effective_tax_rate=tau,
            tax_regime="us_high_state",
            iterations=200,
        )
        res2 = simulate(inp2)
        sum2 = summarize_tax_buckets(breakdown, inp=inp2, safe_spend=res2.required_spend_for_target)
        self.assertAlmostEqual(sum2["suggested_effective_tax_rate"], tau, places=2)


if __name__ == "__main__":
    unittest.main()
