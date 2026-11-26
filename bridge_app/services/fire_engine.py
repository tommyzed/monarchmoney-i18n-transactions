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


def adjust_pia_for_early_retirement(
    pia: Optional[float],
    retirement_age: Optional[int],
) -> float:
    """
    Adjust an SSA statement Primary Insurance Amount (PIA) for early retirement
    zero-earning years in the 35-year AIME window (assuming career start at Age 22)
    using SSA bend points.
    """
    if pia is None or pia <= 0:
        return 0.0

    ret_age = retirement_age if retirement_age is not None else 65
    worked_years = max(0, min(35, int(ret_age) - 22))
    if worked_years >= 35:
        return float(round(pia))
    if worked_years == 0:
        return 0.0

    b1 = 1226.0
    b2 = 7391.0
    p1 = 0.90 * b1
    p2 = p1 + 0.32 * (b2 - b1)

    if pia <= p1:
        implied_aime = pia / 0.90
    elif pia <= p2:
        implied_aime = b1 + (pia - p1) / 0.32
    else:
        implied_aime = b2 + (pia - p2) / 0.15

    adj_aime = implied_aime * (worked_years / 35.0)

    if adj_aime <= b1:
        adj_pia = 0.90 * adj_aime
    elif adj_aime <= b2:
        adj_pia = p1 + 0.32 * (adj_aime - b1)
    else:
        adj_pia = p2 + 0.15 * (adj_aime - b2)

    return float(round(adj_pia))


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
    effective_tax_rate: float = 0.0
    pre_medicare_healthcare: float = 0.0
    post_75_spend_change: float = 0.0
    ss_stop_work_adjust: bool = False
    tax_regime: str = "us_federal"
    effective_ss_pia: float = 0.0

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
        self.effective_tax_rate = min(
            0.50,
            max(0.0, float(self.effective_tax_rate if self.effective_tax_rate is not None else 0.0)),
        )
        self.pre_medicare_healthcare = max(
            0.0,
            float(self.pre_medicare_healthcare if self.pre_medicare_healthcare is not None else 0.0),
        )
        self.post_75_spend_change = min(
            1.0,
            max(-0.50, float(self.post_75_spend_change if self.post_75_spend_change is not None else 0.0)),
        )
        self.ss_stop_work_adjust = bool(self.ss_stop_work_adjust)
        valid_regimes = {"us_federal", "us_mod_state", "us_high_state", "de_abgeltung", "de_etf"}
        self.tax_regime = self.tax_regime if self.tax_regime in valid_regimes else "us_federal"

        self.effective_ss_pia = (
            adjust_pia_for_early_retirement(self.social_security_pia, self.retirement_age)
            if self.ss_stop_work_adjust
            else float(self.social_security_pia)
        )

        self.social_security_mba = calculate_social_security_mba(
            pia=self.effective_ss_pia,
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
    effective_ss_pia: float = 0.0
    effective_ss_mba: float = 0.0


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


def _calc_ss_incomes(
    inp: SimulationInput,
    n_years: int,
    override_retirement_age: Optional[int] = None,
) -> np.ndarray:
    """Precompute real (today's-dollar) Social Security income for each simulation year."""
    ss_incomes = np.zeros(n_years)
    if not inp.social_security_enabled:
        return ss_incomes

    if override_retirement_age is not None and inp.ss_stop_work_adjust:
        eff_pia = adjust_pia_for_early_retirement(inp.social_security_pia, override_retirement_age)
        mba = calculate_social_security_mba(
            pia=eff_pia,
            fra=inp.social_security_fra,
            birth_year=inp.social_security_birth_year,
            birth_month=inp.social_security_birth_month,
            withdrawal_year=inp.social_security_withdrawal_year,
            withdrawal_month=inp.social_security_withdrawal_month,
        )
    else:
        mba = inp.social_security_mba

    if mba > 0:
        birth_months = inp.social_security_birth_year * 12 + inp.social_security_birth_month
        withdraw_months = inp.social_security_withdrawal_year * 12 + inp.social_security_withdrawal_month
        claim_age_months = withdraw_months - birth_months
        if claim_age_months >= 62 * 12:
            claim_age_year = claim_age_months // 12
            rem_months = claim_age_months % 12
            first_year_months = 12 - rem_months
            annual_ss = mba * 12.0
            partial_ss = mba * first_year_months
            for yr in range(n_years):
                sim_age = inp.current_age + yr
                if sim_age > claim_age_year:
                    ss_incomes[yr] = annual_ss
                elif sim_age == claim_age_year:
                    ss_incomes[yr] = partial_ss
    return ss_incomes


def _post_retire_cashflow_for_age(
    sim_age: int,
    base_spend: float,
    ss_income: float,
    effective_tax_rate: float,
    pre_medicare_healthcare: float,
    post_75_spend_change: float,
) -> float:
    """
    Compute net portfolio cashflow for one retirement year after applying
    Age 75+ spending phase shift, pre-65 healthcare bridge, and withdrawal tax drag.
    """
    phase_factor = (1.0 + post_75_spend_change) if sim_age >= 75 else 1.0
    hc_adder = pre_medicare_healthcare if sim_age < 65 else 0.0
    total_after_tax_spend = max(0.0, base_spend * phase_factor + hc_adder)
    if total_after_tax_spend > ss_income:
        gross_withdrawal = (total_after_tax_spend - ss_income) / max(1.0 - effective_tax_rate, 0.01)
        return -gross_withdrawal
    return ss_income - total_after_tax_spend


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
            sim_age = inp.current_age + yr
            net_cashflow = _post_retire_cashflow_for_age(
                sim_age=sim_age,
                base_spend=inp.annual_retirement_spending,
                ss_income=float(ss_incomes[yr]),
                effective_tax_rate=inp.effective_tax_rate,
                pre_medicare_healthcare=inp.pre_medicare_healthcare,
                post_75_spend_change=inp.post_75_spend_change,
            )
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
        effective_ss_pia=inp.effective_ss_pia,
        effective_ss_mba=inp.social_security_mba if inp.social_security_enabled else 0.0,
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
        cand_retire_age = inp.current_age + retire_yr
        ss_vec = (
            _calc_ss_incomes(inp, total_years, override_retirement_age=cand_retire_age)
            if (inp.ss_stop_work_adjust and inp.social_security_enabled)
            else ss_incomes
        )
        port = _get_accum(retire_yr).copy()
        alive = np.ones(inp.iterations, dtype=bool)
        for yr in range(retire_yr, total_years):
            sim_age = inp.current_age + yr
            net_cf = _post_retire_cashflow_for_age(
                sim_age=sim_age,
                base_spend=inp.annual_retirement_spending,
                ss_income=float(ss_vec[yr]),
                effective_tax_rate=inp.effective_tax_rate,
                pre_medicare_healthcare=inp.pre_medicare_healthcare,
                post_75_spend_change=inp.post_75_spend_change,
            )
            raw_next = port * return_cols[yr] + net_cf
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
            sim_age = inp.current_age + years_to_retire + yr
            net_cf = _post_retire_cashflow_for_age(
                sim_age=sim_age,
                base_spend=test_spend,
                ss_income=float(post_ss_incomes[yr]),
                effective_tax_rate=inp.effective_tax_rate,
                pre_medicare_healthcare=inp.pre_medicare_healthcare,
                post_75_spend_change=inp.post_75_spend_change,
            )
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


_TAX_FREE_SUBTYPES = {
    "roth",
    "roth_401k",
    "tfsa",
    "health_savings_account",
    "health_reimbursement_arrangement",
    "non_taxable_brokerage_account",
}

_PRE_TAX_SUBTYPES = {
    "st_401k",
    "st_401a",
    "st_403b",
    "st_457b",
    "ira",
    "sep_ira",
    "simple_ira",
    "sarsep_pension",
    "keogh_plan",
    "pension",
    "retirement",
    "profit_sharing_plan",
    "thrift_savings_plan",
    "sipp",
    "rrsp",
    "rrif",
    "lif",
    "lira",
    "lrif",
    "lrsp",
    "prif",
    "rlif",
}


def classify_tax_bucket(acc: dict) -> str:
    """Classify a Monarch account into 'tax_free', 'pre_tax', or 'taxable'."""
    subtype_info = acc.get("subtype") or {}
    subtype_name = (subtype_info.get("name") or "").lower()
    subtype_display = (subtype_info.get("display") or "").lower()
    display_name = (acc.get("displayName") or acc.get("name") or "").lower()
    label = f"{subtype_display} {display_name}"

    if subtype_name in _TAX_FREE_SUBTYPES or any(
        kw in label for kw in ("roth", "hsa", "health savings", "tfsa")
    ):
        return "tax_free"

    if subtype_name in _PRE_TAX_SUBTYPES or any(
        kw in label
        for kw in (
            "401k",
            "401(k)",
            "401.k",
            "403b",
            "403(b)",
            "457",
            "ira",
            "pension",
            "tsp",
            "rrsp",
            "rrif",
        )
    ):
        return "pre_tax"

    return "taxable"


def estimate_gain_fraction(inp: Optional[SimulationInput]) -> float:
    """
    Estimate the taxable capital-gain fraction of brokerage withdrawals based on
    projected real portfolio compounding until retirement and mid-retirement.
    """
    if inp is None:
        return 0.65

    profile = RISK_PROFILES.get(inp.risk_tolerance, RISK_PROFILES["moderate"])
    g = max(0.01, profile["mean_return"] - 0.5 * (profile["std_return"] ** 2))
    y_pre = max(inp.retirement_age - inp.current_age, 0)
    p0 = max(float(inp.current_portfolio), 0.0)
    c = max(float(inp.annual_contribution), 0.0)

    growth_pre = (1.0 + g) ** y_pre
    annuity_pre = ((growth_pre - 1.0) / g) if y_pre > 0 else 0.0
    expected_port_retire = p0 * growth_pre + c * annuity_pre
    expected_basis_retire = 0.55 * p0 + c * y_pre

    mid_retire_yrs = min(8, max((inp.final_age - inp.retirement_age) // 2, 0))
    mid_growth = (1.0 + g) ** mid_retire_yrs

    if expected_port_retire <= 0:
        return 0.65

    basis_ratio = expected_basis_retire / (expected_port_retire * mid_growth)
    return min(0.92, max(0.35, 1.0 - basis_ratio))


def _progressive_tax(amount: float, brackets: List[tuple]) -> float:
    """Compute progressive tax across ascending (upper_bound, marginal_rate) brackets."""
    if amount <= 0:
        return 0.0
    tax = 0.0
    prev = 0.0
    for upper, rate in brackets:
        if amount <= prev:
            break
        slice_amt = min(amount, upper) - prev
        if slice_amt > 0:
            tax += slice_amt * rate
        prev = upper
    return tax


def estimate_withdrawal_tax(
    gross_withdrawal: float,
    pre_tax_share: float,
    taxable_share: float,
    gain_fraction: float,
    regime: str = "us_federal",
    annual_ss: float = 0.0,
) -> float:
    """
    Estimate annual income & capital-gains tax on a gross portfolio withdrawal
    under US or German tax rules (in Today's $).
    """
    if gross_withdrawal <= 0:
        return 0.0

    pre_w = gross_withdrawal * max(0.0, pre_tax_share)
    realized_gains = gross_withdrawal * max(0.0, taxable_share) * min(1.0, max(0.0, gain_fraction))

    if regime in ("de_abgeltung", "de_etf"):
        de_ord_brackets = [
            (12800.0, 0.0),
            (19000.0, 0.19),
            (38000.0, 0.28),
            (73000.0, 0.36),
            (305000.0, 0.443),
            (float("inf"), 0.475),
        ]
        ss_ord = 0.85 * max(0.0, annual_ss)
        de_ord_tax = _progressive_tax(ss_ord + pre_w, de_ord_brackets) - _progressive_tax(
            ss_ord, de_ord_brackets
        )
        tfs_factor = 0.70 if regime == "de_etf" else 1.0
        de_cap_tax = 0.26375 * max(0.0, realized_gains * tfs_factor - 1100.0)
        return de_ord_tax + de_cap_tax

    # US Federal (+ optional state)
    std_ded = 15000.0
    ss_taxable = 0.85 * max(0.0, annual_ss)
    ord_ss_only = max(0.0, ss_taxable - std_ded)
    ord_total = max(0.0, pre_w + ss_taxable - std_ded)

    fed_ord_brackets = [
        (11925.0, 0.10),
        (48475.0, 0.12),
        (103350.0, 0.22),
        (197300.0, 0.24),
        (250525.0, 0.32),
        (626350.0, 0.35),
        (float("inf"), 0.37),
    ]
    fed_ord_tax = _progressive_tax(ord_total, fed_ord_brackets) - _progressive_tax(
        ord_ss_only, fed_ord_brackets
    )

    rem_ded = max(0.0, std_ded - (pre_w + ss_taxable))
    taxable_gains = max(0.0, realized_gains - rem_ded)
    fed_ltcg_brackets = [
        (48350.0, 0.0),
        (533400.0, 0.15),
        (float("inf"), 0.20),
    ]
    fed_ltcg_tax = _progressive_tax(ord_total + taxable_gains, fed_ltcg_brackets) - _progressive_tax(
        ord_total, fed_ltcg_brackets
    )

    magi = pre_w + ss_taxable + realized_gains
    niit = 0.038 * min(realized_gains, max(0.0, magi - 200000.0))

    state_tax = 0.0
    if regime == "us_mod_state":
        state_tax = 0.05 * max(0.0, pre_w + realized_gains - 10000.0)
    elif regime == "us_high_state":
        ca_brackets = [
            (10756.0, 0.01),
            (25499.0, 0.02),
            (40245.0, 0.04),
            (55866.0, 0.06),
            (70606.0, 0.08),
            (360659.0, 0.093),
            (432787.0, 0.103),
            (721314.0, 0.113),
            (1000000.0, 0.123),
            (float("inf"), 0.133),
        ]
        state_tax = _progressive_tax(max(0.0, pre_w + realized_gains - 5540.0), ca_brackets)

    return fed_ord_tax + fed_ltcg_tax + niit + state_tax


def summarize_tax_buckets(
    breakdown: List[Dict],
    inp: Optional[SimulationInput] = None,
    safe_spend: Optional[float] = None,
) -> dict:
    """
    Aggregate positive account balances by tax bucket and compute a bracket-aware,
    compounded-gain-adjusted effective withdrawal tax rate.
    """
    pre_tax = 0.0
    tax_free = 0.0
    taxable = 0.0

    for acc in breakdown:
        bal = float(acc.get("balance") or 0.0)
        if bal <= 0:
            continue
        bucket = acc.get("taxBucket") or classify_tax_bucket(acc)
        if bucket == "pre_tax":
            pre_tax += bal
        elif bucket == "tax_free":
            tax_free += bal
        else:
            taxable += bal

    total_pos = pre_tax + tax_free + taxable
    gain_fraction = estimate_gain_fraction(inp)
    regime = inp.tax_regime if inp is not None else "us_federal"

    if total_pos <= 0:
        return {
            "pre_tax": 0.0,
            "tax_free": 0.0,
            "taxable": 0.0,
            "suggested_effective_tax_rate": 0.0,
            "tax_regime": regime,
            "estimated_gain_fraction": round(gain_fraction, 3),
            "reference_gross_withdrawal": 0,
        }

    pre_share = pre_tax / total_pos
    tax_share = taxable / total_pos
    annual_ss = (inp.social_security_mba * 12.0) if (inp is not None and inp.social_security_enabled) else 0.0

    if inp is not None:
        cur_tau = min(0.49, max(0.0, float(inp.effective_tax_rate)))
        gross_from_safe = (
            max(0.0, (float(safe_spend) - annual_ss) / (1.0 - cur_tau))
            if (safe_spend is not None and safe_spend > annual_ss)
            else 0.0
        )
        planned_net = max(0.0, float(inp.annual_retirement_spending) - annual_ss)
        g_plan = planned_net
        for _ in range(6):
            g_plan = planned_net + estimate_withdrawal_tax(
                g_plan, pre_share, tax_share, gain_fraction, regime, annual_ss
            )

        if gross_from_safe <= 0.0 and planned_net <= 0.0:
            ref_gross = 0.0
        else:
            ref_gross = max(gross_from_safe, g_plan, 1000.0)
    else:
        ref_gross = max(total_pos * 0.04, 40000.0)

    if ref_gross > 0:
        est_tax = estimate_withdrawal_tax(
            ref_gross, pre_share, tax_share, gain_fraction, regime, annual_ss
        )
        suggested_rate = round(min(0.50, max(0.0, est_tax / ref_gross)), 3)
    else:
        suggested_rate = 0.0

    return {
        "pre_tax": round(pre_tax, 2),
        "tax_free": round(tax_free, 2),
        "taxable": round(taxable, 2),
        "suggested_effective_tax_rate": suggested_rate,
        "tax_regime": regime,
        "estimated_gain_fraction": round(gain_fraction, 3),
        "reference_gross_withdrawal": round(ref_gross),
    }


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
                "taxBucket": classify_tax_bucket(acc),
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


def extract_cashflow_metrics(
    cashflow_data: dict,
    monthly_expenses: Optional[List[float]] = None,
) -> dict:
    """
    Extract trailing 12-month cashflow metrics with optional IQR-based upper-outlier
    trimming when monthly spending observations are available.
    """
    summaries = cashflow_data.get("summary", []) if cashflow_data else []
    total_expense = 0.0
    total_income = 0.0
    total_savings = 0.0
    savings_rate = 0.0

    for s in summaries:
        summary = s.get("summary", {})
        total_expense += abs(summary.get("sumExpense", 0) or 0)
        total_income += summary.get("sumIncome", 0) or 0
        total_savings += summary.get("savings", 0) or 0
        if summary.get("savingsRate") is not None:
            savings_rate = summary.get("savingsRate", 0) or 0

    raw_monthly_spend = round(total_expense / 12.0, 2) if total_expense else 0.0
    cleaned_monthly_spend = raw_monthly_spend
    outlier_months_trimmed = 0

    valid_months = [float(x) for x in (monthly_expenses or []) if x is not None and float(x) > 0]
    if len(valid_months) >= 4:
        arr = np.array(valid_months, dtype=float)
        q25, q50, q75 = np.percentile(arr, [25, 50, 75])
        iqr = max(q75 - q25, q50 * 0.15)
        fence = max(q75 + 1.75 * iqr, q50 * 1.35)
        inliers = arr[arr <= fence]
        if 0 < len(inliers) < len(arr):
            outlier_months_trimmed = int(len(arr) - len(inliers))
            cleaned_monthly_spend = round(float(np.mean(inliers)), 2)
        elif raw_monthly_spend == 0.0:
            cleaned_monthly_spend = round(float(np.mean(arr)), 2)

    return {
        "monthly_spend_avg": cleaned_monthly_spend,
        "raw_monthly_spend_avg": raw_monthly_spend,
        "annual_savings_avg": round(max(total_savings, 0.0)),
        "annual_income_avg": round(max(total_income, 0.0)),
        "savings_rate": round(float(savings_rate or 0.0), 3),
        "outlier_months_trimmed": outlier_months_trimmed,
    }


def _round_list(values: list, decimals: int = 0) -> list:
    """Round a list of floats."""
    return [round(v, decimals) for v in values]
