-- Credentials Table
CREATE TABLE IF NOT EXISTS credentials (
    id SERIAL PRIMARY KEY,
    email VARCHAR NOT NULL UNIQUE,
    encrypted_payload BYTEA NOT NULL,
    monarch_session BYTEA
);

CREATE INDEX IF NOT EXISTS ix_credentials_email ON credentials (email);

-- Transactions Table
CREATE TABLE IF NOT EXISTS transactions (
    id SERIAL PRIMARY KEY,
    image_hash VARCHAR NOT NULL UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    parsed_data JSON
);

CREATE INDEX IF NOT EXISTS ix_transactions_image_hash ON transactions (image_hash);

-- Merchant Mappings Table
CREATE TABLE IF NOT EXISTS merchant_mappings (
    receipt_merchant_name VARCHAR PRIMARY KEY,
    monarch_merchant_name VARCHAR,
    category_name VARCHAR
);

CREATE INDEX IF NOT EXISTS ix_merchant_mappings_receipt_merchant_name ON merchant_mappings (receipt_merchant_name);

-- Categories Table
CREATE TABLE IF NOT EXISTS categories (
    category_name VARCHAR PRIMARY KEY,
    monarch_category_id VARCHAR,
    category_emoji VARCHAR,
    is_hidden BOOLEAN DEFAULT FALSE
);

-- Fire Settings Table
CREATE TABLE IF NOT EXISTS fire_settings (
    id INTEGER PRIMARY KEY DEFAULT 1,
    current_age INTEGER DEFAULT 30,
    retirement_age INTEGER DEFAULT 55,
    annual_contribution INTEGER DEFAULT 50000,
    annual_retirement_spending INTEGER DEFAULT 40000,
    risk_tolerance VARCHAR DEFAULT 'moderate',
    inflation_rate FLOAT DEFAULT 0.03,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);
