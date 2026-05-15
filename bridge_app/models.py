from sqlalchemy import Column, Integer, String, LargeBinary, DateTime, JSON, Boolean, Float
from sqlalchemy.sql import func
from .database import Base

class Credentials(Base):
    __tablename__ = "credentials"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    encrypted_payload = Column(LargeBinary, nullable=False)
    monarch_session = Column(LargeBinary, nullable=True)
    last_update_date = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    image_hash = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    parsed_data = Column(JSON, nullable=True)

class MerchantMapping(Base):
    __tablename__ = "merchant_mappings"
    receipt_merchant_name = Column(String, unique=True, index=True, primary_key=True)
    monarch_merchant_name = Column(String)
    category_name = Column(String)

class Category(Base):
    __tablename__ = "categories"
    category_name = Column(String, primary_key=True, index=True)
    monarch_category_id = Column(String, nullable=True) # New column
    category_emoji = Column(String)
    is_hidden = Column(Boolean, default=False)

class FireSettings(Base):
    __tablename__ = "fire_settings"
    id = Column(Integer, primary_key=True, default=1)
    current_age = Column(Integer, default=30)
    retirement_age = Column(Integer, default=55)
    annual_contribution = Column(Integer, default=50000)
    annual_retirement_spending = Column(Integer, default=40000)
    risk_tolerance = Column(String, default="moderate")  # "lean", "moderate", "fat"
    inflation_rate = Column(Float, default=0.03)
    final_age = Column(Integer, default=85)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
