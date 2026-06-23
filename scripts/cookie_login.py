"""
cookie_login.py — Interactive cookie injection for Monarch Money auth.

Use this when `interactive_login.py` is blocked by Cloudflare (429).
Since you're already logged in via your browser, we bypass the login API
entirely and inject the cookies directly from your browser session.

HOW TO GET YOUR COOKIES:
  1. Go to https://app.monarch.com in your browser (make sure you're logged in)
  2. Open DevTools → Network tab → reload the page
  3. Click any request to api.monarch.com
  4. Under "Request Headers", find the `cookie:` header — copy the ENTIRE value
  5. Run this script and paste when prompted (nothing will be saved to disk)
"""

import asyncio
import os
import sys
import json
from datetime import datetime, timezone

sys.path.append(os.getcwd())
from dotenv import load_dotenv
load_dotenv(override=True)

from bridge_app.database import get_db
from bridge_app.models import Credentials


def parse_cookie_string(cookie_str: str) -> dict:
    """Parse a raw 'cookie' header string into a key→value dict."""
    cookies = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            key, _, value = part.partition("=")
            cookies[key.strip()] = value.strip()
    return cookies


async def manual_session_save():
    print("=" * 60)
    print("  Monarch Money — Cookie Login")
    print("=" * 60)
    print()
    print("Paste your browser cookie string below, then press ENTER on a blank line.")
    print("(DevTools → Network → any api.monarch.com request → Request Headers → cookie:)\n")

    lines = []
    while True:
        line = sys.stdin.readline()
        # readline() returns '\n' for blank lines and '' for EOF
        stripped = line.rstrip("\n")
        if stripped == "" and lines:
            break
        if stripped:
            lines.append(stripped)
    cookie_string = "".join(lines).strip()

    if not cookie_string:
        print("❌ No cookie string provided. Aborting.")
        return

    cookie_dict = parse_cookie_string(cookie_string)
    csrf = cookie_dict.get("csrftoken")

    # Allow overriding csrftoken explicitly if not found in cookie string
    if not csrf:
        print("\n⚠️  No csrftoken found in cookies.")
        csrf_input = input("Paste your x-csrftoken value (or press ENTER to skip): ").strip()
        if csrf_input:
            csrf = csrf_input
            cookie_dict["csrftoken"] = csrf

    print(f"\n📦 Parsed {len(cookie_dict)} cookies: {list(cookie_dict.keys())}")

    session_id = cookie_dict.get("session_id")
    if not csrf:
        print("⚠️  Warning: no csrftoken found — GraphQL calls may fail!")
    if not session_id:
        print("⚠️  Warning: no session_id found — session may not persist!")

    # Build legacy session blob for backward compat
    headers = {
        "Accept": "*/*",
        "Client-Platform": "web",
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "monarch-client": "web",
        "monarch-client-version": "2025.05",
        "cookie": cookie_string,
        "X-Csrftoken": csrf or "",
    }
    session_bytes = json.dumps({"token": None, "headers": headers}).encode("utf-8")

    email = os.getenv("MM_EMAIL") or input("\nEmail to save session for: ").strip()
    print(f"\n💾 Saving cookies for {email}...")

    try:
        async for db in get_db():
            from sqlalchemy import select
            result = await db.execute(select(Credentials).where(Credentials.email == email))
            creds = result.scalar_one_or_none()

            now = datetime.now(timezone.utc)

            if creds:
                print(f"Updating existing credentials for {email}...")
                creds.monarch_cookies = cookie_dict
                creds.monarch_session = session_bytes
                creds.monarch_token = None
                creds.last_update_date = now
            else:
                print(f"User {email} not found — creating new record.")
                from bridge_app.utils.crypto import encrypt
                payload = encrypt('{"password": "", "mfa_secret": ""}')
                creds = Credentials(
                    email=email,
                    encrypted_payload=payload,
                    monarch_cookies=cookie_dict,
                    monarch_session=session_bytes,
                    monarch_token=None,
                    last_update_date=now,
                )
                db.add(creds)

            await db.commit()
            print("\n✅ Cookies saved to database successfully!")
            print(f"   csrftoken:  {csrf[:20] if csrf else 'MISSING'}...")
            print(f"   session_id: {session_id[:20] if session_id else 'MISSING'}...")
            print(f"   saved at:   {now.isoformat()}")
            break

    except Exception as e:
        print(f"\n❌ Exception during DB operation: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(manual_session_save())
