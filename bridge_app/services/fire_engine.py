"""
Ignite FIRE Engine — Monte Carlo retirement simulation using numpy.

Provides:
- simulate(): 10,000-iteration Monte Carlo with percentile bands
- calc_fire_date(): earliest age with ≥95% portfolio survival
- calc_swr(): max safe withdrawal rate with ≥95% survival
- calc_retirement_probability(): % of simulations surviving 30yr post-retirement
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Dict, Any

# Historical return parameters by risk tolerance
# Based on S&P 500 historical data + bond blending
RISK_PROFILES = {
    "lean": {     # 60/40 stocks/bonds
        "mean_return": 0.06,
        "std_return": 0.10,
        "label": "Conservative (60/40)",
    },
    "moderate": { # 80/20 stocks/bonds
        "mean_return": 0.07,
        "std_return": 0.14,
        "label": "Balanced (80/20)",
    },
    "fat": {      # 100% equity
        "mean_return": 0.08,
        "std_return": 0.18,
        "label": "Aggressive (100% Equity)",
    },
}

DEFAULT_ITERATIONS = 10_000


@dataclass
class SimulationInput:
    """Input parameters for the FIRE simulation."""
    current_portfolio: float
    current_age: int
    retirement_age: int
    annual_contribution: float
    annual_retirement_spending: float
    risk_tolerance: str = "moderate"
    inflation_rate: float = 0.03
    simulation_years: int = 0  # auto-calculated if 0
    iterations: int = DEFAULT_ITERATIONS

    def __post_init__(self):
        if self.simulation_years == 0:
            # Simulate until age 85 or at least 30 years post-retirement
            years_to_85 = max(85 - self.current_age, 1)
            years_post_retire = max((self.retirement_age - self.current_age) + 30, 1)
            self.simulation_years = max(years_to_85, years_post_retire)


@dataclass
class SimulationResult:
    """Output of the FIRE simulation."""
    years: List[int]                    # age for each year
    percentile_5: List[float]           # 5th percentile portfolio values
    percentile_25: List[float]          # 25th percentile
    percentile_50: List[float]          # median
    percentile_75: List[float]          # 75th percentile
    percentile_95: List[float]          # 95th percentile
    retirement_probability: float       # % of sims surviving 30yr post-retire
    fire_date_age: int                  # earliest age with ≥95% survival
    fire_date_year: int                 # calendar year of FIRE date
    swr: float                          # safe withdrawal rate (%)
    current_portfolio: float            # input portfolio value
    risk_profile_label: str             # human-readable risk label
    account_breakdown: List[Dict] = field(default_factory=list)


def simulate(inp: SimulationInput) -> SimulationResult:
    """
    Run Monte Carlo simulation.

    Returns percentile bands of portfolio value for each year,
    plus key FIRE metrics.
    """
    profile = RISK_PROFILES.get(inp.risk_tolerance, RISK_PROFILES["moderate"])
    mean_r = profile["mean_return"]
    std_r = profile["std_return"]

    n_years = inp.simulation_years
    n_iter = inp.iterations
    years_to_retire = max(inp.retirement_age - inp.current_age, 0)

    # Generate random annual returns: shape (n_iter, n_years)
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(mean_r, std_r, size=(n_iter, n_years))

    # Portfolio simulation — vectorized across all iterations
    portfolios = np.zeros((n_iter, n_years + 1))
    portfolios[:, 0] = inp.current_portfolio

    for yr in range(n_years):
        age = inp.current_age + yr
        # Inflation-adjusted spending (grows each year)
        inflation_factor = (1 + inp.inflation_rate) ** yr

        if yr < years_to_retire:
            # Pre-retirement: contributing, no withdrawals
            contribution = inp.annual_contribution * inflation_factor
            withdrawal = 0.0
        else:
            # Post-retirement: no contributions, spending down
            contribution = 0.0
            withdrawal = inp.annual_retirement_spending * inflation_factor

        # Apply market return, then net cash flow
        portfolios[:, yr + 1] = (
            portfolios[:, yr] * (1 + returns[:, yr])
            + contribution
            - withdrawal
        )
        # Floor at zero (can't go negative in real life)
        portfolios[:, yr + 1] = np.maximum(portfolios[:, yr + 1], 0)

    # Extract percentile bands
    p5 = np.percentile(portfolios, 5, axis=0).tolist()
    p25 = np.percentile(portfolios, 25, axis=0).tolist()
    p50 = np.percentile(portfolios, 50, axis=0).tolist()
    p75 = np.percentile(portfolios, 75, axis=0).tolist()
    p95 = np.percentile(portfolios, 95, axis=0).tolist()

    ages = [inp.current_age + yr for yr in range(n_years + 1)]

    # Calculate metrics
    prob = _calc_retirement_probability(portfolios, years_to_retire, n_years)
    fire_age = _calc_fire_date(inp, profile)
    swr = _calc_swr(inp, profile)

    # Calendar year for FIRE date
    from datetime import datetime
    current_year = datetime.now().year
    fire_year = current_year + (fire_age - inp.current_age)

    return SimulationResult(
        years=ages,
        percentile_5=_round_list(p5),
        percentile_25=_round_list(p25),
        percentile_50=_round_list(p50),
        percentile_75=_round_list(p75),
        percentile_95=_round_list(p95),
        retirement_probability=round(prob, 1),
        fire_date_age=fire_age,
        fire_date_year=fire_year,
        swr=round(swr, 2),
        current_portfolio=round(inp.current_portfolio, 2),
        risk_profile_label=profile["label"],
    )


def _calc_retirement_probability(
    portfolios: np.ndarray,
    years_to_retire: int,
    n_years: int,
) -> float:
    """
    % of simulations where portfolio is > 0 at 30 years post-retirement
    (or end of simulation if shorter).
    """
    target_year = min(years_to_retire + 30, n_years)
    survived = np.sum(portfolios[:, target_year] > 0)
    return (survived / portfolios.shape[0]) * 100


def _calc_fire_date(inp: SimulationInput, profile: dict) -> int:
    """
    Find the earliest retirement age where ≥95% of simulations survive
    for 30 years post-retirement.

    Binary search over possible retirement ages.
    """
    best_age = inp.current_age + inp.simulation_years  # worst case

    for test_age in range(inp.current_age, inp.current_age + inp.simulation_years - 30 + 1):
        years_to_retire = test_age - inp.current_age
        years_post = 30
        total_years = years_to_retire + years_post

        if total_years > inp.simulation_years:
            break

        rng = np.random.default_rng(seed=42)
        returns = rng.normal(
            profile["mean_return"], profile["std_return"],
            size=(inp.iterations, total_years)
        )

        portfolios = np.zeros((inp.iterations, total_years + 1))
        portfolios[:, 0] = inp.current_portfolio

        for yr in range(total_years):
            inflation_factor = (1 + inp.inflation_rate) ** yr
            if yr < years_to_retire:
                contribution = inp.annual_contribution * inflation_factor
                withdrawal = 0.0
            else:
                contribution = 0.0
                withdrawal = inp.annual_retirement_spending * inflation_factor

            portfolios[:, yr + 1] = (
                portfolios[:, yr] * (1 + returns[:, yr])
                + contribution - withdrawal
            )
            portfolios[:, yr + 1] = np.maximum(portfolios[:, yr + 1], 0)

        survived = np.sum(portfolios[:, -1] > 0) / inp.iterations
        if survived >= 0.95:
            best_age = test_age
            break

    return best_age


def _calc_swr(inp: SimulationInput, profile: dict) -> float:
    """
    Binary search for the maximum withdrawal rate (as % of portfolio at retirement)
    that gives ≥95% survival over 30 years.
    """
    years_to_retire = max(inp.retirement_age - inp.current_age, 0)
    total_years = years_to_retire + 30

    if total_years > 80:
        total_years = 80

    # First, simulate to get portfolio value at retirement
    rng = np.random.default_rng(seed=42)
    returns = rng.normal(
        profile["mean_return"], profile["std_return"],
        size=(inp.iterations, total_years)
    )

    # Accumulation phase portfolio value
    portfolios_accum = np.zeros((inp.iterations, years_to_retire + 1))
    portfolios_accum[:, 0] = inp.current_portfolio

    for yr in range(years_to_retire):
        inflation_factor = (1 + inp.inflation_rate) ** yr
        contribution = inp.annual_contribution * inflation_factor
        portfolios_accum[:, yr + 1] = (
            portfolios_accum[:, yr] * (1 + returns[:, yr]) + contribution
        )
        portfolios_accum[:, yr + 1] = np.maximum(portfolios_accum[:, yr + 1], 0)

    portfolio_at_retire = portfolios_accum[:, -1]

    # Binary search for SWR
    low, high = 0.0, 15.0
    best_swr = 0.0

    for _ in range(50):  # precision iterations
        mid = (low + high) / 2
        withdrawal_rate = mid / 100

        # Simulate 30 years of withdrawals
        port = portfolio_at_retire.copy()
        survived = True

        for yr in range(30):
            post_yr = years_to_retire + yr
            if post_yr >= total_years:
                break
            inflation_factor = (1 + inp.inflation_rate) ** post_yr
            annual_withdrawal = portfolio_at_retire * withdrawal_rate * (
                (1 + inp.inflation_rate) ** yr
            )
            port = port * (1 + returns[:, post_yr]) - annual_withdrawal
            port = np.maximum(port, 0)

        survival_rate = np.sum(port > 0) / inp.iterations
        if survival_rate >= 0.95:
            best_swr = mid
            low = mid
        else:
            high = mid

    return best_swr


def filter_accounts(accounts_data: dict) -> tuple:
    """
    Filter Monarch accounts by includeInNetWorth.
    Returns (total_balance, account_breakdown_list).
    """
    accounts = accounts_data.get("accounts", [])
    total = 0.0
    breakdown = []

    for acc in accounts:
        if not acc.get("includeInNetWorth", False):
            continue
        if acc.get("isHidden", False) and acc.get("syncDisabled", False):
            continue

        # Some accounts (like manual holdings) use displayBalance for the true value
        balance = acc.get("displayBalance") or acc.get("currentBalance", 0) or 0
        total += balance
        breakdown.append({
            "name": acc.get("displayName", "Unknown"),
            "balance": round(balance, 2),
            "type": acc.get("type", {}).get("display", "Other"),
            "subtype": acc.get("subtype", {}).get("display", ""),
            "isAsset": acc.get("isAsset", True),
        })

    # Sort: assets first (descending), then liabilities
    breakdown.sort(key=lambda x: (-int(x["isAsset"]), -abs(x["balance"])))

    return round(total, 2), breakdown


def calc_monthly_spend(cashflow_data: dict) -> float:
    """
    Extract average monthly expense from cashflow summary.
    The data covers 12 months, so divide total by 12.
    """
    summaries = cashflow_data.get("summary", [])
    if not summaries:
        return 0.0

    total_expense = 0.0
    for s in summaries:
        summary = s.get("summary", {})
        total_expense += abs(summary.get("sumExpense", 0) or 0)

    return round(total_expense / 12, 2) if total_expense else 0.0


def _round_list(values: list, decimals: int = 0) -> list:
    """Round a list of floats."""
    return [round(v, decimals) for v in values]
