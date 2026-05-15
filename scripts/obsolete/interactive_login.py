import asyncio
import os
import sys
import pickle
from dotenv import load_dotenv

# Add project root to path
sys.path.append(os.getcwd())

from bridge_app.database import get_db
from bridge_app.models import Credentials
from monarchmoney import MonarchMoney, RequireMFAException

load_dotenv()

async def interactive_login_flow():
    print("Monarch Interactive Login")
    print("-------------------------")
    print("This script will log you in and save your session to the database.")
    
    mm = MonarchMoney()
    
    # OVERRIDE HEADERS to look like a real browser
    # This helps bypass Cloudflare 525/403 blocks often caused by bot User-Agents
    mm._headers["User-Agent"] = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    email = os.getenv("MM_EMAIL")
    password = os.getenv("MM_PWD")

    try:
        if email and password:
            print(f"Using credentials from .env for {email}...")
            try:
                await mm.login(email, password, save_session=False)
            except RequireMFAException:
                print("MFA Required")
                code = input("MFA Code Required: ")
                await mm.multi_factor_authenticate(email, password, code)
        else:
            await mm.interactive_login(save_session=False)
            # If interactive login succeeds, we need to extract email for later?
            # interactive_login doesn't return email, but we can assume user knows it.
            
    except Exception as e:
        import traceback
        traceback.print_exc()
        msg = str(e)
        if "429" in msg:
            print(msg)
            print(f"\n❌ Rate Limit Reached (HTTP 429). The Monarch server is blocking requests due to too many login attempts.")
            print("Please wait 15-30 minutes before trying again.")
        else:
            print(f"Login failed: {e}")
        return

    # The cookie jar from login (primary auth — new Monarch security model)
    cookie_jar = mm._cookie_jar or {}
    if cookie_jar:
        print(f"✅ Got cookie jar with {len(cookie_jar)} cookies (csrftoken={'csrftoken' in cookie_jar})")
    else:
        print("⚠️  No cookies captured — auth will fall back to token/pickle")

    # Legacy: pickle session bytes (kept for backward compat)
    session_data = {"token": mm._token, "headers": mm._headers}
    session_bytes = pickle.dumps(session_data)
    long_lived_token = mm._token

    # Save to user's credentials in DB
    if not email:
        email = input("Confirm your email to save session to: ").strip()
    else:
        print(f"Saving session for {email}...")

    from datetime import datetime, timezone
    async for db in get_db():
        from sqlalchemy import select
        result = await db.execute(select(Credentials).where(Credentials.email == email))
        creds = result.scalar_one_or_none()

        if creds:
            print(f"Updating credentials for {email}...")
            creds.monarch_cookies = cookie_jar or None
            creds.monarch_session = session_bytes
            creds.monarch_token = long_lived_token
            creds.last_update_date = datetime.now(timezone.utc)
        else:
            print(f"User {email} not found in DB. Creating placeholder.")
            from bridge_app.utils.crypto import encrypt
            payload = encrypt('{"password": "", "mfa_secret": ""}')
            creds = Credentials(
                email=email,
                encrypted_payload=payload,
                monarch_cookies=cookie_jar or None,
                monarch_session=session_bytes,
                monarch_token=long_lived_token,
                last_update_date=datetime.now(timezone.utc),
            )
            db.add(creds)

        await db.commit()
        print("✅ Credentials saved to database!")
        if cookie_jar:
            print("🎉 Cookie-based auth stored — this is the new long-lived Monarch session format!")
        elif long_lived_token:
            print("🎉 Long-lived token stored (legacy format).")
        break


if __name__ == "__main__":
    try:
        asyncio.run(interactive_login_flow())
    except KeyboardInterrupt:
        print("\nCancelled.")
