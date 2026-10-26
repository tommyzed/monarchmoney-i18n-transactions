import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger("net_worth_service")


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
) -> Dict[str, Any]:
    """
    Pure calculation helper that computes monthly and cumulative net worth performance
    analytics from raw Monarch aggregate snapshots.
    """
    if not snapshots:
        return {"summary": {}, "history": []}

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
        return {"summary": {}, "history": []}

    sorted_snapshots = sorted(valid_snapshots, key=lambda s: s["date"])

    initial_balance = float(sorted_snapshots[0]["balance"])
    first_date = sorted_snapshots[0]["date"]
    latest_date = sorted_snapshots[-1]["date"]

    # Group snapshots by calendar month (YYYY-MM)
    monthly_groups: Dict[str, List[Dict[str, Any]]] = {}
    for snap in sorted_snapshots:
        month_key = snap["date"][:7]
        monthly_groups.setdefault(month_key, []).append(snap)

    history: List[Dict[str, Any]] = []
    prev_end_balance = initial_balance

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

        history.append(
            {
                "month": month_key,
                "start_balance": round(start_balance, 2),
                "end_balance": round(end_balance, 2),
                "gain_amount": round(gain_amount, 2),
                "gain_percent": round(gain_percent, 2),
                "cumulative_gain_amount": round(cumulative_gain_amount, 2),
                "cumulative_gain_percent": round(cumulative_gain_percent, 2),
                "end_date": end_date,
            }
        )

        prev_end_balance = end_balance

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

    updated_at = updated_at_iso or datetime.now(timezone.utc).astimezone().isoformat(
        timespec="seconds"
    )

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
        "avg_monthly_gain_percent": avg_monthly_gain_percent,
        "updated_at": updated_at,
    }

    return {
        "summary": summary,
        "history": history,
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

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

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

            analytics = compute_net_worth_analytics(
                snapshots, start_date_filter=start_date
            )

            report.latest_date = analytics["summary"].get("latest_date")
            report.summary = analytics["summary"]
            report.history = analytics["history"]
            report.sync_status = "ready"
            report.error_message = None

            await db.commit()
            await db.refresh(report)

            return {
                "status": "ok",
                "summary": report.summary,
                "history": report.history,
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
    return buf.getvalue()
