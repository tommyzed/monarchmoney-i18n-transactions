from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from bridge_app.services.spending_service import (
    DE_TAXABLE_INCOME_ACCOUNT_IDS,
    DE_TAXABLE_INCOME_CATEGORY_IDS,
    calculate_and_save_spending_report,
    is_taxable_earnings_account,
)
from scripts import spending_report as spending_report_script


def test_de_taxable_income_account_ids_updated():
    expected_ids = {
        "165678505356837668",
        "165766005491385245",
        "173196443058061630",
        "189288389787664529",
        "260973476790551490",
        "260973476855563203",
        "260973477077861319",
        "260973477159650248",
    }
    removed_legacy_ids = {
        "161771648750862611",
        "161771648799097108",
        "219467442390712365",
    }

    assert DE_TAXABLE_INCOME_ACCOUNT_IDS == expected_ids
    assert DE_TAXABLE_INCOME_ACCOUNT_IDS.isdisjoint(removed_legacy_ids)
    assert (
        spending_report_script.DE_TAXABLE_INCOME_ACCOUNT_IDS
        == DE_TAXABLE_INCOME_ACCOUNT_IDS
    )
    assert DE_TAXABLE_INCOME_CATEGORY_IDS == {
        "207418185825534724",
        "169645499485667936",
    }


@pytest.mark.parametrize(
    ("account", "expected"),
    [
        (
            {
                "id": "260973476529456064",
                "displayName": "Rollover IRA",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {
                    "name": "ira",
                    "display": "Individual Retirement Account (IRA)",
                },
            },
            False,
        ),
        (
            {
                "id": "260973476790551490",
                "displayName": "Bonds -2525",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            True,
        ),
        (
            {
                "id": "260973476855563203",
                "displayName": "Gemini Stocks",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            True,
        ),
        (
            {
                "id": "260973477159650248",
                "displayName": "Stocks",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            True,
        ),
        (
            {
                "id": "260973477077861319",
                "displayName": "ORCL ESPP",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "stock_plan", "display": "Stock Plan"},
            },
            True,
        ),
        (
            {
                "id": "260973476909040580",
                "displayName": "Premium Savings - 3.25%",
                "type": {"name": "depository", "display": "Depository"},
                "subtype": {"name": "savings", "display": "Savings"},
            },
            True,
        ),
    ],
)
def test_is_taxable_earnings_account_etrade_classification(account, expected):
    assert is_taxable_earnings_account(account) is expected


async def test_calculate_and_save_spending_report_de_taxable_income():
    mock_categories = {
        "categories": [
            {
                "id": "169645499485667936",
                "name": "Dividends",
                "group": {"name": "Income", "type": "income"},
            },
            {
                "id": "207418185825534724",
                "name": "Capital Gains",
                "group": {"name": "Income", "type": "income"},
            },
            {
                "id": "999999",
                "name": "Interest",
                "group": {"name": "Income", "type": "income"},
            },
        ]
    }
    mock_accounts = {
        "accounts": [
            {
                "id": "260973477159650248",
                "displayName": "Stocks",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            {
                "id": "260973476855563203",
                "displayName": "Gemini Stocks",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            {
                "id": "260973476790551490",
                "displayName": "Bonds -2525",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            {
                "id": "260973477077861319",
                "displayName": "ORCL ESPP",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "stock_plan", "display": "Stock Plan"},
            },
            {
                "id": "189288389787664529",
                "displayName": "Brokerage",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {"name": "brokerage", "display": "Brokerage (Taxable)"},
            },
            {
                "id": "260973476529456064",
                "displayName": "Rollover IRA",
                "type": {"name": "brokerage", "display": "Brokerage"},
                "subtype": {
                    "name": "ira",
                    "display": "Individual Retirement Account (IRA)",
                },
            },
        ]
    }
    mock_cashflow = {
        "summary": [
            {
                "summary": {
                    "sumExpense": 0.0,
                    "sumIncome": 3524.75,
                    "savings": 3524.75,
                    "savingsRate": 1.0,
                }
            }
        ],
        "byCategoryGroup": [],
    }
    mock_transactions = {
        "allTransactions": {
            "totalCount": 8,
            "results": [
                {
                    "date": "2026-03-15",
                    "amount": 1000.50,
                    "category": {"id": "169645499485667936", "name": "Dividends"},
                    "account": {"id": "260973477159650248", "displayName": "Stocks"},
                },
                {
                    "date": "2026-04-10",
                    "amount": 500.25,
                    "category": {"id": "207418185825534724", "name": "Capital Gains"},
                    "account": {
                        "id": "260973476855563203",
                        "displayName": "Gemini Stocks",
                    },
                },
                {
                    "date": "2026-05-01",
                    "amount": 200.00,
                    "category": {"id": "169645499485667936", "name": "Dividends"},
                    "account": {
                        "id": "260973476790551490",
                        "displayName": "Bonds -2525",
                    },
                },
                {
                    "date": "2026-06-01",
                    "amount": 50.00,
                    "category": {"id": "169645499485667936", "name": "Dividends"},
                    "account": {
                        "id": "260973477077861319",
                        "displayName": "ORCL ESPP",
                    },
                },
                {
                    "date": "2026-07-01",
                    "amount": 300.00,
                    "category": {"id": "169645499485667936", "name": "Dividends"},
                    "account": {"id": "189288389787664529", "displayName": "Brokerage"},
                },
                {
                    "date": "2026-08-01",
                    "amount": 999.00,
                    "category": {"id": "169645499485667936", "name": "Dividends"},
                    "account": {
                        "id": "161771648750862611",
                        "displayName": "Legacy E*TRADE",
                    },
                },
                {
                    "date": "2026-09-01",
                    "amount": 400.00,
                    "category": {"id": "169645499485667936", "name": "Dividends"},
                    "account": {
                        "id": "260973476529456064",
                        "displayName": "Rollover IRA",
                    },
                },
                {
                    "date": "2026-10-01",
                    "amount": 75.00,
                    "category": {"id": "999999", "name": "Interest"},
                    "account": {"id": "260973477159650248", "displayName": "Stocks"},
                },
            ],
        }
    }

    mock_mm = SimpleNamespace(
        get_transaction_categories=AsyncMock(return_value=mock_categories),
        get_accounts=AsyncMock(return_value=mock_accounts),
        get_cashflow=AsyncMock(return_value=mock_cashflow),
        get_transactions=AsyncMock(return_value=mock_transactions),
    )

    with (
        patch(
            "bridge_app.services.spending_service.get_latest_credentials",
            new=AsyncMock(return_value=SimpleNamespace(id=1)),
        ),
        patch(
            "bridge_app.services.spending_service.get_monarch_client",
            new=AsyncMock(return_value=mock_mm),
        ),
    ):
        result = await calculate_and_save_spending_report(year=2026)

    assert result["status"] == "success"
    assert result["summary"]["de_taxable_income"] == 2050.75
    assert result["summary"]["taxable_earnings"] == 3124.75
