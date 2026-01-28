# Monarch Euro Bridge 🇪🇺🌉🇺🇸

A bridge application to import international (EUR) transactions into Monarch Money.

Since Monarch Money currently focuses on the US/Canada markets, it lacks native support for European banks and currencies. This bridge allows you to "Share" a receipt image (or upload it) to a local server, which parses the details, converts the currency to USD, and pushes it to Monarch as a manual transaction.

## 🏗 Architecture & Components

The system is built as a lightweight **FastAPI** application designed to run locally or on a personal server.

### Core Services (`bridge_app/services/`)
1.  **Gemini OCR (`gemini.py`)**: Uses Google's Gemini 2.0 Flash model to extract structured data (Date, Amount, Merchant, Currency) from receipt images.
2.  **Currency Converter (`currency.py`)**: Automatically detects EUR transactions and converts them to USD using historical rates from the **Frankfurter API** for the specific transaction date.
3.  **Monarch Integration (`monarch.py`)**:
    *   Uses a modified `monarchmoney` library to interact with the private GraphQL API.
    *   Handling **Interactive Login** (MFA support).
    *   **Session Persistence**: Stores auth session in the local database to avoid repeated logins.
    *   **Auto-Tagging**: Tags Imported transactions with "Imported by MM Euro Bridge".
    *   **Needs Review**: Marks new transactions as "Needs Review" for easy finding.
4.  **Orchestrator (`orchestrator.py`)**: Ties it all together: Hash Image -> Check Duplicate -> OCR -> Convert -> Push -> Save Record.

### Data Storage (`bridge_app/models.py`)
*   **PostgreSQL**: Used for robustness (compatible with Neon/cloud providers).
*   **Transactions**: Stores hashes of processed images to prevent duplicate uploads.
*   **Credentials**: Securely stores your Monarch login session (encrypted).

## 📂 Project Structure

```text
bridge_app/
├── main.py              # FastAPI entry point
├── database.py          # Database connection
├── models.py            # SQLAlchemy models
├── services/
├── gemini.py        # OCR with Gemini
├── monarch.py       # Monarch API wrapper
└── orchestrator.py  # Core logic
├── utils/
└── crypto.py        # Fernet encryption
└── static/
├── manifest.json    # PWA Manifest
├── index.html       # Landing page / Install Prompt
└── sw.js            # Service Worker
```

## 🚀 Setup & Usage

### 1. Prerequisites
*   Python 3.10+
*   A Monarch Money account
*   A Google Cloud Project with **Gemini API** access
*   PostgreSQL (local or remote, e.g. Neon)

### 2. Environment Variables
Create a `.env` file in the root directory:

```bash
# Database
export DATABASE_URL="postgresql+asyncpg://user:pass@localhost/dbname"

# Security (Encryption key for credentials)
export FERNET_KEY="<generated_key>"

# AI
export GEMINI_API_KEY="<your_gemini_api_key>"

# Monarch Configuration
export MM_ACCOUNT="Euro Transactions" # Optional: Defaults to "Euro Transactions"
```

To generate a secure `FERNET_KEY`, run the helper script:
```bash
python scripts/generate_key.py
```

### 3. Installation
```bash
# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt
```

### 4. First Run (Authentication)
Run the interactive login script to verify your Monarch credentials and save the session to the database.
```bash
python scripts/interactive_login.py
```
*   It will ask for your MFA code (if enabled).
*   It handles Rate Limit (429) warnings gracefully.

### 5. Running the Server
```bash
uvicorn bridge_app.main:app --reload
```
The server will start at `http://127.0.0.1:8000`.

### 6. Usage (Mobile Share Target)
The app is designed to be a PWA or simple web target.
1.  Navigate to `http://127.0.0.1:8000` on your phone (if exposed via Tailscale/Ngrok).
2.  "Add to Home Screen" to install it.
3.  Go to your Photos -> Share Receipt Image -> Select "Monarch Bridge".
4.  **Result**: 
    *   ✅ Parsed & Converted
    *   ✅ Uploaded to "Euro Transactions" account (USD)
    *   ✅ Original amount in Notes
    *   ✅ Tagged "Imported by MM Euro Bridge"

## 🛠 Management Scripts

*   **Reset History**: Clears the local duplicate cache (allowing re-uploads) without logging you out.
    ```bash
    python scripts/reset_transactions.py
    ```
*   **Login**: Refreshes session.
    ```bash
    python scripts/interactive_login.py
    ```
*   **Seeding Credentials Manually**:
    If you need to seed credentials via script (e.g. on a headless server without interactive input):
    ```python
    # seed_creds.py
    import asyncio
    from bridge_app.database import get_db
    from bridge_app.models import Credentials
    from bridge_app.utils.crypto import encrypt
    import json
    
    async def seed():
        async for db in get_db():
            payload = json.dumps({"password": "YOUR_PASS", "mfa_secret": "YOUR_SECRET"})
            c = Credentials(email="your@email.com", encrypted_payload=encrypt(payload))
            db.add(c)
            await db.commit()
    
    if __name__ == "__main__":
        import asyncio
        asyncio.run(seed())
    ```

## 🔮 Roadmap (Productionize)

*   [ ] **Docker Image**: Containerize the app for easy deployment on a NAS or VPS.
*   [ ] **Secure Remote Access**: Currently relies on `localhost` or VPN (Tailscale). Adding basic HTTP Auth for valid endpoints would be safer for public exposure.
*   [ ] **Alembic Migrations**: Currently uses `Base.metadata.create_all`, which is fine for dev but bad for schema changes.
*   [ ] **Frontend Polish**: Improve the "Success" screen to show more details or a history of recent uploads.
*   [ ] **Multi-User Support**: Currently optimized for a single household.
