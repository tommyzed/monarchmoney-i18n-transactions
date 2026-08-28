# Monarch Money Bridge 🇪🇺🇺🇸

> 🎁 **New to Monarch Money?** Sign up using this [referral link](https://www.monarch.com/referral?r_source=copy&code=42woh1ux8n)!

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
*   **📊 v2.0: Spending & Cash Flow Analysis Dashboard**:
    -   **Interactive Spending Dashboard**: Dedicated, mobile-friendly analytics dashboard (`/spending`) with responsive ECharts visualizations, KPI metrics, and dark theme design.
    -   **Executive Cash Flow KPIs**: Instant metrics for Total Net Spending, Total Inflow Income, Net Savings, and Savings Rate % across any calendar year.
    -   **Monthly Spend Trends**: Interactive monthly spending breakdown chart with dynamic monthly average benchmarking.
    -   **Category Groups & Donut Charts**: Visual group distribution breakdown with percentage of total spend and Year-over-Year (YoY) percentage comparisons against the previous year.
    -   **Ranked Category Breakdown**: Searchable, itemized category table with real-time filtering and spending totals.
    -   **Dynamic Data Freshness**: Visual freshness pills indicating whether snapshot data is current, stale, syncing, or locked for historical years, with one-click background recalculation.
    -   **CLI Report Generator (`scripts/spending_report.py`)**: Standalone read-only command-line script to analyze Monarch cash flows and itemized transactions with terminal tables, JSON export, and database sync options.
    -   **Failed Transaction Queue & Retry**: Persistent database store (`failed_transactions`) for failed receipt scans and manual submissions, with a dedicated review modal, editing capabilities, and single/batch retry mechanisms.
    -   **Starred / Favorite Merchants**: Mark and prioritize frequently used merchants for fast lookup and automated suggestions.
*   **⚡ v2.5: Multi-Receipt Batch Upload & Processing**:
    -   **Multi-File Selection & Drag-and-Drop**: Upload up to 20 receipt images simultaneously via the file picker or drag-and-drop.
    -   **Interactive Thumbnail Staging Strip**: Preview selected receipt thumbnails with per-item remove buttons (`✕`), plus an interactive **"+" add card** to append additional receipts to the batch queue.
    -   **Controlled Concurrency Worker Pool**: Processes items concurrently using `asyncio.Semaphore(2)` to balance throughput while avoiding Gemini API rate limits and database contention.
    -   **Monarch Session Reuse**: Pre-authenticates the `MonarchMoney` client once per batch and shares it across worker tasks, eliminating redundant per-item authentication latency.
    -   **Live Continuous Progress Dashboard**: Real-time aggregate progress bar computing weighted sub-step completion across all items with 400ms polling, live status cards (`⏳ Queued`, `🧙‍♂️ Scanning (35%)`, `✅ Synced`, `🔄 Duplicate`, `⚠️ Failed`), and detailed step descriptions.
    -   **Post-Batch Interactive Review & Manual Editing**: Click on any completed or duplicate receipt card in the batch dashboard to open the full transaction detail modal:
        -   **⭐ Star Merchants**: Toggle favorite merchant status directly.
        -   **📅 Date Correction**: Adjust dates with automatic historical exchange rate recalculation and live Monarch sync.
        -   **🏷️ Category Selection**: Change transaction categories via inline dropdown.
        -   **⚙️ Auto-Mapping Rules**: Create or update merchant mapping rules on the fly.
        -   **⚡ Duplicate Force Mode**: Force-sync duplicate receipts to Monarch with one click.
        -   **← Back to Batch Navigation**: Return cleanly to the batch dashboard with all edits updated in real time.
    -   **Resilient Error Recovery**: Any failed receipts are automatically persisted to the `FailedTransaction` database store, with one-click "🔄 Retry Failed" support.
    -   **100% Backward Compatible**: Single-file uploads seamlessly continue to use the classic upload and review pathway.

## 🖼 Demos / Screenshots

Single Mode and Batch Mode (v2.5+)
<p align="center">
  <img width="324" src="https://github.com/user-attachments/assets/b4fefae9-ff0d-4cf5-a2b3-71befc6e29d8" alt="Single Mode Demo" />
  &nbsp;&nbsp;&nbsp;&nbsp;
  <img width="324" src="https://github.com/user-attachments/assets/ae047f66-73c1-4064-8b40-c628cb72da13" alt="Batch Mode Demo" />
</p>

Spending Reports (v2.0+)
<p align="center">
<img width="764" height="615" alt="Spending Report 1" src="https://github.com/user-attachments/assets/23b2c28b-ff75-43ea-bc27-2ca1d7b4c2dd" />
<img width="786" height="500" alt="Spending Report 2" src="https://github.com/user-attachments/assets/06b23dde-b0e8-49bf-8357-995cbbbdf1c3" />
<img width="758" height="686" alt="Spending Report 3" src="https://github.com/user-attachments/assets/567db11e-7d72-4905-b3ae-60d35bd02842" />
</p>

## 🏗 Architecture

The system is a lightweight **FastAPI** application backed by **PostgreSQL**.

### Core Services
1.  **Orchestrator**: The brain. Handles three flows:
    *   **Single Image Flow**: Hashing -> De-duplication -> OCR -> Conversion -> Push.
    *   **Batch Image Flow**: Concurrency Semaphore(2) -> Shared Monarch Session -> Background Processing -> Streaming Status -> In-Place Review.
    *   **Manual Flow**: Form Data -> Hashing -> Conversion -> Push.
2.  **Monarch Service**: Handles authentication (including MFA and session cookies), session persistence, and GraphQL interactions.
3.  **Gemini Service**: Interacts with Google's GenAI SDK for image parsing. Accepts an optional list of historical merchant names to hint the model toward canonical names.
4.  **Currency Service**: Fetches historical forex rates (via Frankfurter API).
5.  **FIRE Engine**: Runs Monte Carlo simulations and safe withdrawal rate analysis against live portfolio data.
6.  **Spending Service**: Read-only cash flow and transaction aggregation engine computing annual summaries, category breakdowns, monthly trends, and database caching.

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

*   **`python scripts/spending_report.py`**: Generates a read-only spending and cash flow report in your terminal (supports `--year`, `--start-date`, `--end-date`, `--top`, `--json`, `--save-db`, `--include-hidden`).
*   **`python scripts/cookie_login.py`**: Authenticates and stores session cookies securely for Monarch Money access.
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
│   ├── currency.py      # Historical forex exchange rates
│   ├── fire_engine.py   # FIRE Monte Carlo simulation engine
│   ├── gemini.py        # OCR logic & AI merchant hinting
│   ├── monarch.py       # Monarch API interaction & session management
│   ├── orchestrator.py  # Pipeline coordination
│   └── spending_service.py # Annual spending & cash flow aggregation
└── static/              # Frontend assets
    ├── fire.html        # 🔥 Ignite FIRE dashboard
    ├── index.html       # PWA entry point
    ├── spending.html    # 📊 Spending & Cash Flow report dashboard
    ├── sw.js            # Service Worker (Offline & Share Target)
    └── manifest.json    # App Manifest
```

## 🙏 Acknowledgements

This project is a fork of [monarchmoneycommunity](https://github.com/bradleyseanf/monarchmoneycommunity). Huge thanks to **BradleySeanF** and all contributors for building the foundation of the Monarch Money API wrapper and community tools! 🚀
