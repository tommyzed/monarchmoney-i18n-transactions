import asyncio
import io
import logging
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from cachetools import TTLCache

logger = logging.getLogger("net_worth_service")
_SCHEMA_INITIALIZED = False
_NET_WORTH_PNG_CACHE = TTLCache(maxsize=8, ttl=600)

_RETIREMENT_SUBTYPES = {
    # US Defined Contribution & Retirement
    "st_401k",
    "401k",
    "st_401a",
    "401a",
    "st_403b",
    "403b",
    "st_457b",
    "457b",
    "roth_401k",
    "ira",
    "roth",
    "roth_ira",
    "traditional_ira",
    "rollover_ira",
    "inherited_ira",
    "sep_ira",
    "simple_ira",
    "sarsep_pension",
    "keogh_plan",
    "pension",
    "retirement",
    "profit_sharing_plan",
    "thrift_savings_plan",
    "tsp",
    "sipp",
    # Canadian Registered Retirement Accounts
    "rrsp",
    "rrif",
    "lif",
    "lira",
    "lrif",
    "lrsp",
    "prif",
    "rlif",
    # Tax-Free / Non-taxable
    "non_taxable_brokerage_account",
    "tfsa",
    # Health & Education Tax-Advantaged
    "health_savings_account",
    "hsa",
    "health_reimbursement_arrangement",
    "st_529",
    "529",
    "education_savings_account",
}

_RETIREMENT_KEYWORDS = [
    "401k",
    "401(k)",
    "403b",
    "403(b)",
    "457",
    "roth",
    "sep ira",
    "simple ira",
    "traditional ira",
    "rollover ira",
    "inherited ira",
    "pension",
    "retirement",
    "superannuation",
    "thrift savings",
    "rrsp",
    "rrif",
    "lira",
    "lif",
    "tfsa",
    "hsa",
    "529",
    "health savings",
]

_RETIREMENT_NAME_RE = re.compile(
    r"(?:^|\b)(ira|roth|401k|401\(k\)|403b|403\(b\)|457b?|sep\s+ira|simple\s+ira|pension|retirement|tsp|thrift\s+savings|hsa|529|rrsp|rrif|tfsa)(?:\b|(?=\s|$|[^a-z0-9]))",
    re.IGNORECASE,
)


def classify_account(account: Optional[Dict[str, Any]]) -> str:
    """
    Classify a Monarch account into:
    - 'retirement': tax-sheltered / retirement account (IRA, Roth, 401k, 403b, 457b, Pension, TSP, HSA, 529, etc.)
    - 'taxable_brokerage': non-retirement brokerage / investment account
    - 'depository': cash / bank account
    - 'credit': credit card / line of credit
    - 'loan': mortgage / loan
    - 'other': real estate, vehicle, valuables, or other account types
    """
    if not isinstance(account, dict):
        return "other"

    raw_type = account.get("type")
    if isinstance(raw_type, dict):
        type_name = str(raw_type.get("name") or "").strip().lower()
        type_display = str(raw_type.get("display") or "").strip().lower()
    else:
        type_name = str(raw_type or "").strip().lower()
        type_display = ""

    raw_subtype = account.get("subtype")
    if isinstance(raw_subtype, dict):
        subtype_name = str(raw_subtype.get("name") or "").strip().lower()
        subtype_display = str(raw_subtype.get("display") or "").strip().lower()
    else:
        subtype_name = str(raw_subtype or "").strip().lower()
        subtype_display = ""

    display_name = (
        str(account.get("displayName") or account.get("name") or "").strip().lower()
    )

    if (
        type_name == "credit"
        or "credit card" in type_display
        or subtype_name in {"credit_card", "line_of_credit"}
    ):
        return "credit"

    if (
        type_name == "loan"
        or "loan" in type_display
        or subtype_name in {"mortgage", "auto", "student", "loan", "home_equity"}
    ):
        return "loan"

    if type_name == "retirement" or subtype_name in _RETIREMENT_SUBTYPES:
        return "retirement"

    for kw in _RETIREMENT_KEYWORDS:
        if kw in subtype_display:
            return "retirement"

    if type_name not in {"depository", "cash", "real_estate", "vehicle", "valuables"}:
        if _RETIREMENT_NAME_RE.search(display_name):
            return "retirement"

    if (
        type_name in {"brokerage", "investment"}
        or "investment" in type_display
        or "brokerage" in type_display
        or subtype_name
        in {
            "brokerage",
            "taxable_brokerage",
            "individual",
            "joint",
            "stock_plan",
            "crypto",
            "mutual_fund",
        }
    ):
        return "taxable_brokerage"

    if (
        type_name in {"depository", "cash"}
        or "cash" in type_display
        or subtype_name
        in {
            "checking",
            "savings",
            "cd",
            "money_market",
            "cash_management",
            "prepaid",
            "paypal",
        }
    ):
        return "depository"

    return "other"


async def _retry_monarch_call(func, *args, max_retries: int = 4, initial_delay: float = 1.5, **kwargs):
    """Execute a Monarch API call with automatic retries on transient errors."""
    delay = initial_delay
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_err = e
            logger.warning(
                f"Monarch call {getattr(func, '__name__', str(func))} attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay:.1f}s..."
            )
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2
    raise last_err


def compute_net_worth_analytics(
    snapshots: List[Dict[str, Any]],
    start_date_filter: str = "2024-01-01",
    updated_at_iso: Optional[str] = None,
    snapshots_by_type: Optional[List[Dict[str, Any]]] = None,
    account_types: Optional[List[Dict[str, Any]]] = None,
    transactions: Optional[List[Dict[str, Any]]] = None,
    categories: Optional[List[Dict[str, Any]]] = None,
    account_monthly_balances: Optional[Dict[str, Dict[str, float]]] = None,
    account_daily_balances: Optional[Dict[str, Dict[str, float]]] = None,
) -> Dict[str, Any]:
    """
    Pure calculation helper that computes monthly and cumulative net worth performance
    analytics, account-type scopes (investable/liquid/all), return decomposition
    (interest/dividends/cap gains/price change/withdrawals), Modified Dietz TWR,
    and retirement drawdown & withdrawal rate metrics.
    """
    if not snapshots:
        return {
            "summary": {},
            "history": [],
            "weekly_history": [],
            "daily_history": [],
        }

    valid_snapshots = [
        s
        for s in snapshots
        if isinstance(s, dict)
        and isinstance(s.get("date"), str)
        and len(s["date"]) >= 7
        and s["date"] >= start_date_filter
        and s.get("balance") is not None
    ]

    if not valid_snapshots:
        return {
            "summary": {},
            "history": [],
            "weekly_history": [],
            "daily_history": [],
        }

    sorted_snapshots = sorted(valid_snapshots, key=lambda s: s["date"])

    initial_balance = float(sorted_snapshots[0]["balance"])
    first_date = sorted_snapshots[0]["date"]
    latest_date = sorted_snapshots[-1]["date"]

    # Build account-type monthly lookup (liability types normalized to negative)
    liability_types = {"credit", "loan", "other_liability"}
    for at in account_types or []:
        if (
            isinstance(at, dict)
            and str(at.get("group") or "").strip().lower() == "liability"
            and at.get("name")
        ):
            liability_types.add(str(at["name"]).strip())

    by_month_type: Dict[str, Dict[str, float]] = {}
    for item in snapshots_by_type or []:
        if not isinstance(item, dict):
            continue
        m_raw = str(item.get("month") or item.get("date") or "")
        if len(m_raw) < 7:
            continue
        m_key = m_raw[:7]
        acc_type = str(item.get("accountType") or "").strip()
        if not acc_type or item.get("balance") is None:
            continue
        try:
            raw_bal = float(item["balance"])
        except (TypeError, ValueError):
            continue
        signed_bal = -abs(raw_bal) if acc_type in liability_types else raw_bal
        by_month_type.setdefault(m_key, {})[acc_type] = round(signed_bal, 2)

    # Build monthly transaction buckets for return decomposition
    categories_map: Dict[str, Dict[str, Any]] = {
        str(c["id"]): c
        for c in (categories or [])
        if isinstance(c, dict) and c.get("id") is not None
    }
    start_month_filter = start_date_filter[:7] if start_date_filter else ""
    tx_by_month: Dict[str, Dict[str, float]] = {}

    for tx in transactions or []:
        if not isinstance(tx, dict):
            continue
        tx_date = str(tx.get("date") or "")
        if len(tx_date) < 7:
            continue
        m_key = tx_date[:7]
        if start_month_filter and m_key < start_month_filter:
            continue
        if tx.get("hideFromReports") is True or tx.get("hiddenFromReports") is True:
            continue
        try:
            amount = float(tx.get("amount") or 0.0)
        except (TypeError, ValueError):
            continue
        if amount == 0.0:
            continue

        cat_info = tx.get("category") or {}
        cat_id = str(cat_info.get("id") or "")
        full_cat = categories_map.get(cat_id, {})
        cat_name = (
            full_cat.get("name") or cat_info.get("name") or "Uncategorized"
        ).strip()
        cat_name_clean = cat_name.lower()
        group = full_cat.get("group") or cat_info.get("group") or {}
        group_type = (group.get("type") or "").strip().lower()

        if group_type == "transfer" or cat_name_clean in {
            "transfer",
            "transfers",
            "credit card payment",
        }:
            continue

        bucket = tx_by_month.setdefault(
            m_key,
            {
                "interest": 0.0,
                "dividends": 0.0,
                "capital_gains": 0.0,
                "other_income": 0.0,
                "raw_spending": 0.0,
            },
        )

        is_income_tx = group_type == "income" or (
            group_type not in ("expense", "transfer") and amount > 0
        )
        is_expense_tx = group_type == "expense" or (
            group_type not in ("income", "transfer") and amount < 0
        )

        if cat_name_clean in {"interest", "interest income"} and is_income_tx:
            bucket["interest"] += abs(amount)
        elif (
            cat_name_clean
            in {
                "dividends",
                "dividend",
                "dividend & cap gains",
                "dividends & capital gains",
            }
            and is_income_tx
        ):
            bucket["dividends"] += abs(amount)
        elif cat_name_clean in {"capital gains", "capital gain"} and is_income_tx:
            bucket["capital_gains"] += abs(amount)
        elif is_income_tx:
            bucket["other_income"] += amount
        elif is_expense_tx:
            bucket["raw_spending"] += -amount

    # Group snapshots by calendar month (YYYY-MM)
    monthly_groups: Dict[str, List[Dict[str, Any]]] = {}
    for snap in sorted_snapshots:
        month_key = snap["date"][:7]
        monthly_groups.setdefault(month_key, []).append(snap)

    history: List[Dict[str, Any]] = []
    prev_end_balance = initial_balance
    prev_investable_end_balance = initial_balance
    prev_liquid_end_balance = initial_balance
    prev_taxable_end_balance = 0.0
    prev_retirement_end_balance = 0.0
    cum_twr_factor = 1.0
    inv_cum_twr_factor = 1.0
    running_peak = 0.0
    inv_running_peak = 0.0

    # Determine earliest known taxable / retirement ratio from account_monthly_balances
    known_taxable_ratio: Optional[float] = None
    if account_monthly_balances:
        for m_k in sorted(account_monthly_balances.keys()):
            m_vals = account_monthly_balances.get(m_k) or {}
            t_v = max(0.0, float(m_vals.get("taxable_brokerage") or 0.0))
            r_v = max(0.0, float(m_vals.get("retirement") or 0.0))
            if t_v + r_v > 0:
                known_taxable_ratio = t_v / (t_v + r_v)
                break

    for idx, month_key in enumerate(sorted(monthly_groups.keys())):
        month_snaps = monthly_groups[month_key]
        max_snap = max(month_snaps, key=lambda s: s["date"])
        end_date = max_snap["date"]
        end_balance = float(max_snap["balance"])

        start_balance = initial_balance if idx == 0 else prev_end_balance
        gain_amount = end_balance - start_balance
        gain_percent = (
            ((end_balance - start_balance) / start_balance) * 100.0
            if start_balance != 0
            else 0.0
        )
        cumulative_gain_amount = end_balance - initial_balance
        cumulative_gain_percent = (
            ((end_balance - initial_balance) / initial_balance) * 100.0
            if initial_balance != 0
            else 0.0
        )

        start_bal_r = round(start_balance, 2)
        end_bal_r = round(end_balance, 2)
        gain_amt_r = round(gain_amount, 2)

        # Account-type scope balances for this month
        balances_by_type = dict(by_month_type.get(month_key, {}))
        amb_month = (account_monthly_balances or {}).get(month_key)
        if amb_month is not None:
            raw_taxable = max(0.0, float(amb_month.get("taxable_brokerage") or 0.0))
            raw_ret = max(0.0, float(amb_month.get("retirement") or 0.0))
            raw_sum = raw_taxable + raw_ret
            if raw_sum > 0:
                known_taxable_ratio = raw_taxable / raw_sum
            if "brokerage" in balances_by_type:
                brok_val = float(balances_by_type["brokerage"])
                if raw_sum > 0:
                    taxable_val = round(brok_val * (raw_taxable / raw_sum), 2)
                    ret_val = round(brok_val - taxable_val, 2)
                elif brok_val > 0 and known_taxable_ratio is not None:
                    taxable_val = round(brok_val * known_taxable_ratio, 2)
                    ret_val = round(brok_val - taxable_val, 2)
                else:
                    taxable_val = round(raw_taxable, 2)
                    ret_val = round(raw_ret, 2)
            else:
                taxable_val = round(raw_taxable, 2)
                ret_val = round(raw_ret, 2)
            balances_by_type["taxable_brokerage"] = taxable_val
            balances_by_type["retirement"] = ret_val
            balances_by_type["brokerage"] = round(taxable_val + ret_val, 2)
        elif (
            known_taxable_ratio is not None
            and "brokerage" in balances_by_type
            and float(balances_by_type["brokerage"]) > 0
        ):
            brok_val = float(balances_by_type["brokerage"])
            taxable_val = round(brok_val * known_taxable_ratio, 2)
            ret_val = round(brok_val - taxable_val, 2)
            balances_by_type["taxable_brokerage"] = taxable_val
            balances_by_type["retirement"] = ret_val
            balances_by_type["brokerage"] = round(taxable_val + ret_val, 2)

        if balances_by_type:
            investable_end_balance = round(
                balances_by_type.get("depository", 0.0)
                + balances_by_type.get("brokerage", 0.0),
                2,
            )
            liquid_end_balance = round(
                investable_end_balance + balances_by_type.get("credit", 0.0),
                2,
            )
            taxable_end_balance = round(
                balances_by_type.get("depository", 0.0)
                + balances_by_type.get("taxable_brokerage", 0.0),
                2,
            )
            retirement_end_balance = round(
                balances_by_type.get("retirement", 0.0),
                2,
            )
        else:
            investable_end_balance = end_bal_r
            liquid_end_balance = end_bal_r
            taxable_end_balance = 0.0
            retirement_end_balance = 0.0

        if idx == 0:
            if not balances_by_type:
                investable_start_balance = start_bal_r
                liquid_start_balance = start_bal_r
                taxable_start_balance = 0.0
                retirement_start_balance = 0.0
            else:
                inv_start_est = round(investable_end_balance - gain_amt_r, 2)
                if inv_start_est > 0:
                    investable_start_balance = inv_start_est
                elif end_bal_r != 0:
                    investable_start_balance = round(
                        start_bal_r * (investable_end_balance / end_bal_r), 2
                    )
                else:
                    investable_start_balance = start_bal_r

                liq_start_est = round(liquid_end_balance - gain_amt_r, 2)
                if liq_start_est > 0:
                    liquid_start_balance = liq_start_est
                elif end_bal_r != 0:
                    liquid_start_balance = round(
                        start_bal_r * (liquid_end_balance / end_bal_r), 2
                    )
                else:
                    liquid_start_balance = start_bal_r

                if end_bal_r != 0:
                    taxable_start_balance = round(
                        taxable_end_balance
                        - (taxable_end_balance / end_bal_r * gain_amt_r),
                        2,
                    )
                    retirement_start_balance = round(
                        retirement_end_balance
                        - (retirement_end_balance / end_bal_r * gain_amt_r),
                        2,
                    )
                else:
                    taxable_start_balance = taxable_end_balance
                    retirement_start_balance = retirement_end_balance
        else:
            investable_start_balance = prev_investable_end_balance
            liquid_start_balance = prev_liquid_end_balance
            taxable_start_balance = prev_taxable_end_balance
            retirement_start_balance = prev_retirement_end_balance

        # Monthly return decomposition
        tx_bucket = tx_by_month.get(month_key, {})
        interest = round(tx_bucket.get("interest", 0.0), 2)
        dividends = round(tx_bucket.get("dividends", 0.0), 2)
        capital_gains = round(tx_bucket.get("capital_gains", 0.0), 2)
        inv_income = round(interest + dividends + capital_gains, 2)
        other_income = round(tx_bucket.get("other_income", 0.0), 2)
        spending = round(max(0.0, tx_bucket.get("raw_spending", 0.0)), 2)
        net_external_flow = round(other_income - spending, 2)

        total_return_dollars = round(gain_amt_r - net_external_flow, 2)
        price_change_dollars = round(total_return_dollars - inv_income, 2)

        # Modified Dietz TWR (All Accounts)
        denom = start_bal_r + 0.5 * net_external_flow
        twr_frac = (
            (end_bal_r - start_bal_r - net_external_flow) / denom
            if denom > 0
            else 0.0
        )
        twr_percent = round(twr_frac * 100.0, 2)
        cum_twr_factor *= 1.0 + twr_frac
        cumulative_twr_percent = round((cum_twr_factor - 1.0) * 100.0, 2)

        # Peak & Drawdown (All Accounts)
        running_peak = max(running_peak, start_bal_r, end_bal_r)
        drawdown_percent = (
            round(((end_bal_r / running_peak) - 1.0) * 100.0, 2)
            if running_peak > 0
            else 0.0
        )

        # Investable Portfolio Return Decomposition, TWR & Drawdown
        investable_gain_amount = round(
            investable_end_balance - investable_start_balance, 2
        )
        investable_total_return_dollars = round(
            investable_gain_amount - net_external_flow, 2
        )
        investable_price_change_dollars = round(
            investable_total_return_dollars - inv_income, 2
        )
        inv_denom = investable_start_balance + 0.5 * net_external_flow
        inv_twr_frac = (
            (
                investable_end_balance
                - investable_start_balance
                - net_external_flow
            )
            / inv_denom
            if inv_denom > 0
            else 0.0
        )
        investable_twr_percent = round(inv_twr_frac * 100.0, 2)
        inv_cum_twr_factor *= 1.0 + inv_twr_frac
        investable_cumulative_twr_percent = round(
            (inv_cum_twr_factor - 1.0) * 100.0, 2
        )
        inv_running_peak = max(
            inv_running_peak, investable_start_balance, investable_end_balance
        )
        investable_drawdown_percent = (
            round(((investable_end_balance / inv_running_peak) - 1.0) * 100.0, 2)
            if inv_running_peak > 0
            else 0.0
        )

        history.append(
            {
                "month": month_key,
                "start_balance": start_bal_r,
                "end_balance": end_bal_r,
                "gain_amount": gain_amt_r,
                "gain_percent": round(gain_percent, 2),
                "cumulative_gain_amount": round(cumulative_gain_amount, 2),
                "cumulative_gain_percent": round(cumulative_gain_percent, 2),
                "end_date": end_date,
                "interest": interest,
                "dividends": dividends,
                "capital_gains": capital_gains,
                "inv_income": inv_income,
                "other_income": other_income,
                "spending": spending,
                "net_external_flow": net_external_flow,
                "total_return_dollars": total_return_dollars,
                "price_change_dollars": price_change_dollars,
                "twr_percent": twr_percent,
                "cumulative_twr_percent": cumulative_twr_percent,
                "drawdown_percent": drawdown_percent,
                "investable_start_balance": investable_start_balance,
                "investable_end_balance": investable_end_balance,
                "investable_gain_amount": investable_gain_amount,
                "investable_total_return_dollars": investable_total_return_dollars,
                "investable_price_change_dollars": investable_price_change_dollars,
                "investable_twr_percent": investable_twr_percent,
                "investable_cumulative_twr_percent": investable_cumulative_twr_percent,
                "investable_drawdown_percent": investable_drawdown_percent,
                "liquid_start_balance": liquid_start_balance,
                "liquid_end_balance": liquid_end_balance,
                "taxable_start_balance": taxable_start_balance,
                "taxable_end_balance": taxable_end_balance,
                "retirement_start_balance": retirement_start_balance,
                "retirement_end_balance": retirement_end_balance,
                "balances_by_type": balances_by_type,
            }
        )

        prev_end_balance = end_balance
        prev_investable_end_balance = investable_end_balance
        prev_liquid_end_balance = liquid_end_balance
        prev_taxable_end_balance = taxable_end_balance
        prev_retirement_end_balance = retirement_end_balance

    final_balance = float(sorted_snapshots[-1]["balance"])
    current_net_worth = round(final_balance, 2)
    starting_net_worth = round(initial_balance, 2)
    total_gain_amount = round(final_balance - initial_balance, 2)
    total_gain_percent = (
        round(((final_balance - initial_balance) / initial_balance) * 100.0, 2)
        if initial_balance != 0
        else 0.0
    )

    peak_entry = max(history, key=lambda h: h["end_balance"])
    best_entry = max(history, key=lambda h: h["gain_percent"])
    worst_entry = min(history, key=lambda h: h["gain_percent"])
    avg_monthly_gain_percent = (
        round(sum(h["gain_percent"] for h in history) / len(history), 2)
        if history
        else 0.0
    )

    num_months = len(history)
    if num_months > 0 and starting_net_worth > 0 and current_net_worth > 0:
        annualized_gain_percent = round(
            ((current_net_worth / starting_net_worth) ** (12.0 / num_months) - 1.0) * 100.0,
            2,
        )
    elif num_months > 0 and starting_net_worth != 0:
        annualized_gain_percent = round(total_gain_percent * (12.0 / num_months), 2)
    else:
        annualized_gain_percent = 0.0

    history_by_month: Dict[str, Dict[str, Any]] = {
        h["month"]: h for h in history
    }

    def _resolve_daily_scoped_balances(date_str: str, total_bal: float) -> tuple[float, float]:
        if account_daily_balances and date_str in account_daily_balances:
            d_info = account_daily_balances[date_str] or {}
            d_ret = float(
                d_info.get("retirement_balance")
                if "retirement_balance" in d_info
                else d_info.get("retirement", 0.0)
            )
            if "taxable_balance" in d_info:
                d_tax = float(d_info["taxable_balance"])
            elif "taxable_brokerage" in d_info:
                d_tax = float(d_info["taxable_brokerage"])
            else:
                d_tax = max(0.0, total_bal - d_ret)
            return round(d_ret, 2), round(d_tax, 2)

        m_h = history_by_month.get(date_str[:7])
        if m_h and (
            m_h.get("retirement_end_balance", 0.0) > 0
            or m_h.get("taxable_end_balance", 0.0) > 0
        ):
            m_end_bal = float(m_h.get("end_balance") or 0.0)
            m_ret_end = float(m_h.get("retirement_end_balance") or 0.0)
            m_tax_end = float(m_h.get("taxable_end_balance") or 0.0)
            if date_str == str(m_h.get("end_date") or "")[:10] or m_end_bal <= 0:
                return round(m_ret_end, 2), round(m_tax_end, 2)
            ret_ratio = m_ret_end / m_end_bal
            tax_ratio = m_tax_end / m_end_bal
            return round(total_bal * ret_ratio, 2), round(total_bal * tax_ratio, 2)

        return 0.0, round(total_bal, 2)

    # Weekly sampling: group by ISO calendar year and week (YYYY-Www)
    weekly_groups: Dict[str, List[Dict[str, Any]]] = {}
    for snap in sorted_snapshots:
        date_str = str(snap["date"])[:10]
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            year, week_num, _ = dt.isocalendar()
            week_key = f"{year}-W{week_num:02d}"
        except Exception:
            week_key = date_str[:7]
        weekly_groups.setdefault(week_key, []).append(snap)

    weekly_history: List[Dict[str, Any]] = []
    for week_key in sorted(weekly_groups.keys()):
        w_snaps = weekly_groups[week_key]
        latest_w_snap = max(w_snaps, key=lambda s: s["date"])
        latest_w_date = str(latest_w_snap["date"])[:10]
        latest_w_total_bal = float(latest_w_snap["balance"])
        latest_w_ret_bal, latest_w_tax_bal = _resolve_daily_scoped_balances(
            latest_w_date, latest_w_total_bal
        )
        weekly_history.append(
            {
                "week": week_key,
                "date": latest_w_date,
                "balance": round(latest_w_total_bal, 2),
                "retirement_balance": round(latest_w_ret_bal, 2),
                "taxable_balance": round(latest_w_tax_bal, 2),
            }
        )

    # Daily sampling: deduplicate snapshots by date
    daily_groups: Dict[str, Dict[str, Any]] = {}
    for snap in sorted_snapshots:
        d = str(snap["date"])[:10]
        total_bal = float(snap["balance"])
        d_ret_bal, d_tax_bal = _resolve_daily_scoped_balances(d, total_bal)
        daily_groups[d] = {
            "date": d,
            "balance": round(total_bal, 2),
            "retirement_balance": round(d_ret_bal, 2),
            "taxable_balance": round(d_tax_bal, 2),
        }
    daily_history: List[Dict[str, Any]] = [
        daily_groups[d] for d in sorted(daily_groups.keys())
    ]

    updated_at = updated_at_iso or datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )

    # Compute retirement_analytics summary object
    latest_h = history[-1]
    latest_balances_by_type = latest_h["balances_by_type"]
    investable_net_worth = latest_h["investable_end_balance"]
    liquid_net_worth = latest_h["liquid_end_balance"]
    illiquid_net_worth = round(current_net_worth - investable_net_worth, 2)
    cash_balance = round(float(latest_balances_by_type.get("depository", 0.0)), 2)
    investments_balance = round(float(latest_balances_by_type.get("brokerage", 0.0)), 2)
    has_taxable_retirement_breakdown = bool(
        "taxable_brokerage" in latest_balances_by_type
        or "retirement" in latest_balances_by_type
    )
    taxable_investments_balance = round(
        float(latest_balances_by_type.get("taxable_brokerage", 0.0)), 2
    )
    retirement_balance = round(
        float(latest_balances_by_type.get("retirement", 0.0)), 2
    )
    real_estate_balance = round(
        float(latest_balances_by_type.get("real_estate", 0.0)), 2
    )
    liabilities_balance = round(
        sum(v for v in latest_balances_by_type.values() if v < 0), 2
    )

    trailing = history[-12:]
    n_trail = len(trailing)
    ann_factor = 12.0 / n_trail if n_trail > 0 else 0.0
    trailing_12m_spending = round(sum(h["spending"] for h in trailing), 2)
    trailing_12m_other_income = round(sum(h["other_income"] for h in trailing), 2)
    trailing_12m_net_withdrawal = round(
        max(0.0, trailing_12m_spending - trailing_12m_other_income), 2
    )
    annualized_spending = round(trailing_12m_spending * ann_factor, 2)
    annualized_net_withdrawal = round(trailing_12m_net_withdrawal * ann_factor, 2)
    avg_monthly_spending = (
        round(trailing_12m_spending / n_trail, 2) if n_trail > 0 else 0.0
    )

    trailing_12m_interest = round(sum(h["interest"] for h in trailing), 2)
    trailing_12m_dividends = round(sum(h["dividends"] for h in trailing), 2)
    trailing_12m_capital_gains = round(sum(h["capital_gains"] for h in trailing), 2)
    trailing_12m_inv_income = round(sum(h["inv_income"] for h in trailing), 2)
    annualized_inv_income = round(trailing_12m_inv_income * ann_factor, 2)

    # SWR is based on total (gross) living spending — matching the Spending report —
    # rather than spending net of non-portfolio income.
    withdrawal_rate_percent = (
        round((annualized_spending / investable_net_worth) * 100.0, 2)
        if investable_net_worth > 0
        else 0.0
    )
    if withdrawal_rate_percent <= 3.5:
        withdrawal_rate_status = "safe"
    elif withdrawal_rate_percent <= 5.0:
        withdrawal_rate_status = "caution"
    else:
        withdrawal_rate_status = "high"

    income_coverage_percent = (
        round((trailing_12m_inv_income / trailing_12m_spending) * 100.0, 2)
        if trailing_12m_spending > 0
        else 0.0
    )
    yield_on_investable_percent = (
        round((annualized_inv_income / investable_net_worth) * 100.0, 2)
        if investable_net_worth > 0
        else 0.0
    )

    twr_total_percent = latest_h["cumulative_twr_percent"]
    twr_annualized_percent = (
        round(((cum_twr_factor ** (12.0 / num_months)) - 1.0) * 100.0, 2)
        if num_months > 0 and cum_twr_factor > 0
        else 0.0
    )
    investable_twr_total_percent = latest_h["investable_cumulative_twr_percent"]
    investable_twr_annualized_percent = (
        round(((inv_cum_twr_factor ** (12.0 / num_months)) - 1.0) * 100.0, 2)
        if num_months > 0 and inv_cum_twr_factor > 0
        else 0.0
    )

    best_twr_entry = max(history, key=lambda h: h["twr_percent"])
    worst_twr_entry = min(history, key=lambda h: h["twr_percent"])
    max_dd_entry = min(history, key=lambda h: h["drawdown_percent"])
    inv_max_dd_entry = min(history, key=lambda h: h["investable_drawdown_percent"])

    months_withdrawing_at_loss = sum(
        1 for h in history if h["total_return_dollars"] < 0 and h["spending"] > 0
    )
    cash_buffer_months = (
        round(cash_balance / avg_monthly_spending, 1)
        if avg_monthly_spending > 0
        else None
    )

    retirement_analytics = {
        "has_type_breakdown": bool(by_month_type or account_monthly_balances),
        "has_taxable_retirement_breakdown": has_taxable_retirement_breakdown,
        "has_transactions": bool(transactions),
        "investable_net_worth": investable_net_worth,
        "liquid_net_worth": liquid_net_worth,
        "illiquid_net_worth": illiquid_net_worth,
        "cash_balance": cash_balance,
        "investments_balance": investments_balance,
        "taxable_investments_balance": taxable_investments_balance,
        "retirement_balance": retirement_balance,
        "taxable_accessible_balance": latest_h["taxable_end_balance"],
        "retirement_accounts_balance": latest_h["retirement_end_balance"],
        "real_estate_balance": real_estate_balance,
        "liabilities_balance": liabilities_balance,
        "trailing_months_count": n_trail,
        "trailing_12m_spending": trailing_12m_spending,
        "trailing_12m_other_income": trailing_12m_other_income,
        "trailing_12m_net_withdrawal": trailing_12m_net_withdrawal,
        "annualized_spending": annualized_spending,
        "annualized_net_withdrawal": annualized_net_withdrawal,
        "avg_monthly_spending": avg_monthly_spending,
        "trailing_12m_interest": trailing_12m_interest,
        "trailing_12m_dividends": trailing_12m_dividends,
        "trailing_12m_capital_gains": trailing_12m_capital_gains,
        "trailing_12m_inv_income": trailing_12m_inv_income,
        "annualized_inv_income": annualized_inv_income,
        "withdrawal_rate_percent": withdrawal_rate_percent,
        "withdrawal_rate_status": withdrawal_rate_status,
        "income_coverage_percent": income_coverage_percent,
        "yield_on_investable_percent": yield_on_investable_percent,
        "twr_total_percent": twr_total_percent,
        "twr_annualized_percent": twr_annualized_percent,
        "investable_twr_total_percent": investable_twr_total_percent,
        "investable_twr_annualized_percent": investable_twr_annualized_percent,
        "best_twr_month": best_twr_entry["month"],
        "best_twr_percent": best_twr_entry["twr_percent"],
        "worst_twr_month": worst_twr_entry["month"],
        "worst_twr_percent": worst_twr_entry["twr_percent"],
        "max_drawdown_percent": max_dd_entry["drawdown_percent"],
        "max_drawdown_month": max_dd_entry["month"],
        "current_drawdown_percent": latest_h["drawdown_percent"],
        "investable_max_drawdown_percent": inv_max_dd_entry[
            "investable_drawdown_percent"
        ],
        "investable_max_drawdown_month": inv_max_dd_entry["month"],
        "investable_current_drawdown_percent": latest_h[
            "investable_drawdown_percent"
        ],
        "months_withdrawing_at_loss": months_withdrawing_at_loss,
        "cash_buffer_months": cash_buffer_months,
        "total_inv_income_all_time": round(sum(h["inv_income"] for h in history), 2),
        "total_spending_all_time": round(sum(h["spending"] for h in history), 2),
        "total_other_income_all_time": round(
            sum(h["other_income"] for h in history), 2
        ),
        "total_return_dollars_all_time": round(
            sum(h["total_return_dollars"] for h in history), 2
        ),
        "total_price_change_dollars_all_time": round(
            sum(h["price_change_dollars"] for h in history), 2
        ),
    }

    summary = {
        "start_date": first_date,
        "latest_date": latest_date,
        "starting_net_worth": starting_net_worth,
        "current_net_worth": current_net_worth,
        "total_gain_amount": total_gain_amount,
        "total_gain_percent": total_gain_percent,
        "peak_net_worth": peak_entry["end_balance"],
        "peak_month": peak_entry["month"],
        "best_month": best_entry["month"],
        "best_month_percent": best_entry["gain_percent"],
        "worst_month": worst_entry["month"],
        "worst_month_percent": worst_entry["gain_percent"],
        "annualized_gain_percent": annualized_gain_percent,
        "avg_monthly_gain_percent": avg_monthly_gain_percent,
        "weekly_history": weekly_history,
        "daily_history": daily_history,
        "updated_at": updated_at,
    }
    if account_monthly_balances is not None:
        summary["taxable_investments_balance"] = taxable_investments_balance
        summary["retirement_balance"] = retirement_balance
        summary["has_taxable_retirement_breakdown"] = has_taxable_retirement_breakdown

    return {
        "summary": summary,
        "retirement_analytics": retirement_analytics,
        "history": history,
        "weekly_history": weekly_history,
        "daily_history": daily_history,
    }


async def get_latest_net_worth_report(
    db: Any,
    start_date: Optional[str] = None,
    user_id: Optional[int] = None,
) -> Optional[Any]:
    """Retrieve the latest stored NetWorthReport record."""
    from sqlalchemy.future import select
    from ..models import NetWorthReport

    query = select(NetWorthReport)
    if start_date:
        query = query.where(NetWorthReport.start_date == start_date)
    if user_id is not None:
        query = query.where(NetWorthReport.user_id == user_id)
    query = query.order_by(NetWorthReport.updated_at.desc(), NetWorthReport.id.desc())
    res = await db.execute(query)
    return res.scalars().first()


async def calculate_and_save_net_worth_report(
    start_date: str = "2024-01-01",
    user_id: Optional[int] = None,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetches daily aggregate snapshots from Monarch Money, computes monthly and cumulative
    net worth analytics, and persists the result in `net_worth_reports`.
    """
    import os
    from ..database import AsyncSessionLocal, Base, engine
    from ..models import Credentials, NetWorthReport
    from .monarch import get_latest_credentials, get_monarch_client

    if email:
        os.environ["MM_EMAIL"] = email

    global _SCHEMA_INITIALIZED
    if not _SCHEMA_INITIALIZED:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        _SCHEMA_INITIALIZED = True

    async with AsyncSessionLocal() as db:
        if user_id:
            creds = await db.get(Credentials, user_id)
        else:
            creds = await get_latest_credentials(db)

        if not creds:
            raise ValueError("No valid Monarch credentials found in database.")

        resolved_user_id = creds.id

        report = await get_latest_net_worth_report(
            db, start_date=start_date, user_id=resolved_user_id
        )
        if not report:
            report = NetWorthReport(
                user_id=resolved_user_id,
                start_date=start_date,
                sync_status="syncing",
            )
            db.add(report)
        else:
            report.sync_status = "syncing"
            report.error_message = None

        await db.commit()
        await db.refresh(report)

        try:
            mm = await get_monarch_client(db, resolved_user_id)
            end_date = datetime.now().strftime("%Y-%m-%d")

            res = await _retry_monarch_call(
                mm.get_aggregate_snapshots,
                start_date=start_date,
                end_date=end_date,
            )
            snapshots = res.get("aggregateSnapshots", []) if isinstance(res, dict) else []

            snapshots_by_type: List[Dict[str, Any]] = []
            account_types: List[Dict[str, Any]] = []
            categories: List[Dict[str, Any]] = []
            all_txs: List[Dict[str, Any]] = []
            account_monthly_balances: Dict[str, Dict[str, float]] = {}
            account_daily_balances: Dict[str, Dict[str, float]] = {}

            if hasattr(mm, "get_account_snapshots_by_type"):
                try:
                    type_res = await _retry_monarch_call(
                        mm.get_account_snapshots_by_type,
                        start_date=start_date,
                        timeframe="month",
                    )
                    if isinstance(type_res, dict):
                        snapshots_by_type = type_res.get("snapshotsByAccountType") or []
                        account_types = type_res.get("accountTypes") or []
                except Exception as e:
                    logger.warning(f"Failed to fetch account snapshots by type: {e}")

            if hasattr(mm, "get_accounts"):
                try:
                    acc_res = await _retry_monarch_call(mm.get_accounts)
                    raw_accounts = (
                        acc_res.get("accounts") or []
                        if isinstance(acc_res, dict)
                        else []
                    )
                    inv_accounts: List[tuple[str, str]] = []
                    for acc in raw_accounts:
                        if not isinstance(acc, dict):
                            continue
                        if acc.get("includeInNetWorth") is False:
                            continue
                        if acc.get("isHidden") is True:
                            continue
                        acc_cat = classify_account(acc)
                        if (
                            acc_cat in ("taxable_brokerage", "retirement")
                            and acc.get("id") is not None
                        ):
                            inv_accounts.append((str(acc["id"]), acc_cat))

                    if inv_accounts:
                        acc_sem = asyncio.Semaphore(5)

                        async def _fetch_acc_snapshots(
                            acc_id: str,
                        ) -> List[Dict[str, Any]]:
                            async with acc_sem:
                                if hasattr(mm, "gql_call"):
                                    try:
                                        from gql import gql

                                        q = gql(
                                            """
                                            query GetAccountSnapshots($id: UUID!) {
                                                snapshots: snapshotsForAccount(accountId: $id) {
                                                    date
                                                    signedBalance
                                                }
                                            }
                                            """
                                        )
                                        r = await _retry_monarch_call(
                                            mm.gql_call,
                                            operation="GetAccountSnapshots",
                                            graphql_query=q,
                                            variables={"id": str(acc_id)},
                                        )
                                        if isinstance(r, dict) and isinstance(
                                            r.get("snapshots"), list
                                        ):
                                            return r["snapshots"]
                                    except Exception as gql_err:
                                        logger.debug(
                                            f"gql_call GetAccountSnapshots failed for {acc_id}, falling back: {gql_err}"
                                        )
                                if hasattr(mm, "get_account_history"):
                                    r = await _retry_monarch_call(
                                        mm.get_account_history, acc_id
                                    )
                                    if isinstance(r, dict) and isinstance(
                                        r.get("snapshots"), list
                                    ):
                                        return r["snapshots"]
                                return []

                        snap_results = await asyncio.gather(
                            *[_fetch_acc_snapshots(aid) for aid, _ in inv_accounts],
                            return_exceptions=True,
                        )

                        target_months_set = {
                            str(s["date"])[:7]
                            for s in snapshots
                            if isinstance(s, dict)
                            and isinstance(s.get("date"), str)
                            and len(s["date"]) >= 7
                            and s["date"] >= start_date
                        }
                        for item in snapshots_by_type:
                            if isinstance(item, dict):
                                m_raw = str(
                                    item.get("month") or item.get("date") or ""
                                )
                                if len(m_raw) >= 7 and m_raw[:7] >= start_date[:7]:
                                    target_months_set.add(m_raw[:7])

                        parsed_acc_snaps: List[
                            tuple[str, List[tuple[str, float]]]
                        ] = []
                        for (_, acc_cat), s_res in zip(inv_accounts, snap_results):
                            if isinstance(s_res, Exception) or not isinstance(
                                s_res, list
                            ):
                                continue
                            pts: List[tuple[str, float]] = []
                            for pt in s_res:
                                if not isinstance(pt, dict):
                                    continue
                                d_str = str(pt.get("date") or "")[:10]
                                bal_val = pt.get("signedBalance")
                                if bal_val is None:
                                    bal_val = pt.get("balance")
                                if len(d_str) >= 7 and bal_val is not None:
                                    try:
                                        pts.append((d_str, float(bal_val)))
                                        if d_str[:7] >= start_date[:7]:
                                            target_months_set.add(d_str[:7])
                                    except (TypeError, ValueError):
                                        pass
                            if pts:
                                pts.sort(key=lambda x: x[0])
                                parsed_acc_snaps.append((acc_cat, pts))

                        if parsed_acc_snaps and target_months_set:
                            sorted_target_months = sorted(target_months_set)
                            for m_key in sorted_target_months:
                                account_monthly_balances[m_key] = {
                                    "taxable_brokerage": 0.0,
                                    "retirement": 0.0,
                                }
                            for acc_cat, pts in parsed_acc_snaps:
                                pt_idx = 0
                                n_pts = len(pts)
                                last_val: Optional[float] = None
                                for m_key in sorted_target_months:
                                    while (
                                        pt_idx < n_pts
                                        and pts[pt_idx][0][:7] <= m_key
                                    ):
                                        last_val = pts[pt_idx][1]
                                        pt_idx += 1
                                    if last_val is not None:
                                        account_monthly_balances[m_key][acc_cat] = (
                                            round(
                                                account_monthly_balances[m_key][
                                                    acc_cat
                                                ]
                                                + max(0.0, last_val),
                                                2,
                                            )
                                        )

                            # Compute daily retirement & taxable balances for every aggregate snapshot date
                            daily_totals_map: Dict[str, float] = {}
                            for s in snapshots:
                                if (
                                    isinstance(s, dict)
                                    and isinstance(s.get("date"), str)
                                    and len(s["date"]) >= 10
                                    and s["date"] >= start_date
                                    and s.get("balance") is not None
                                ):
                                    d_k = str(s["date"])[:10]
                                    try:
                                        daily_totals_map[d_k] = float(s["balance"])
                                    except (TypeError, ValueError):
                                        pass

                            sorted_snap_dates = sorted(daily_totals_map.keys())
                            if sorted_snap_dates:
                                daily_ret_map: Dict[str, float] = {
                                    d_k: 0.0 for d_k in sorted_snap_dates
                                }
                                for acc_cat, pts in parsed_acc_snaps:
                                    if acc_cat != "retirement":
                                        continue
                                    pt_idx = 0
                                    n_pts = len(pts)
                                    last_val = None
                                    for d_k in sorted_snap_dates:
                                        while (
                                            pt_idx < n_pts
                                            and pts[pt_idx][0] <= d_k
                                        ):
                                            last_val = pts[pt_idx][1]
                                            pt_idx += 1
                                        if last_val is not None:
                                            daily_ret_map[d_k] = round(
                                                daily_ret_map[d_k]
                                                + max(0.0, last_val),
                                                2,
                                            )
                                for d_k in sorted_snap_dates:
                                    tot_b = daily_totals_map[d_k]
                                    ret_b = daily_ret_map[d_k]
                                    tax_b = round(max(0.0, tot_b - ret_b), 2)
                                    account_daily_balances[d_k] = {
                                        "retirement_balance": round(ret_b, 2),
                                        "taxable_balance": tax_b,
                                    }
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch per-account brokerage/retirement breakdown: {e}"
                    )

            if hasattr(mm, "get_transaction_categories"):
                try:
                    cat_res = await _retry_monarch_call(mm.get_transaction_categories)
                    if isinstance(cat_res, dict):
                        categories = cat_res.get("categories") or []
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch transaction categories for net worth: {e}"
                    )

            if hasattr(mm, "get_transactions"):
                try:
                    limit = 100
                    first_res = await _retry_monarch_call(
                        mm.get_transactions,
                        start_date=start_date,
                        end_date=end_date,
                        hidden_from_reports=False,
                        limit=limit,
                        offset=0,
                    )
                    if isinstance(first_res, dict):
                        first_data = first_res.get("allTransactions") or {}
                        total_count = int(first_data.get("totalCount") or 0)
                        all_txs = list(first_data.get("results") or [])
                        if total_count > limit:
                            remaining_offsets = range(limit, total_count, limit)
                            page_sem = asyncio.Semaphore(5)

                            async def _fetch_page(off: int):
                                async with page_sem:
                                    return await _retry_monarch_call(
                                        mm.get_transactions,
                                        start_date=start_date,
                                        end_date=end_date,
                                        hidden_from_reports=False,
                                        limit=limit,
                                        offset=off,
                                    )

                            pages = await asyncio.gather(
                                *[_fetch_page(off) for off in remaining_offsets]
                            )
                            for page in pages:
                                if isinstance(page, dict):
                                    all_txs.extend(
                                        (page.get("allTransactions") or {}).get(
                                            "results"
                                        )
                                        or []
                                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to fetch transactions for net worth report: {e}"
                    )

            analytics = compute_net_worth_analytics(
                snapshots,
                start_date_filter=start_date,
                snapshots_by_type=snapshots_by_type,
                account_types=account_types,
                transactions=all_txs,
                categories=categories,
                account_monthly_balances=account_monthly_balances or None,
                account_daily_balances=account_daily_balances or None,
            )

            retirement_analytics = analytics.get("retirement_analytics") or {}
            summary_with_ret = dict(analytics["summary"])
            summary_with_ret["taxable_investments_balance"] = retirement_analytics.get(
                "taxable_investments_balance", 0.0
            )
            summary_with_ret["retirement_balance"] = retirement_analytics.get(
                "retirement_balance", 0.0
            )
            summary_with_ret["has_taxable_retirement_breakdown"] = bool(
                retirement_analytics.get("has_taxable_retirement_breakdown", False)
            )
            if retirement_analytics:
                summary_with_ret["retirement_analytics"] = retirement_analytics

            report.latest_date = analytics["summary"].get("latest_date")
            report.summary = summary_with_ret
            report.history = analytics["history"]
            report.sync_status = "ready"
            report.error_message = None

            await db.commit()
            await db.refresh(report)

            return {
                "status": "ok",
                "summary": report.summary,
                "retirement_analytics": retirement_analytics,
                "history": report.history,
                "weekly_history": (report.summary or {}).get("weekly_history") or [],
                "daily_history": (report.summary or {}).get("daily_history") or [],
                "sync_status": report.sync_status,
                "error_message": report.error_message,
                "updated_at": (
                    report.updated_at.isoformat() if report.updated_at else None
                ),
            }

        except Exception as e:
            logger.error(
                f"Error calculating net worth report (start_date={start_date}): {e}",
                exc_info=True,
            )
            report.sync_status = "error"
            report.error_message = str(e)
            await db.commit()
            raise e


refresh_net_worth_data = calculate_and_save_net_worth_report


def generate_net_worth_png(payload: Dict[str, Any]) -> Optional[bytes]:
    """
    Generates a high-resolution dark-mode dual-subplot PNG chart of Net Worth performance.
    Returns PNG bytes, or None if matplotlib is not installed.
    """
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    history = payload.get("history") or []
    summary = payload.get("summary") or {}
    if not history:
        return None

    cache_key = (
        summary.get("updated_at"),
        summary.get("start_date"),
        summary.get("latest_date"),
        summary.get("total_gain_percent"),
        len(history),
        history[-1].get("month") if isinstance(history[-1], dict) else None,
        history[-1].get("end_balance") if isinstance(history[-1], dict) else None,
    )
    if cache_key in _NET_WORTH_PNG_CACHE:
        return _NET_WORTH_PNG_CACHE[cache_key]

    months = [h["month"] for h in history]
    monthly_pcts = [float(h["gain_percent"]) for h in history]
    cum_pcts = [float(h["cumulative_gain_percent"]) for h in history]
    x_indices = list(range(len(months)))

    plt.style.use("dark_background")
    fig, (ax1, ax2) = plt.subplots(
        2,
        1,
        figsize=(14, 10),
        dpi=300,
        sharex=True,
        gridspec_kw={"height_ratios": [1.2, 1]},
    )
    fig.patch.set_facecolor("#0B0F19")

    for ax in (ax1, ax2):
        ax.set_facecolor("#111827")
        ax.grid(True, linestyle="--", alpha=0.2, color="#374151")
        for spine in ax.spines.values():
            spine.set_color("#1F2937")

    # Top Chart (Subplot 1): Monthly Net Worth Gain / Loss (%)
    bar_colors = ["#10B981" if pct >= 0 else "#EF4444" for pct in monthly_pcts]
    ax1.bar(x_indices, monthly_pcts, width=0.65, color=bar_colors, zorder=3)
    ax1.axhline(0, color="#6B7280", linestyle="--", linewidth=1, alpha=0.6, zorder=2)
    ax1.set_title(
        "Monthly Net Worth Gain / Loss (%)",
        fontsize=16,
        fontweight="bold",
        color="#F9FAFB",
        pad=14,
    )
    ax1.set_ylabel("Monthly Change (%)", fontsize=11, color="#9CA3AF")
    ax1.tick_params(colors="#D1D5DB")

    max_pct = max(monthly_pcts)
    min_pct = min(monthly_pcts)
    y_span = max(max_pct - min_pct, 10.0)
    ax1.set_ylim(min(min_pct, 0) - y_span * 0.22, max(max_pct, 0) + y_span * 0.22)

    for idx, pct in enumerate(monthly_pcts):
        if abs(pct) >= 7.0 or pct == max_pct or pct == min_pct:
            sign = "+" if pct >= 0 else ""
            va = "bottom" if pct >= 0 else "top"
            offset = y_span * 0.03 if pct >= 0 else -y_span * 0.03
            ax1.text(
                idx,
                pct + offset,
                f"{sign}{pct:.1f}%",
                ha="center",
                va=va,
                fontsize=8.5,
                fontweight="bold",
                color="#10B981" if pct >= 0 else "#EF4444",
            )

    # Bottom Chart (Subplot 2): Cumulative Net Worth Growth Since Jan 2024 (%)
    ax2.plot(
        x_indices,
        cum_pcts,
        color="#3B82F6",
        linewidth=2.5,
        marker="o",
        markersize=4,
        zorder=4,
    )
    ax2.fill_between(x_indices, cum_pcts, 0, color="#3B82F6", alpha=0.15, zorder=2)
    ax2.set_title(
        "Cumulative Net Worth Growth Since Jan 2024 (%)",
        fontsize=16,
        fontweight="bold",
        color="#F9FAFB",
        pad=14,
    )
    ax2.set_ylabel("Cumulative Growth (%)", fontsize=11, color="#9CA3AF")
    ax2.tick_params(colors="#D1D5DB")

    final_x = x_indices[-1]
    final_cum = cum_pcts[-1]
    total_gain_pct = float(summary.get("total_gain_percent", final_cum))
    sign_total = "+" if total_gain_pct >= 0 else ""
    ax2.annotate(
        f"Total Gain: {sign_total}{total_gain_pct:.1f}%",
        xy=(final_x, final_cum),
        xytext=(max(0, final_x - max(2, len(x_indices) // 6)), final_cum),
        fontsize=10,
        fontweight="bold",
        color="#F9FAFB",
        bbox=dict(
            boxstyle="round,pad=0.4",
            facecolor="#1E293B",
            edgecolor="#60A5FA",
            linewidth=1.2,
        ),
        arrowprops=dict(arrowstyle="->", color="#60A5FA", lw=1.5),
        zorder=5,
    )

    ax2.set_xticks(x_indices)
    ax2.set_xticklabels(months, rotation=45, ha="right", color="#D1D5DB", fontsize=9)

    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=300, bbox_inches="tight", facecolor="#0B0F19")
    plt.close(fig)
    buf.seek(0)
    png_bytes = buf.getvalue()
    _NET_WORTH_PNG_CACHE[cache_key] = png_bytes
    return png_bytes
