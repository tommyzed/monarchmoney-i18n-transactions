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
    calc_monthly_spend,
    calculate_social_security_mba,
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


if __name__ == "__main__":
    unittest.main()
