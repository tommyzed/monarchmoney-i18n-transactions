#!/usr/bin/env python3
"""
Net Worth Performance Report Generator (Read-Only)
Fetches daily aggregate snapshots from Monarch Money and computes monthly and cumulative
net worth performance analytics.

Usage:
    python scripts/net_worth_report.py --start-date 2024-01-01
    python scripts/net_worth_report.py --start-date 2024-01-01 --json
    python scripts/net_worth_report.py --start-date 2024-01-01 --save-db
    python scripts/net_worth_report.py --start-date 2024-01-01 --png net_worth_chart.png
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

from bridge_app.database import AsyncSessionLocal, Base, engine
from bridge_app.models import NetWorthReport
from bridge_app.services.monarch import get_latest_credentials, get_monarch_client
from bridge_app.services.net_worth_service import (
    _retry_monarch_call,
    compute_net_worth_analytics,
    generate_net_worth_png,
    get_latest_net_worth_report,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Generate read-only Net Worth Performance analytics from Monarch Money."
    )
    parser.add_argument(
        "--start-date",
        type=str,
        default="2024-01-01",
        help="Start date in YYYY-MM-DD format (default: 2024-01-01).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output raw structured JSON instead of formatted text tables.",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        default=False,
        help="Persist calculated report into the database for the web UI.",
    )
    parser.add_argument(
        "--png",
        type=str,
        default=None,
        help="Optional output file path to render a high-res PNG chart.",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="Optional Monarch account email to query credentials for.",
    )
    return parser.parse_args()


async def fetch_net_worth_report(
    start_date: str = "2024-01-01",
    email: str = None,
    save_db: bool = False,
    quiet: bool = False,
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
        end_date = datetime.date.today().strftime("%Y-%m-%d")

        res = await _retry_monarch_call(
            mm.get_aggregate_snapshots,
            start_date=start_date,
            end_date=end_date,
        )
        snapshots = res.get("aggregateSnapshots", []) if isinstance(res, dict) else []
        payload = compute_net_worth_analytics(snapshots, start_date_filter=start_date)

        if save_db:
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(Base.metadata.create_all)

                db_report = await get_latest_net_worth_report(
                    session, start_date=start_date, user_id=creds.id
                )
                if not db_report:
                    db_report = NetWorthReport(
                        user_id=creds.id,
                        start_date=start_date,
                        sync_status="ready",
                    )
                    session.add(db_report)

                db_report.latest_date = payload["summary"].get("latest_date")
                db_report.summary = payload["summary"]
                db_report.history = payload["history"]
                db_report.sync_status = "ready"
                db_report.error_message = None
                await session.commit()
                if not quiet:
                    print(f"💾 Successfully saved Net Worth report (since {start_date}) to database.")
            except Exception as save_err:
                print(f"⚠️ Warning: Could not save to DB: {save_err}", file=sys.stderr)

        return payload


def print_formatted_report(data: dict):
    summary = data.get("summary") or {}
    history = data.get("history") or []

    if not summary or not history:
        print("No net worth snapshot data found for the specified date range.")
        return

    header = (
        f"MONARCH MONEY NET WORTH PERFORMANCE ({summary['start_date']} to {summary['latest_date']})"
    )
    print("=" * len(header))
    print(header)
    print("=" * len(header))
    print()

    print("--- EXECUTIVE SUMMARY ---")
    print(f"Current Net Worth             : ${summary['current_net_worth']:>14,.2f}")
    print(f"Starting Net Worth            : ${summary['starting_net_worth']:>14,.2f}")
    total_sign = "+" if summary["total_gain_amount"] >= 0 else ""
    pct_sign = "+" if summary["total_gain_percent"] >= 0 else ""
    print(
        f"Total Cumulative Gain         : {total_sign}${summary['total_gain_amount']:>13,.2f} ({pct_sign}{summary['total_gain_percent']:.2f}%)"
    )
    print(
        f"All-Time High Net Worth       : ${summary['peak_net_worth']:>14,.2f} ({summary['peak_month']})"
    )
    best_sign = "+" if summary["best_month_percent"] >= 0 else ""
    worst_sign = "+" if summary["worst_month_percent"] >= 0 else ""
    avg_sign = "+" if summary["avg_monthly_gain_percent"] >= 0 else ""
    print(
        f"Best Month                    : {summary['best_month']:>15} ({best_sign}{summary['best_month_percent']:.2f}%)"
    )
    print(
        f"Worst Month                   : {summary['worst_month']:>15} ({worst_sign}{summary['worst_month_percent']:.2f}%)"
    )
    print(f"Avg Monthly Return            : {avg_sign}{summary['avg_monthly_gain_percent']:>14.2f}%")
    print()

    print("--- MONTHLY PERFORMANCE BREAKDOWN ---")
    print(
        f"  {'Month':<10} | {'Start Balance':>15} | {'End Balance':>15} | {'Monthly Gain ($)':>16} | {'Monthly (%)':>11} | {'Cumulative (%)':>14}"
    )
    print("  " + "-" * 94)
    for row in history:
        m_sign = "+" if row["gain_amount"] >= 0 else ""
        mp_sign = "+" if row["gain_percent"] >= 0 else ""
        cp_sign = "+" if row["cumulative_gain_percent"] >= 0 else ""
        print(
            f"  {row['month']:<10} | ${row['start_balance']:>14,.2f} | ${row['end_balance']:>14,.2f} | "
            f"{m_sign}${row['gain_amount']:>14,.2f} | {mp_sign}{row['gain_percent']:>9.2f}% | {cp_sign}{row['cumulative_gain_percent']:>12.2f}%"
        )
    print("=" * len(header))


def main():
    args = parse_args()

    try:
        report_data = asyncio.run(
            fetch_net_worth_report(
                start_date=args.start_date,
                email=args.email,
                save_db=args.save_db,
                quiet=args.json,
            )
        )

        if args.png:
            png_bytes = generate_net_worth_png(report_data)
            if png_bytes is None:
                print(
                    "⚠️ Warning: Could not generate PNG chart (matplotlib not installed or empty data).",
                    file=sys.stderr,
                )
            else:
                with open(args.png, "wb") as f:
                    f.write(png_bytes)
                if not args.json:
                    print(f"🖼️  Saved Net Worth chart PNG to {args.png}")

        if args.json:
            print(json.dumps(report_data, indent=2))
        else:
            print_formatted_report(report_data)

    except Exception as e:
        import traceback

        print(f"❌ Error generating net worth report: {e}", file=sys.stderr)
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
