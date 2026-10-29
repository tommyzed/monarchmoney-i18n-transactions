import asyncio
import os
import sys

# Add project root to path
sys.path.append(os.getcwd())

from bridge_app.database import engine, DATABASE_URL
from bridge_app.models import Transaction
from sqlalchemy import delete
from dotenv import load_dotenv

load_dotenv()

async def reset_transactions():
    target = DATABASE_URL.split('@')[-1]
    print(f"WARNING: This will delete all transaction history from: {target}")
    print("This resets the duplicate detection cache.")
    print("It will NOT delete transactions from Monarch Money.")
    
    confirm = input("Are you sure? (y/N): ")
    if confirm.lower() != 'y':
        print("Cancelled.")
        return

    async with engine.begin() as conn:
        await conn.execute(delete(Transaction))
        print("All transaction records deleted successfully.")

if __name__ == "__main__":
    try:
        asyncio.run(reset_transactions())
    except Exception as e:
        print(f"Error: {e}")
