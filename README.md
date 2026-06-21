# Monarch Money Bridge 🇪🇺🇺🇸

**Seamlessly import international (EUR, GBP, etc) transaction receipts into Monarch Money.**

Monarch Money is amazing, but it lacks native support for foreign banks and currencies. This application bridges that gap by allowing you to "Share" a receipt image from your phone directly to your Monarch account, automatically handling OCR, currency conversion, and upload.

## ✨ Features

*   **🇪🇺 Automatic Currency Conversion**: Detects EUR/GBP/etc amounts and converts them to USD using historical exchange rates (via Frankfurter API) for the exact transaction date.
*   **✍️ Manual Entry Mode**: Quickly add transactions manually (Amount, Currency, Date, Merchant) without needing a receipt image. Supports any currency.
*   **🧙‍♂️ AI-Powered OCR**: Uses **Google Gemini 3** to instantly extract Merchant, Date, and Amount from receipt photos with high accuracy.
*   **📱 Native-Like PWA Experience**:
    *   **Installable**: Add to your home screen as a standalone app.
    *   **Share Target**: Appears in your phone's native "Share" sheet for images.
    *   **Instant UI**: Service Worker interception ensures you see the "Processing" animation instantly, even used continuously offline or on slow networks.
*   **🔒 Secure & Private**:
    *   Option to run locally on your server/computer.
    *   Credentials encrypted at rest (Fernet).
    *   No logs of sensitive financial data.
*   **🤖 v1.2: Smart Monarch Integration**:
    *   **Auto-tags transactions** (`Imported by MM Bridge`).
    *   **Auto-Mapping Engine**: Define rules to automatically rename merchants and assign categories.
    *   **Edit Mapping**: Edit/Update merchant names and categories on the fly via a new modal.
    *   **Sync Categories**: Import your Monarch categories (including emojis 🍔) for easy mapping.
    *   **Stores Metadata**: Saves `Original Amount: €XX.XX` and ForEx Rate in notes.
*   **🔥 v1.3: Ignite FIRE Engine**:
    *   **Monte Carlo Simulations**: Runs 10,000 real-time Monte Carlo simulations against your live Monarch portfolio.
    *   **Dynamic Safe Withdrawal Rate**: Calculates the safest withdrawal amount by hunting for a ≥95% success rate over a 30-year horizon.
    *   **Custom Assumptions**: Adjust inflation, expected market returns, retirement age, and risk volatility directly in the UI.
*   **🧠 v1.4: AI Merchant Hinting**:
    *   **Historical Merchant Hints**: Before each OCR scan, the Gemini agent receives the full list of previously seen `monarch_merchant_names` from the mapping table as context.
    *   **Confidence-Based Matching**: If Gemini is ≥75% confident the receipt merchant matches a historical name, it returns the canonical historical name exactly. Below 75%, the raw OCR name is used instead.
    *   **Auto-Category Resolution**: When a historical name is matched, the associated category is automatically looked up from the mapping table and applied — no manual step required.
    *   **💜 Result Card Indicators**: The Merchant and Category fields display a 💜 prefix when a historical name was used, with a legend below the result card. The "Add Mapping" button is hidden in this case since the merchant is already mapped.
    *   **New API Endpoint**: `GET /api/merchant-names` returns the sorted, distinct list of Monarch merchant names for use by other tools.
*   **📋 v1.5: Hamburger Navigation & History Log**:
    -   **Unified Hamburger Menu**: Replaced the floating links with a clean, CSS-animated hamburger menu that morphs into an "X" when clicked. Consolidates both the FIRE Dashboard and deep links.
    -   **Transaction History Modal**: Access a modal from the menu displaying a table of the last 10 transactions processed by the app.
    -   **Visual Enhancements**: Displays merchant names with a `💵` cash emoji for cash transactions, color-codes debits in red (-) and credits in green (+), and shows pre-converted foreign currency amounts.
    -   **Deep Link Integration**: Includes clickable deep links to navigate directly to each transaction within the Monarch mobile app (or desktop browser fallback).
    -   **Automatic Synchronization**: Endpoints such as transaction date/amount updates automatically keep the history log database in sync.

## 🖼 Demo (v1.1 only)

![LatestMMDemo-ezgif com-speed](https://github.com/user-attachments/assets/b4fefae9-ff0d-4cf5-a2b3-71befc6e29d8)

## 🏗 Architecture

The system is a lightweight **FastAPI** application backed by **PostgreSQL**.

### Core Services
1.  **Orchestrator**: The brain. Handles two flows:
    *   **Image Flow**: Hashing -> De-duplication -> OCR -> Conversion -> Push.
    *   **Manual Flow**: Form Data -> Hashing -> Conversion -> Push.
2.  **Monarch Service**: Handles authentication (including MFA), session persistence, and GraphQL interactions.
3.  **Gemini Service**: Interacts with Google's GenAI SDK for image parsing. Accepts an optional list of historical merchant names to hint the model toward canonical names.
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
export GEMINI_MODEL="gemini-3.5-flash"

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

## 📱 Mobile Usage

### Option A: Share Sheet (Images)
1.  Open your **Photos** or **Gallery** app.
2.  Select a receipt.
3.  Tap **Share**.
4.  Select **Monarch Bridge**.
5.  🦄 Watch the magic happen!

### Option B: Manual Entry
1.  Open the app in your browser (or PWA).
2.  Tap **"Or enter manually ✍️"** on the home screen.
3.  Enter amount, select currency, date, and merchant.
4.  Tap **Submit**.

## 🛠 Management Scripts

*   **`python scripts/reset_transactions.py`**: Clears the local "processed" cache. Useful if you want to re-upload a receipt that was previously marked as duplicate.
*   **`python scripts/interactive_login.py`**: Re-authenticate if your session expires.
*   **`python scripts/sync_categories.py`**: Imports categories (and emojis) from your Monarch account to the local database for mapping.

## 🔮 Roadmap

*   [ ] **Multi-User Support**: Support multiple Monarch accounts/users.

## 📂 Project Structure

```text
bridge_app/
├── main.py              # FastAPI entry point & API routes
├── database.py          # Database connection & session info
├── models.py            # SQLAlchemy database models
├── services/            # Business logic modules
│   ├── fire_engine.py   # FIRE Monte Carlo simulation engine
│   ├── gemini.py        # OCR logic
│   ├── monarch.py       # Monarch API interaction
│   └── orchestrator.py  # Pipeline coordination
└── static/              # Frontend assets
    ├── fire.html        # 🔥 Ignite FIRE dashboard
    ├── index.html       # PWA entry point
    ├── sw.js            # Service Worker (Offline & Share Target)
    └── manifest.json    # App Manifest
```

## 🙏 Acknowledgements

This project is a fork of [monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity). Huge thanks to **BradleySeanF** and all contributors for building the foundation of the Monarch Money API wrapper and community tools! 🚀
