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
import pickle
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


def get_clipboard_text() -> str:
    """Attempt to retrieve text from system clipboard using native OS commands."""
    try:
        import subprocess
        if sys.platform == "darwin":
            res = subprocess.run(["pbpaste"], capture_output=True, text=True, check=False)
            return res.stdout.strip()
        elif sys.platform.startswith("linux"):
            for cmd in [["xclip", "-selection", "clipboard", "-o"], ["wl-paste"]]:
                try:
                    res = subprocess.run(cmd, capture_output=True, text=True, check=False)
                    if res.returncode == 0 and res.stdout.strip():
                        return res.stdout.strip()
                except FileNotFoundError:
                    continue
    except Exception:
        pass
    return ""


def get_cookie_string() -> str:
    """Obtain cookie string from CLI arg, file, stdin pipe, clipboard, or interactive input."""
    # 1. Check CLI arguments
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if os.path.isfile(arg):
            print(f"📄 Reading cookie string from file: {arg}")
            with open(arg, "r", encoding="utf-8") as f:
                return f.read().strip()
        elif arg.lower() in ("--clipboard", "-c"):
            clip = get_clipboard_text()
            if clip:
                print("📋 Loaded cookie string from clipboard.")
                return clip
            else:
                print("⚠️ Clipboard is empty or unreadable.")
        elif "=" in arg or ";" in arg:
            return arg.strip()

    # 2. Check if piped/redirected stdin
    if not sys.stdin.isatty():
        print("📥 Reading cookie string from piped stdin...")
        return sys.stdin.read().strip()

    # 3. Interactive TTY mode
    clipboard_text = get_clipboard_text()
    has_cookie_in_clip = "session_id" in clipboard_text or "csrftoken" in clipboard_text or ";" in clipboard_text

    print("Choose input method:")
    if clipboard_text and has_cookie_in_clip:
        print(f"  [1] Use clipboard content (Detected ~{len(clipboard_text)} chars) (DEFAULT)")
        print("  [2] Read from a file path")
        print("  [3] Paste manually into terminal")
        choice = input("\nSelect option [1/2/3] (default 1): ").strip()
        if choice in ("", "1"):
            print("📋 Using clipboard content.")
            return clipboard_text
        elif choice == "2":
            filepath = input("Enter file path containing cookie string: ").strip()
            if os.path.isfile(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    return f.read().strip()
            else:
                print(f"❌ File not found: {filepath}")
                return ""
    else:
        print("  [1] Read from a file path (recommended for very long strings)")
        print("  [2] Read from system clipboard")
        print("  [3] Paste manually into terminal")
        choice = input("\nSelect option [1/2/3]: ").strip()
        if choice == "2":
            clip = get_clipboard_text()
            if clip:
                print("📋 Using clipboard content.")
                return clip
            else:
                print("⚠️ Clipboard is empty or unreadable.")
        elif choice == "1":
            filepath = input("Enter file path containing cookie string: ").strip()
            if os.path.isfile(filepath):
                with open(filepath, "r", encoding="utf-8") as f:
                    return f.read().strip()
            else:
                print(f"❌ File not found: {filepath}")
                return ""

    # Fallback to manual terminal paste
    print("\nPaste your browser cookie string below, then press ENTER on a blank line (or Ctrl+D when finished):")
    lines = []
    try:
        while True:
            line = sys.stdin.readline()
            if not line:  # EOF
                break
            stripped = line.rstrip("\r\n")
            if stripped == "" and lines:
                break
            if stripped:
                lines.append(stripped)
    except KeyboardInterrupt:
        pass
    return "".join(lines).strip()


async def manual_session_save():
    print("=" * 60)
    print("  Monarch Money — Cookie Login")
    print("=" * 60)
    print()

    cookie_string = get_cookie_string()

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

    # Build legacy pickle blob for backward compat
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
    session_bytes = pickle.dumps({"token": None, "headers": headers})

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
