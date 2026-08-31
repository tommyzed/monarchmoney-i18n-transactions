import asyncio
import datetime
import logging
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from ..database import AsyncSessionLocal
from ..models import Credentials, SpendingReport
from .monarch import get_monarch_client, get_latest_credentials

logger = logging.getLogger("spending_service")
UNCATEGORIZED = "Uncategorized"


async def get_or_create_spending_report(
    db: AsyncSession, year: int, user_id: Optional[int] = None
) -> Optional[SpendingReport]:
    """Retrieve existing spending report for year/user, or None."""
    query = select(SpendingReport).where(SpendingReport.year == year)
    if user_id is not None:
        query = query.where(SpendingReport.user_id == user_id)
    query = query.order_by(SpendingReport.updated_at.desc())
    res = await db.execute(query)
    return res.scalars().first()


async def _retry_monarch_call(func, *args, max_retries=4, initial_delay=1.5, **kwargs):
    """Execute a Monarch API call with automatic retries on transient errors."""
    delay = initial_delay
    last_err = None
    for attempt in range(1, max_retries + 1):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            last_err = e
            logger.warning(f"Monarch call {func.__name__} attempt {attempt}/{max_retries} failed: {e}. Retrying in {delay:.1f}s...")
            if attempt < max_retries:
                await asyncio.sleep(delay)
                delay *= 2
    raise last_err


async def calculate_and_save_spending_report(
    year: int,
    user_id: Optional[int] = None,
    include_hidden: bool = False,
    email: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Executes read-only Monarch queries to compute annual spending
    and persists the snapshot to the database.
    """
    start_date = f"{year}-01-01"
    end_date = f"{year}-12-31"

    async with AsyncSessionLocal() as db:
        # 1. Fetch credentials
        if user_id:
            creds = await db.get(Credentials, user_id)
        else:
            creds = await get_latest_credentials(db)

        if not creds:
            raise ValueError("No valid Monarch credentials found in database.")

        resolved_user_id = creds.id

        # 2. Find or initialize report record
        report = await get_or_create_spending_report(db, year=year, user_id=resolved_user_id)
        if not report:
            report = SpendingReport(
                user_id=resolved_user_id,
                year=year,
                start_date=start_date,
                end_date=end_date,
                include_hidden=include_hidden,
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

            # 3. Read Categories (READ-ONLY)
            cat_res = await _retry_monarch_call(mm.get_transaction_categories)
            categories_map = {c["id"]: c for c in cat_res.get("categories", [])}

            # 4. Read Cash Flow Aggregates (READ-ONLY)
            cashflow_res = await _retry_monarch_call(mm.get_cashflow, start_date=start_date, end_date=end_date)
            summary_list = cashflow_res.get("summary", [])
            server_sum = summary_list[0].get("summary", {}) if summary_list else {}

            server_group_aggregates = []
            for item in cashflow_res.get("byCategoryGroup", []):
                group = item.get("groupBy", {}).get("categoryGroup", {})
                g_sum = item.get("summary", {}).get("sum", 0.0)
                server_group_aggregates.append(
                    {
                        "name": group.get("name") or UNCATEGORIZED,
                        "type": group.get("type") or "other",
                        "amount": abs(g_sum) if (group.get("type") == "expense" and g_sum < 0) else g_sum,
                        "raw_sum": g_sum,
                    }
                )

            # 5. Read Itemized Transactions (READ-ONLY)
            limit = 100
            hidden_filter = None if include_hidden else False

            first_res = await _retry_monarch_call(
                mm.get_transactions,
                start_date=start_date,
                end_date=end_date,
                hidden_from_reports=hidden_filter,
                limit=limit,
                offset=0,
            )
            first_data = first_res.get("allTransactions", {})
            total_count = first_data.get("totalCount", 0)
            all_txs = list(first_data.get("results", []))

            if total_count > limit:
                remaining_offsets = range(limit, total_count, limit)
                tasks = [
                    _retry_monarch_call(
                        mm.get_transactions,
                        start_date=start_date,
                        end_date=end_date,
                        hidden_from_reports=hidden_filter,
                        limit=limit,
                        offset=off,
                    )
                    for off in remaining_offsets
                ]
                pages = await asyncio.gather(*tasks)
                for page in pages:
                    all_txs.extend(page.get("allTransactions", {}).get("results", []))

            # 6. Aggregate Calculations
            itemized_expense_total = 0.0
            itemized_income_total = 0.0
            itemized_transfers_total = 0.0

            categorized_breakdown = {}
            category_group_breakdown = {}
            category_to_group_map = {}
            monthly_breakdown = {}
            monthly_category_group_breakdown = {}
            monthly_categorized_breakdown = {}

            # Pre-populate category -> group mapping from categories dictionary
            for c in cat_res.get("categories", []):
                c_name = c.get("name")
                g_name = c.get("group", {}).get("name")
                if c_name and g_name:
                    category_to_group_map[c_name] = g_name

            for tx in all_txs:
                amount = tx.get("amount", 0.0)
                tx_date = tx.get("date", "")
                month_key = tx_date[:7] if tx_date else "Unknown"
                cat_info = tx.get("category") or {}
                cat_id = cat_info.get("id")

                full_cat = categories_map.get(cat_id, {})
                cat_name = full_cat.get("name") or cat_info.get("name") or UNCATEGORIZED
                group = full_cat.get("group") or {}
                group_name = group.get("name") or "Other"
                group_type = group.get("type", "unknown")

                if cat_name and group_name:
                    category_to_group_map[cat_name] = group_name

                if group_type == "expense" or (group_type not in ("income", "transfer") and amount < 0):
                    expense_val = -amount
                    itemized_expense_total += expense_val
                    categorized_breakdown[cat_name] = (
                        categorized_breakdown.get(cat_name, 0.0) + expense_val
                    )
                    category_group_breakdown[group_name] = (
                        category_group_breakdown.get(group_name, 0.0) + expense_val
                    )
                    monthly_breakdown[month_key] = (
                        monthly_breakdown.get(month_key, 0.0) + expense_val
                    )
                    if month_key not in monthly_category_group_breakdown:
                        monthly_category_group_breakdown[month_key] = {}
                    monthly_category_group_breakdown[month_key][group_name] = (
                        monthly_category_group_breakdown[month_key].get(group_name, 0.0) + expense_val
                    )
                    if month_key not in monthly_categorized_breakdown:
                        monthly_categorized_breakdown[month_key] = {}
                    monthly_categorized_breakdown[month_key][cat_name] = (
                        monthly_categorized_breakdown[month_key].get(cat_name, 0.0) + expense_val
                    )
                elif group_type == "income" or (group_type not in ("expense", "transfer") and amount >= 0):
                    itemized_income_total += amount
                elif group_type == "transfer":
                    itemized_transfers_total += amount

            sum_expense = abs(server_sum.get("sumExpense") or 0.0)
            sum_income = server_sum.get("sumIncome") or 0.0
            sum_savings = server_sum.get("savings") or 0.0
            sum_savings_rate = server_sum.get("savingsRate") or 0.0

            # 7. Update and Commit Report Record
            report.summary = {
                "total_expense": sum_expense,
                "total_income": sum_income,
                "net_savings": sum_savings,
                "savings_rate": sum_savings_rate,
                "itemized_expense": itemized_expense_total,
                "itemized_income": itemized_income_total,
                "itemized_transfers": itemized_transfers_total,
                "total_transactions": len(all_txs),
            }
            report.category_groups = category_group_breakdown
            report.categories = categorized_breakdown
            report.category_to_group = category_to_group_map
            report.monthly_spending = monthly_breakdown
            report.monthly_category_groups = monthly_category_group_breakdown
            report.monthly_categories = monthly_categorized_breakdown
            report.sync_status = "ready"
            report.error_message = None

            await db.commit()
            await db.refresh(report)

            return {
                "status": "success",
                "year": year,
                "summary": report.summary,
                "updated_at": report.updated_at.isoformat() if report.updated_at else None,
            }

        except Exception as e:
            logger.error(f"Error calculating spending report for {year}: {e}", exc_info=True)
            report.sync_status = "error"
            report.error_message = str(e)
            await db.commit()
            raise e
