import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from bridge_app.services.orchestrator import _process_transaction_data
from bridge_app.main import get_logs, update_transaction_date
from bridge_app.models import Log
from pydantic import BaseModel
from typing import Optional

class UpdateDateRequest(BaseModel):
    monarch_tx_id: str
    new_date: str
    original_amount: float
    original_currency: str
    is_credit: Optional[bool] = False

async def test_orchestrator_saves_log():
    print("Testing orchestrator saves log to database...")

    mock_db = AsyncMock()

    # Mock DB Credentials query result
    mock_creds = MagicMock()
    mock_creds.id = 1
    
    # Precise query mock handling
    def mock_execute(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        else:
            res.scalar_one_or_none.return_value = None
            res.scalars.return_value.first.return_value = None
        return res
        
    mock_db.execute = AsyncMock(side_effect=mock_execute)

    async def mock_report(msg, percent=None):
        pass

    with patch('bridge_app.services.orchestrator.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.orchestrator.push_transaction', new_callable=AsyncMock) as mock_push:
        
        mock_get_client.return_value = MagicMock()
        mock_push.return_value = "monarch_tx_id_123"
        
        data = {
            "date": "2026-11-01",
            "amount": 10.0, 
            "currency": "EUR",
            "merchant": "Burger King",
            "is_cash": True
        }
        
        with patch('bridge_app.services.currency.get_exchange_rate', new_callable=AsyncMock) as mock_rate:
            mock_rate.return_value = 1.10
            
            result = await _process_transaction_data(data, "hash123", mock_db, mock_report, force_override=True)
            
            assert mock_db.add.call_count == 2
            
            add_calls = mock_db.add.call_args_list
            log_instance = None
            for call in add_calls:
                obj = call[0][0]
                if isinstance(obj, Log):
                    log_instance = obj
                    break
            
            assert log_instance is not None
            assert log_instance.merchant == "Burger King"
            assert log_instance.amount == -11.00
            assert log_instance.currency == "USD"
            assert log_instance.date == "2026-11-01"
            assert log_instance.original_amount == 10.0
            assert log_instance.original_currency == "EUR"
            assert log_instance.is_cash is True
            assert log_instance.monarch_tx_id == "monarch_tx_id_123"
            
            print("✅ SUCCESS: Transaction successfully logged to database with conversion")

async def test_get_logs_endpoint():
    print("Testing get_logs API endpoint...")
    
    mock_db = AsyncMock()
    mock_log = Log(
        id=1,
        merchant="Starbucks",
        amount=-5.50,
        currency="USD",
        date="2026-11-02",
        original_amount=None,
        original_currency=None,
        is_cash=False,
        monarch_tx_id="tx_starbucks"
    )
    
    universal_mock_result = MagicMock()
    universal_mock_result.scalars.return_value.all.return_value = [mock_log]
    mock_db.execute.return_value = universal_mock_result
    
    res = await get_logs(db=mock_db)
    
    assert len(res) == 1
    assert res[0]["merchant"] == "Starbucks"
    assert res[0]["amount"] == -5.50
    assert res[0]["monarch_tx_id"] == "tx_starbucks"
    
    print("✅ SUCCESS: logs endpoint returned correct payload")

async def test_update_date_endpoint_updates_log():
    print("Testing update-date updates logs table record...")
    
    mock_db = AsyncMock()
    mock_log = Log(
        id=1,
        merchant="Starbucks",
        amount=-5.50,
        currency="USD",
        date="2026-11-02",
        original_amount=5.00,
        original_currency="EUR",
        is_cash=False,
        monarch_tx_id="tx_starbucks"
    )
    
    # Mock precise DB queries for update endpoint
    mock_creds = MagicMock()
    mock_creds.id = 1
    
    def mock_execute(stmt):
        stmt_str = str(stmt)
        res = MagicMock()
        if "credentials" in stmt_str:
            res.scalars.return_value.first.return_value = mock_creds
        elif "logs" in stmt_str:
            res.scalar_one_or_none.return_value = mock_log
        else:
            res.scalar_one_or_none.return_value = None
        return res
        
    mock_db.execute = AsyncMock(side_effect=mock_execute)
    
    req = UpdateDateRequest(
        monarch_tx_id="tx_starbucks",
        new_date="2026-11-03",
        original_amount=5.00,
        original_currency="EUR",
        is_credit=False
    )
    
    # Patch update_transaction_fields at the monarch service level since it is imported inside the local scope of update_transaction_date
    with patch('bridge_app.main.get_monarch_client', new_callable=AsyncMock) as mock_get_client, \
         patch('bridge_app.services.monarch.update_transaction_fields', new_callable=AsyncMock) as mock_update_fields, \
         patch('bridge_app.services.currency.get_exchange_rate', new_callable=AsyncMock) as mock_rate:
        
        mock_get_client.return_value = MagicMock()
        mock_update_fields.return_value = {"amount_updated": True}
        mock_rate.return_value = 1.20
        
        from bridge_app.main import UpdateDateRequest as AppUpdateDateRequest
        app_req = AppUpdateDateRequest(
            monarch_tx_id=req.monarch_tx_id,
            new_date=req.new_date,
            original_amount=req.original_amount,
            original_currency=req.original_currency,
            is_credit=req.is_credit
        )
        
        res = await update_transaction_date(app_req, db=mock_db)
        
        assert mock_log.date == "2026-11-03"
        assert mock_log.amount == -6.00
        
        print("✅ SUCCESS: update-date endpoint updated the log table entry")

async def run_all_tests():
    await test_orchestrator_saves_log()
    await test_get_logs_endpoint()
    await test_update_date_endpoint_updates_log()

if __name__ == "__main__":
    asyncio.run(run_all_tests())
