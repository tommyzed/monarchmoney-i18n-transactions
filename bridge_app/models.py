from sqlalchemy import Column, Integer, String, LargeBinary, DateTime, JSON
from sqlalchemy.sql import func
from .database import Base

class Credentials(Base):
    __tablename__ = "credentials"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    encrypted_payload = Column(LargeBinary, nullable=False)
    monarch_session = Column(LargeBinary, nullable=True)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True, index=True)
    image_hash = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    parsed_data = Column(JSON, nullable=True)

class MerchantMapping(Base):
    __tablename__ = "merchant_mappings"
    id = Column(Integer, primary_key=True, index=True)
    receipt_merchant_name = Column(String, unique=True, index=True)
    monarch_merchant_name = Column(String)
    category_id = Column(String, nullable=True)
    category_name = Column(String)

class Category(Base):
    __tablename__ = "categories"
    category_id = Column(String, primary_key=True, index=True)
    category_name = Column(String)
    category_emoji = Column(String)
