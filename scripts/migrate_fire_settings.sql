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

-- Seed default row
INSERT INTO fire_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
