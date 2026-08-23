#!/usr/bin/env python3
"""
Spending Report Generator (Read-Only)
Fetches and analyzes transactions and cash flow aggregates from Monarch Money.

Usage:
    python scripts/spending_report.py --year 2026
    python scripts/spending_report.py --start-date 2026-01-01 --end-date 2026-06-30
    python scripts/spending_report.py --year 2026 --top 20
    python scripts/spending_report.py --year 2026 --json
"""

import argparse
import asyncio
import datetime
import json
import os
import sys

# Ensure repository root is on sys.path
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

from bridge_app.database import AsyncSessionLocal
from bridge_app.services.monarch import get_latest_credentials, get_monarch_client


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate read-only spending and cash flow reports from Monarch Money."
    )
    current_year = datetime.date.today().year
    parser.add_argument(
        "--year",
        type=int,
        default=current_year,
        help=f"Calendar year to analyze (default: {current_year})",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default=None,
        help="Custom start date (YYYY-MM-DD). Overrides --year.",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        default=None,
        help="Custom end date (YYYY-MM-DD). Overrides --year.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        default=False,
        help="Include transactions marked as hidden from reports (default: False).",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=15,
        help="Number of top categories to display (default: 15).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output raw structured JSON instead of formatted text tables.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Optional Monarch account email to query credentials for.",
    )
    return parser.parse_args()


async def fetch_spending_report(
    start_date: str,
    end_date: str,
    include_hidden: bool = False,
    email: str = None,
):
    if email:
        os.environ["MM_EMAIL"] = email

    async with AsyncSessionLocal() as session:
        creds = await get_latest_credentials(session)
        if not creds:
            raise RuntimeError(
                "No Monarch Money credentials found in database. Run 'python scripts/cookie_login.py' first."
            )

        mm = await get_monarch_client(session, creds.id)

        # 1. Fetch Categories for metadata and classification (READ-ONLY)
        categories_data = await mm.get_transaction_categories()
        categories_map = {c["id"]: c for c in categories_data.get("categories", [])}

        # 2. Fetch Cash Flow Aggregates (READ-ONLY)
        cashflow_data = await mm.get_cashflow(start_date=start_date, end_date=end_date)
        summary_list = cashflow_data.get("summary", [])
        server_summary = summary_list[0].get("summary", {}) if summary_list else {}

        server_group_aggregates = []
        for item in cashflow_data.get("byCategoryGroup", []):
            group = item.get("groupBy", {}).get("categoryGroup", {})
            group_sum = item.get("summary", {}).get("sum", 0.0)
            server_group_aggregates.append(
                {
                    "name": group.get("name") or "Uncategorized",
                    "type": group.get("type") or "other",
                    "amount": abs(group_sum) if (group.get("type") == "expense" and group_sum < 0) else group_sum,
                    "raw_sum": group_sum,
                }
            )

        # 3. Fetch Itemized Transactions (READ-ONLY)
        all_txs = []
        offset = 0
        limit = 100
        hidden_filter = None if include_hidden else False

        while True:
            res = await mm.get_transactions(
                start_date=start_date,
                end_date=end_date,
                hidden_from_reports=hidden_filter,
                limit=limit,
                offset=offset,
            )
            data = res.get("allTransactions", {})
            total_count = data.get("totalCount", 0)
            results = data.get("results", [])
            all_txs.extend(results)

            if len(all_txs) >= total_count or len(results) == 0:
                break
            offset += limit

        # 4. Itemized Calculation & Aggregation
        itemized_expense_total = 0.0
        itemized_income_total = 0.0
        itemized_transfers_total = 0.0

        categorized_breakdown = {}
        category_group_breakdown = {}
        monthly_breakdown = {}

        for tx in all_txs:
            amount = tx.get("amount", 0.0)
            tx_date = tx.get("date", "")
            month_key = tx_date[:7] if tx_date else "Unknown"
            cat_info = tx.get("category") or {}
            cat_id = cat_info.get("id")

            full_cat = categories_map.get(cat_id, {})
            cat_name = full_cat.get("name") or cat_info.get("name") or "Uncategorized"
            group = full_cat.get("group") or {}
            group_name = group.get("name") or "Other"
            group_type = group.get("type", "unknown")

            if group_type == "expense":
                # In Monarch, charges are negative amounts, refunds are positive amounts
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
            elif group_type == "income":
                itemized_income_total += amount
            elif group_type == "transfer":
                itemized_transfers_total += amount
            else:
                if amount < 0:
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
                else:
                    itemized_income_total += amount

        return {
            "period": {"start_date": start_date, "end_date": end_date},
            "include_hidden": include_hidden,
            "total_transactions": len(all_txs),
            "server_summary": {
                "total_expense": abs(server_summary.get("sumExpense", 0.0)),
                "total_income": server_summary.get("sumIncome", 0.0),
                "net_savings": server_summary.get("savings", 0.0),
                "savings_rate": server_summary.get("savingsRate", 0.0),
            },
            "itemized_summary": {
                "total_expense": itemized_expense_total,
                "total_income": itemized_income_total,
                "total_transfers": itemized_transfers_total,
            },
            "server_group_aggregates": server_group_aggregates,
            "category_groups": category_group_breakdown,
            "categories": categorized_breakdown,
            "monthly_spending": monthly_breakdown,
        }


def print_formatted_report(data: dict, top_n: int = 15):
    period = data["period"]
    server_sum = data["server_summary"]
    itemized_sum = data["itemized_summary"]
    cat_groups = data["category_groups"]
    categories = data["categories"]
    monthly = data["monthly_spending"]
    include_hidden = data["include_hidden"]

    header = f"MONARCH MONEY SPENDING REPORT ({period['start_date']} to {period['end_date']})"
    print("=" * len(header))
    print(header)
    print("=" * len(header))
    print(f"Hidden Transactions: {'INCLUDED' if include_hidden else 'EXCLUDED'}")
    print(f"Total Transactions Processed: {data['total_transactions']:,}")
    print()

    print("--- EXECUTIVE SUMMARY ---")
    print(f"Total Spending (Net Expenses) : ${server_sum['total_expense']:>12,.2f}")
    print(f"Total Income                  : ${server_sum['total_income']:>12,.2f}")
    print(f"Net Savings                   : ${server_sum['net_savings']:>12,.2f} ({server_sum['savings_rate']*100:.1f}%)")
    print(f"Itemized Gross Sum            : ${itemized_sum['total_expense']:>12,.2f}")
    print()

    print("--- SPENDING BY CATEGORY GROUP ---")
    total_exp = server_sum["total_expense"] if server_sum["total_expense"] > 0 else 1.0
    for g_name, amt in sorted(cat_groups.items(), key=lambda x: x[1], reverse=True):
        pct = (amt / total_exp) * 100
        print(f"  {g_name:<30} : ${amt:>10,.2f}  ({pct:>5.1f}%)")
    print()

    print(f"--- TOP {top_n} SPENDING CATEGORIES ---")
    sorted_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:top_n]
    for c_name, amt in sorted_cats:
        pct = (amt / total_exp) * 100
        print(f"  {c_name:<35} : ${amt:>10,.2f}  ({pct:>5.1f}%)")
    print()

    print("--- MONTHLY SPENDING BREAKDOWN ---")
    for m_key in sorted(monthly.keys()):
        amt = monthly[m_key]
        print(f"  {m_key} : ${amt:>10,.2f}")
    print("=" * len(header))


def main():
    args = parse_args()

    if args.start_date and args.end_date:
        start_date = args.start_date
        end_date = args.end_date
    else:
        start_date = f"{args.year}-01-01"
        end_date = f"{args.year}-12-31"

    try:
        report_data = asyncio.run(
            fetch_spending_report(
                start_date=start_date,
                end_date=end_date,
                include_hidden=args.include_hidden,
                email=args.email,
            )
        )

        if args.json:
            print(json.dumps(report_data, indent=2))
        else:
            print_formatted_report(report_data, top_n=args.top)

    except Exception as e:
        print(f"❌ Error generating spending report: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
