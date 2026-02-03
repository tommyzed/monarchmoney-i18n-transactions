# Monarch Euro Bridge 🇪🇺🌉🇺🇸

**Seamlessly import international (EUR) transaction receipts into Monarch Money.**

Monarch Money is amazing, but it lacks native support for European banks and currencies. This application bridges that gap by allowing you to "Share" a receipt image from your phone directly to your Monarch account, automatically handling OCR, currency conversion, and upload.

## ✨ Features

*   **🇪🇺 Automatic Currency Conversion**: Detects EUR amounts and converts them to USD using historical exchange rates (via Frankfurter API) for the exact transaction date.
*   **🧙‍♂️ AI-Powered OCR**: Uses **Google Gemini 3 Flash** to instantly extract Merchant, Date, and Amount from receipt photos with high accuracy.
*   **📱 Native-Like PWA Experience**:
    *   **Installable**: Add to your home screen as a standalone app.
    *   **Share Target**: Appears in your phone's native "Share" sheet for images.
    *   **Instant UI**: Service Worker interception ensures you see the "Processing" animation instantly, even used continuously offline or on slow networks.
*   **🔒 Secure & Private**:
    *   Runs locally on your server/computer.
    *   Credentials encrypted at rest (Fernet).
    *   No logs of sensitive financial data.
*   **🤖 Smart Monarch Integration**:
    *   Auto-tags transactions (`Imported by MM Euro Bridge`).
    *   Marks as `Needs Review` for easy workflows.
    *   Stores `Original Amount: €XX.XX` and the ForEx Rate in the notes.

## 🖼 Screenshots
<img width="125" height="280" alt="Screenshot_20260127-223044" src="https://github.com/user-attachments/assets/3253a592-4479-4d75-8610-af21a0a57dea" /> 
<img width="125" height="280" alt="Screenshot_20260127-223044" src="https://github.com/user-attachments/assets/718a7ec0-b122-4f31-a3a9-de46ed2f40c4" />  
<img width="125" height="280" alt="Screenshot_20260127-223044" src="https://github.com/user-attachments/assets/f15f1821-3083-449a-b179-458383d2103c" /> 
<img width="125" height="280" alt="Screenshot_20260127-223044" src="https://github.com/user-attachments/assets/372631a6-ab94-4b60-a445-d364780ddb9b" /> 
<img width="125" height="280" alt="Screenshot_20260127-223044" src="https://github.com/user-attachments/assets/38dda35f-6d34-4541-bbe1-6fc0e84aa8d2" /> 
https://github.com/user-attachments/assets/dee51831-0da3-47e6-b30f-b2045876ce46


## 🏗 Architecture

The system is a lightweight **FastAPI** application backed by **PostgreSQL**.

### Core Services
1.  **Orchestrator**: The brain. Hashing -> De-duplication -> OCR -> Conversion -> Push.
2.  **Monarch Service**: Handles authentication (including MFA), session persistence, and GraphQL interactions.
3.  **Gemini Service**: Interacts with Google's GenAI SDK for image parsing.
4.  **Currency Service**: Fetches historical forex rates.

## 🚀 Getting Started

### 1. Prerequisites
*   Python 3.10+
*   PostgreSQL (Local or Cloud like Neon.tech)
*   Google Cloud API Key (with Gemini API access)
*   Monarch Money Account

### 2. Installation

Clone the repo and set up the environment:

```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 3. Configuration

Create a `.env` file in the root directory:

```bash
# Database Connection
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/dbname"

# Security (Encryption key for credentials)
# Generate one with: python scripts/generate_key.py
export FERNET_KEY="<your_generated_key>"

# AI (Google Gemini)
export GEMINI_API_KEY="<your_gemini_api_key>"
export GEMINI_MODEL="gemini-3-flash-preview"

# Monarch Settings
export MM_EMAIL="<your_monarch_email>"
export MM_PWD="<your_monarch_password>"
export MM_ACCOUNT="Euro Transactions" # The name of the manual cash account in Monarch

# Security (Ghost Mode)
export UNLOCK_SECRET="<random_secret>" # Set this to a secret string
```

### 4. First Run

Run the interactive login script to authenticate with Monarch. This will verify your credentials and store a secure session token.

```bash
venv/bin/python3 scripts/interactive_login.py
```

- or -

```bash
venv/bin/python3 scripts/seed_session_token.py
```

### 5. Start the Server

```bash
venv/bin/uvicorn bridge_app.main:app --host 0.0.0.0 --port 8000 --reload
```

## 🔒 Security & Ghost Mode 👻

To prevent unauthorized access, the app uses a "Ghost Cookie" mechanism.

1.  **Configure**: Set `UNLOCK_SECRET` in your `.env` file.
2.  **Activate**: On your phone, visit:
    `http://<your-server>:8000/s?s=<YOUR_SECRET>`
3.  **Unlock**: You will see a "Device Activated" screen. This sets a secure cookie valid for 10 years.
4.  **Ghosting**: Any subsequent request *without* this cookie (e.g. random scanners) will receive a `404 Not Found`, making the server appear non-existent.

## 📱 Mobile Setup (PWA)

1.  **Expose the Server**: Ensure your phone can reach the server (e.g., via local Wi-Fi IP `http://192.168.1.X:8000`, Tailscale, or a tunnel like Ngrok).
    *   *Note: For the Service Worker and PWA install features to work fully, you usually need HTTPS unless using localhost.*
2.  **Visit in Browser**: Open the URL on your mobile browser (Chrome/Safari).
3.  **Install**: Tap "Add to Home Screen" in your browser menu.
4.  **Use**: 
    *   Open your **Photos** or **Gallery** app.
    *   Select a receipt.
    *   Tap **Share**.
    *   Select **Monarch Bridge**.
    *   🦄 Watch the magic happen!

## 🛠 Management Scripts

*   **`python scripts/reset_transactions.py`**: Clears the local "processed" cache. Useful if you want to re-upload a receipt that was previously marked as duplicate.
*   **`python scripts/interactive_login.py`**: Re-authenticate if your session expires.

## 🔮 Roadmap

*   [ ] **Docker Support**: Containerize for easy NAS deployment.
*   [ ] **Alembic Migrations**: Proper database schema management.
*   [ ] **Multi-User Support**: Support multiple Monarch accounts/users.
*   [ ] **Secure Remote Access**: Add Basic Auth or OAuth for the web interface.

## 📂 Project Structure

```text
bridge_app/
├── main.py              # FastAPI entry point & API routes
├── database.py          # Database connection & session info
├── models.py            # SQLAlchemy database models
├── services/            # Business logic modules
│   ├── gemini.py        # OCR logic
│   ├── monarch.py       # Monarch API interaction
│   └── orchestrator.py  # Pipeline coordination
└── static/              # Frontend assets
    ├── index.html       # PWA entry point
    ├── sw.js            # Service Worker (Offline & Share Target)
    └── manifest.json    # App Manifest
```

## 🙏 Acknowledgements

This project is a fork of [monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity). Huge thanks to **BradleySeanF** and all contributors for building the foundation of the Monarch Money API wrapper and community tools! 🚀
