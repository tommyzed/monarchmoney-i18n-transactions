"""
Ignite FIRE Engine — Monte Carlo retirement simulation using numpy.

Provides:
- simulate(): 10,000-iteration Monte Carlo with percentile bands
- calc_fire_date(): earliest age with ≥95% portfolio survival to final age
- calc_swr(): max safe withdrawal rate with ≥95% survival to final age
- calc_retirement_probability(): % of simulations surviving to final age
"""

import numpy as np
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional

# Historical real (inflation-adjusted) return parameters by risk tolerance
# Based on S&P 500 historical data (8% real) + bond blending (3% real)
RISK_PROFILES = {
    "lean": {  # 60/40 stocks/bonds
        "mean_return": 0.06,
        "std_return": 0.10,
        "label": "Conservative (60/40)",
    },
    "moderate": {  # 80/20 stocks/bonds
        "mean_return": 0.07,
        "std_return": 0.14,
        "label": "Balanced (80/20)",
    },
    "fat": {  # 100% equity
        "mean_return": 0.08,
        "std_return": 0.18,
        "label": "Aggressive (100% Equity)",
    },
}

DEFAULT_ITERATIONS = 10_000


def _get_rng() -> np.random.Generator:
    return np.random.default_rng(seed=42)


def calculate_social_security_mba(
    pia: Optional[float],
    fra: Optional[int],
    birth_year: Optional[int],
    birth_month: Optional[int],
    withdrawal_year: Optional[int],
    withdrawal_month: Optional[int],
) -> float:
    """
    Calculate Monthly Benefit Amount (MBA) based on Social Security rules.
    """
    pia_val = pia if pia is not None else 0.0
    fra_val = fra if fra is not None else 67
    birth_y = birth_year if birth_year is not None else 1980
    birth_m = birth_month if birth_month is not None else 1
    withdraw_y = withdrawal_year if withdrawal_year is not None else 2047
    withdraw_m = withdrawal_month if withdrawal_month is not None else 1

    if pia_val <= 0:
        return 0.0

    birth_months = birth_y * 12 + birth_m
    withdrawal_months = withdraw_y * 12 + withdraw_m
    fra_months = birth_months + (fra_val * 12)

    diff_months = withdrawal_months - fra_months

    if diff_months < 0:
        # Early Retirement
        m_e = -diff_months
        if m_e <= 36:
            reduction_factor = m_e * (5.0 / 900.0)
        else:
            reduction_factor = (36.0 * (5.0 / 900.0)) + ((m_e - 36) * (5.0 / 1200.0))

        reduction_factor = min(reduction_factor, 1.0)
        mba = pia_val * (1.0 - reduction_factor)
    elif diff_months > 0:
        # Delayed Retirement
        # Capped at age 70
        age_70_months = birth_months + (70 * 12)
        effective_withdrawal_months = min(withdrawal_months, age_70_months)
        m_l = max(0, effective_withdrawal_months - fra_months)

        credit_factor = m_l * (2.0 / 300.0)
        mba = pia_val * (1.0 + credit_factor)
    else:
        mba = pia_val

    return float(round(mba))


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
    final_age: int = 85
    simulation_years: int = 0  # auto-calculated if 0
    iterations: int = DEFAULT_ITERATIONS
    social_security_enabled: bool = False
    social_security_pia: float = 0.0
    social_security_fra: int = 67
    social_security_birth_month: int = 1
    social_security_birth_year: int = 1980
    social_security_withdrawal_month: int = 1
    social_security_withdrawal_year: int = 2047
    social_security_mba: float = 0.0

    def __post_init__(self):
        if self.simulation_years == 0:
            # Simulate until final age
            self.simulation_years = max(self.final_age - self.current_age, 0)

        # Coerce None values to defaults (e.g. from legacy database rows)
        self.social_security_enabled = bool(self.social_security_enabled)
        self.social_security_pia = (
            self.social_security_pia if self.social_security_pia is not None else 0.0
        )
        self.social_security_fra = (
            self.social_security_fra if self.social_security_fra is not None else 67
        )
        self.social_security_birth_month = (
            self.social_security_birth_month
            if self.social_security_birth_month is not None
            else 1
        )
        self.social_security_birth_year = (
            self.social_security_birth_year
            if self.social_security_birth_year is not None
            else 1980
        )
        self.social_security_withdrawal_month = (
            self.social_security_withdrawal_month
            if self.social_security_withdrawal_month is not None
            else 1
        )
        self.social_security_withdrawal_year = (
            self.social_security_withdrawal_year
            if self.social_security_withdrawal_year is not None
            else 2047
        )

        self.social_security_mba = calculate_social_security_mba(
            pia=self.social_security_pia,
            fra=self.social_security_fra,
            birth_year=self.social_security_birth_year,
            birth_month=self.social_security_birth_month,
            withdrawal_year=self.social_security_withdrawal_year,
            withdrawal_month=self.social_security_withdrawal_month,
        )


@dataclass
class SimulationResult:
    """Output of the FIRE simulation."""

    years: List[int]  # age for each year
    percentile_5: List[float]  # 5th percentile portfolio values
    percentile_25: List[float]  # 25th percentile
    percentile_50: List[float]  # median
    percentile_75: List[float]  # 75th percentile
    percentile_95: List[float]  # 95th percentile
    retirement_probability: float  # % of sims surviving to final age
    fire_date_age: Optional[int]  # earliest age with ≥95% survival
    fire_date_year: Optional[int]  # calendar year of FIRE date
    swr: float  # safe withdrawal rate (%)
    required_spend_for_target: Optional[
        float
    ]  # max allowable spending to hit Target age
    current_portfolio: float  # input portfolio value
    risk_profile_label: str  # human-readable risk label
    account_breakdown: List[Dict] = field(default_factory=list)


def _generate_return_cols(profile: dict, n_iter: int, n_years: int) -> List[np.ndarray]:
    """Generate clamped (1 + r) annual return columns of shape (n_iter,) per year."""
    rng = _get_rng()
    returns = rng.normal(
        profile["mean_return"],
        profile["std_return"],
        size=(n_iter, n_years),
    )
    returns_plus_one = np.maximum(1.0 + returns, 0.0)
    return [returns_plus_one[:, yr] for yr in range(n_years)]


def _calc_ss_incomes(inp: SimulationInput, n_years: int) -> np.ndarray:
    """Precompute real (today's-dollar) Social Security income for each simulation year."""
    ss_incomes = np.zeros(n_years)
    if inp.social_security_enabled and inp.social_security_mba > 0:
        birth_months = inp.social_security_birth_year * 12 + inp.social_security_birth_month
        withdraw_months = inp.social_security_withdrawal_year * 12 + inp.social_security_withdrawal_month
        claim_age_months = withdraw_months - birth_months
        if claim_age_months >= 62 * 12:
            claim_age_year = claim_age_months // 12
            rem_months = claim_age_months % 12
            first_year_months = 12 - rem_months
            annual_ss = inp.social_security_mba * 12.0
            partial_ss = inp.social_security_mba * first_year_months
            for yr in range(n_years):
                sim_age = inp.current_age + yr
                if sim_age > claim_age_year:
                    ss_incomes[yr] = annual_ss
                elif sim_age == claim_age_year:
                    ss_incomes[yr] = partial_ss
    return ss_incomes


def simulate(inp: SimulationInput) -> SimulationResult:
    """
    Run Monte Carlo simulation in today's purchasing power (real dollars).

    Returns percentile bands of portfolio value for each year,
    plus key FIRE metrics.
    """
    profile = RISK_PROFILES.get(inp.risk_tolerance, RISK_PROFILES["moderate"])
    mean_r = profile["mean_return"]
    std_r = profile["std_return"]

    n_years = inp.simulation_years
    n_iter = inp.iterations
    years_to_retire = max(inp.retirement_age - inp.current_age, 0)

    # Generate random real annual returns once: shape (n_iter, n_years)
    rng = _get_rng()
    returns = rng.normal(mean_r, std_r, size=(n_iter, n_years))
    returns_plus_one = np.maximum(1.0 + returns, 0.0)
    return_cols = [returns_plus_one[:, yr] for yr in range(n_years)]

    current_year = datetime.now().year
    ss_incomes = _calc_ss_incomes(inp, n_years)

    # Portfolio simulation — vectorized across all iterations (in Today's $)
    portfolios = np.zeros((n_iter, n_years + 1))
    portfolios[:, 0] = inp.current_portfolio
    port = np.full(n_iter, float(inp.current_portfolio))
    alive = np.ones(n_iter, dtype=bool)

    for yr in range(n_years):
        if yr < years_to_retire:
            # Pre-retirement: annual_contribution is net annual savings/contributions
            net_cashflow = inp.annual_contribution + ss_incomes[yr]
            port = np.maximum(port * return_cols[yr] + net_cashflow, 0.0)
        else:
            # Post-retirement: once a portfolio is depleted, it stays ruined permanently
            net_cashflow = ss_incomes[yr] - inp.annual_retirement_spending
            raw_next = port * return_cols[yr] + net_cashflow
            alive &= raw_next > 0
            port = np.where(alive, raw_next, 0.0)

        portfolios[:, yr + 1] = port

    # Extract percentile bands in a single sort pass
    p5, p25, p50, p75, p95 = np.percentile(
        portfolios, [5, 25, 50, 75, 95], axis=0
    ).tolist()

    ages = [inp.current_age + yr for yr in range(n_years + 1)]

    # Calculate metrics (reusing precomputed returns when dimensions match)
    prob = _calc_retirement_probability(portfolios, inp)
    fire_age = _calc_fire_date(
        inp,
        profile,
        returns_plus_one=returns_plus_one,
        sim_portfolios=portfolios,
        return_cols=return_cols,
    )
    portfolio_at_retire = (
        portfolios[:, years_to_retire] if years_to_retire <= n_years else None
    )
    swr = _calc_swr(
        inp,
        profile,
        returns_plus_one=returns_plus_one,
        portfolio_at_retire=portfolio_at_retire,
        return_cols=return_cols,
    )
    required_spend = _calc_required_spend(
        inp,
        profile,
        returns_plus_one=returns_plus_one,
        portfolio_at_retire=portfolio_at_retire,
        return_cols=return_cols,
    )
    years_post = max(inp.final_age - inp.retirement_age, 0)
    actual_years_post = min(years_post, max(n_years - years_to_retire, 0)) if n_years > 0 else 0

    # Calendar year for FIRE date
    fire_year = None
    if fire_age is not None:
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
        required_spend_for_target=int(round(required_spend)) if actual_years_post > 0 else None,
        current_portfolio=round(inp.current_portfolio, 2),
        risk_profile_label=profile["label"],
    )


def _calc_retirement_probability(portfolios: np.ndarray, inp: SimulationInput) -> float:
    """
    % of simulations where portfolio survives (> 0) to final age
    (or end of simulation if shorter).
    """
    if inp.simulation_years == 0:
        return 100.0 if inp.current_portfolio > 0 else 0.0
    years_to_final = max(inp.final_age - inp.current_age, 0)
    target_year = min(years_to_final, inp.simulation_years)
    if target_year == 0:
        return 100.0 if inp.current_portfolio > 0 else 0.0
    survived = np.sum(portfolios[:, target_year] > 0)
    return (survived / portfolios.shape[0]) * 100


def _calc_fire_date(
    inp: SimulationInput,
    profile: dict,
    returns_plus_one: Optional[np.ndarray] = None,
    inflation_factors: Optional[np.ndarray] = None,
    sim_portfolios: Optional[np.ndarray] = None,
    return_cols: Optional[List[np.ndarray]] = None,
) -> Optional[int]:
    """
    Find the earliest retirement age (< final_age) where ≥95% of simulations survive
    to final age without depleting during retirement.

    Binary search over possible retirement ages.
    """
    if inp.current_age >= inp.final_age:
        return None

    total_years = inp.final_age - inp.current_age
    if total_years <= 0 or total_years > inp.simulation_years:
        return None

    if return_cols is None or len(return_cols) != total_years:
        if returns_plus_one is not None and returns_plus_one.shape == (
            inp.iterations,
            total_years,
        ):
            return_cols = [np.maximum(returns_plus_one[:, yr], 0.0) for yr in range(total_years)]
        else:
            return_cols = _generate_return_cols(profile, inp.iterations, total_years)

    ss_incomes = _calc_ss_incomes(inp, total_years)

    pre_retire_cashflows = np.zeros(total_years)
    post_retire_cashflows = np.zeros(total_years)
    for yr in range(total_years):
        post_income = ss_incomes[yr]
        pre_retire_cashflows[yr] = inp.annual_contribution + post_income
        post_retire_cashflows[yr] = post_income - inp.annual_retirement_spending

    # Lazily extended pre-retirement accumulation trajectory: accum_cols[yr] is
    # the 1D portfolio vector at age (current_age + yr) when working up to that age.
    sim_retire_yr = min(max(inp.retirement_age - inp.current_age, 0), total_years)
    if (
        sim_portfolios is not None
        and sim_portfolios.shape == (inp.iterations, total_years + 1)
    ):
        accum_cols: List[np.ndarray] = [
            sim_portfolios[:, yr] for yr in range(sim_retire_yr + 1)
        ]
    else:
        accum_cols = [np.full(inp.iterations, float(inp.current_portfolio))]

    def _get_accum(retire_yr: int) -> np.ndarray:
        while len(accum_cols) <= retire_yr:
            yr = len(accum_cols) - 1
            next_port = np.maximum(
                accum_cols[yr] * return_cols[yr] + pre_retire_cashflows[yr], 0.0
            )
            accum_cols.append(next_port)
        return accum_cols[retire_yr]

    def _survives(retire_yr: int) -> bool:
        port = _get_accum(retire_yr).copy()
        alive = np.ones(inp.iterations, dtype=bool)
        for yr in range(retire_yr, total_years):
            raw_next = port * return_cols[yr] + post_retire_cashflows[yr]
            alive &= raw_next > 0
            port = np.where(alive, raw_next, 0.0)
        survived = np.sum(alive) / inp.iterations
        # Round to align with the UI's probability display (which rounds to 1 decimal, i.e 94.95% -> 95.0%)
        return round(survived, 3) >= 0.950

    # Fast path: if the earliest possible age (current age) works, our FIRE date is RIGHT NOW!
    if _survives(0):
        return inp.current_age

    best_age = None
    low = 1
    high = total_years - 1

    # Narrow binary search bounds using the already-simulated target retirement age if monotonic
    if (
        sim_portfolios is not None
        and sim_portfolios.shape == (inp.iterations, total_years + 1)
        and inp.annual_contribution >= 0
        and 0 < sim_retire_yr < total_years
    ):
        target_survived = np.sum(sim_portfolios[:, total_years] > 0) / inp.iterations
        if round(target_survived, 3) >= 0.950:
            best_age = inp.current_age + sim_retire_yr
            high = sim_retire_yr - 1
        else:
            low = sim_retire_yr + 1

    while low <= high:
        mid = (low + high) // 2
        if _survives(mid):
            best_age = inp.current_age + mid
            high = mid - 1
        else:
            low = mid + 1

    return best_age


def _calc_swr(
    inp: SimulationInput,
    profile: dict,
    returns_plus_one: Optional[np.ndarray] = None,
    inflation_factors: Optional[np.ndarray] = None,
    portfolio_at_retire: Optional[np.ndarray] = None,
    return_cols: Optional[List[np.ndarray]] = None,
) -> float:
    """
    Binary search for the standalone maximum portfolio Safe Withdrawal Rate (% of
    starting retirement balance, real $) giving ≥95% permanent survival to final age.
    """
    years_to_retire = max(inp.retirement_age - inp.current_age, 0)
    years_post = max(inp.final_age - inp.retirement_age, 0)
    total_years = min(years_to_retire + years_post, 80)
    actual_years_post = max(0, min(years_post, total_years - years_to_retire))

    if actual_years_post <= 0:
        return 0.0

    needed_years = years_to_retire + actual_years_post
    if return_cols is None or len(return_cols) < needed_years:
        if returns_plus_one is not None and returns_plus_one.shape == (
            inp.iterations,
            total_years,
        ):
            return_cols = [np.maximum(returns_plus_one[:, yr], 0.0) for yr in range(total_years)]
        else:
            return_cols = _generate_return_cols(profile, inp.iterations, total_years)

    post_return_cols = return_cols[years_to_retire : years_to_retire + actual_years_post]

    # Binary search for standalone portfolio SWR on a unit $1.0 portfolio
    low, high = 0.0, 15.0
    best_swr = 0.0

    for _ in range(50):  # precision iterations
        if high - low < 1e-6:
            break
        mid = (low + high) / 2
        withdrawal_rate = mid / 100.0

        port = np.ones(inp.iterations, dtype=float)
        alive = np.ones(inp.iterations, dtype=bool)

        for yr in range(actual_years_post):
            raw_next = port * post_return_cols[yr] - withdrawal_rate
            alive &= raw_next > 0
            port = np.where(alive, raw_next, 0.0)

        survival_rate = np.sum(alive) / inp.iterations
        if round(survival_rate, 3) >= 0.950:
            best_swr = mid
            low = mid
        else:
            high = mid

    return best_swr


def _calc_required_spend(
    inp: SimulationInput,
    profile: dict,
    returns_plus_one: Optional[np.ndarray] = None,
    inflation_factors: Optional[np.ndarray] = None,
    portfolio_at_retire: Optional[np.ndarray] = None,
    return_cols: Optional[List[np.ndarray]] = None,
) -> float:
    """
    Binary search for the highest real annual retirement spending amount
    that still gives ≥95% permanent survival to final age at Target Retirement Age.
    """
    years_to_retire = max(inp.retirement_age - inp.current_age, 0)
    years_post = max(inp.final_age - inp.retirement_age, 0)
    total_years = min(years_to_retire + years_post, 80)
    actual_years_post = max(0, min(years_post, total_years - years_to_retire))

    if actual_years_post <= 0:
        return 0.0

    needed_years = years_to_retire + actual_years_post
    if return_cols is None or len(return_cols) < needed_years:
        if returns_plus_one is not None and returns_plus_one.shape == (
            inp.iterations,
            total_years,
        ):
            return_cols = [np.maximum(returns_plus_one[:, yr], 0.0) for yr in range(total_years)]
        else:
            return_cols = _generate_return_cols(profile, inp.iterations, total_years)

    ss_incomes = _calc_ss_incomes(inp, needed_years)

    if portfolio_at_retire is None or portfolio_at_retire.shape != (inp.iterations,):
        port = np.full(inp.iterations, float(inp.current_portfolio))
        for yr in range(years_to_retire):
            net_cashflow = inp.annual_contribution + ss_incomes[yr]
            port = np.maximum(port * return_cols[yr] + net_cashflow, 0.0)
        portfolio_at_retire = port

    post_return_cols = return_cols[years_to_retire : years_to_retire + actual_years_post]
    post_ss_incomes = ss_incomes[years_to_retire : years_to_retire + actual_years_post]

    low, high = 0.0, 10_000_000.0  # Search up to $10M/yr real spend
    best_spend = 0.0

    for _ in range(50):  # precision iterations
        if high - low < 0.005:
            break
        test_spend = (low + high) / 2

        port = portfolio_at_retire.copy()
        alive = np.ones(inp.iterations, dtype=bool)

        for yr in range(actual_years_post):
            net_cf = post_ss_incomes[yr] - test_spend
            raw_next = port * post_return_cols[yr] + net_cf
            alive &= raw_next > 0
            port = np.where(alive, raw_next, 0.0)

        survival_rate = np.sum(alive) / inp.iterations
        if round(survival_rate, 3) >= 0.950:
            best_spend = test_spend
            low = test_spend
        else:
            high = test_spend

    return best_spend


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
        breakdown.append(
            {
                "name": acc.get("displayName", "Unknown"),
                "balance": round(balance, 2),
                "type": acc.get("type", {}).get("display", "Other"),
                "subtype": acc.get("subtype", {}).get("display", ""),
                "isAsset": acc.get("isAsset", True),
            }
        )

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
