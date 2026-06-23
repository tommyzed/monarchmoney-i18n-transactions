import asyncio
import json

from monarchmoney import MonarchMoney

_SESSION_FILE_ = ".mm/mm_session.pickle"


def login() -> MonarchMoney:
    mm = MonarchMoney(session_file=_SESSION_FILE_)
    print("Attempting to login...")
    asyncio.run(mm.interactive_login())
    print("Login successful")
    return mm


def get_subscription_details(mm: MonarchMoney) -> None:
    subs = asyncio.run(mm.get_subscription_details())
    print(subs)


def get_accounts(mm: MonarchMoney) -> None:
    accounts = asyncio.run(mm.get_accounts())
    with open("data.json", "w") as outfile:
        json.dump(accounts, outfile)


def get_institutions(mm: MonarchMoney) -> None:
    institutions = asyncio.run(mm.get_institutions())
    with open("institutions.json", "w") as outfile:
        json.dump(institutions, outfile)


def get_budgets(mm: MonarchMoney) -> None:
    budgets = asyncio.run(mm.get_budgets())
    with open("budgets.json", "w") as outfile:
        json.dump(budgets, outfile, indent=4)


def get_transactions_summary(mm: MonarchMoney) -> None:
    transactions_summary = asyncio.run(mm.get_transactions_summary())
    with open("transactions_summary.json", "w") as outfile:
        json.dump(transactions_summary, outfile)


def get_categories(mm: MonarchMoney) -> tuple[dict, dict]:
    categories = asyncio.run(mm.get_transaction_categories())
    with open("categories.json", "w") as outfile:
        json.dump(categories, outfile)

    income_categories = dict()
    for c in categories.get("categories"):
        if c.get("group").get("type") == "income":
            print(
                f'{c.get("group").get("type")} - {c.get("group").get("name")} - {c.get("name")}'
            )
            income_categories[c.get("name")] = 0

    expense_category_groups = dict()
    for c in categories.get("categories"):
        if c.get("group").get("type") == "expense":
            print(
                f'{c.get("group").get("type")} - {c.get("group").get("name")} - {c.get("name")}'
            )
            expense_category_groups[c.get("group").get("name")] = 0

    return income_categories, expense_category_groups


def get_transactions(mm: MonarchMoney) -> None:
    transactions = asyncio.run(mm.get_transactions(limit=10))
    with open("transactions.json", "w") as outfile:
        json.dump(transactions, outfile)


def get_cashflow(mm: MonarchMoney, income_categories: dict, expense_category_groups: dict) -> None:
    cashflow = asyncio.run(
        mm.get_cashflow(start_date="2026-10-01", end_date="2026-10-31")
    )
    with open("cashflow.json", "w") as outfile:
        json.dump(cashflow, outfile)

    for c in cashflow.get("summary"):
        print(
            f'Income: {c.get("summary").get("sumIncome")} '
            f'Expense: {c.get("summary").get("sumExpense")} '
            f'Savings: {c.get("summary").get("savings")} '
            f'({c.get("summary").get("savingsRate"):.0%})'
        )

    for c in cashflow.get("byCategory"):
        if c.get("groupBy").get("category").get("group").get("type") == "income":
            income_categories[c.get("groupBy").get("category").get("name")] += c.get(
                "summary"
            ).get("sum")

    print()
    for c in cashflow.get("byCategoryGroup"):
        if c.get("groupBy").get("categoryGroup").get("type") == "expense":
            expense_category_groups[
                c.get("groupBy").get("categoryGroup").get("name")
            ] += c.get("summary").get("sum")

    print(income_categories)
    print()
    print(expense_category_groups)


def run_all(mm: MonarchMoney) -> None:
    get_subscription_details(mm)
    get_accounts(mm)
    get_institutions(mm)
    get_budgets(mm)
    get_transactions_summary(mm)
    income_categories, expense_category_groups = get_categories(mm)
    get_transactions(mm)
    get_cashflow(mm, income_categories, expense_category_groups)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Monarch Money CLI")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    subparsers.add_parser("subscriptions", help="Get subscription details")
    subparsers.add_parser("accounts", help="Get accounts")
    subparsers.add_parser("institutions", help="Get institutions")
    subparsers.add_parser("budgets", help="Get budgets")
    subparsers.add_parser("transactions_summary", help="Get transactions summary")
    subparsers.add_parser("categories", help="Get categories")
    subparsers.add_parser("transactions", help="Get transactions")
    subparsers.add_parser("cashflow", help="Get cashflow")
    subparsers.add_parser("all", help="Run all commands sequentially")

    args = parser.parse_args()

    # Default to "all" if no command is provided to preserve original behavior
    if not args.command:
        args.command = "all"

    mm = login()

    if args.command == "subscriptions":
        get_subscription_details(mm)
    elif args.command == "accounts":
        get_accounts(mm)
    elif args.command == "institutions":
        get_institutions(mm)
    elif args.command == "budgets":
        get_budgets(mm)
    elif args.command == "transactions_summary":
        get_transactions_summary(mm)
    elif args.command == "categories":
        get_categories(mm)
    elif args.command == "transactions":
        get_transactions(mm)
    elif args.command == "cashflow":
        income_categories, expense_category_groups = get_categories(mm)
        get_cashflow(mm, income_categories, expense_category_groups)
    elif args.command == "all":
        run_all(mm)


if __name__ == "__main__":
    main()
