import os
import io
import json
from unittest.mock import patch, MagicMock
import pytest
from PIL import Image

from bridge_app.services.gemini import extract_transaction_data

def test_extract_transaction_data_no_api_key():
    with patch.dict(os.environ, {}, clear=True):
        # Ensure GEMINI_API_KEY is not set
        if "GEMINI_API_KEY" in os.environ:
            del os.environ["GEMINI_API_KEY"]
        result = extract_transaction_data(b"fake_image_bytes")
        assert result == {"error": "GEMINI_API_KEY not set"}

@patch("bridge_app.services.gemini.Image.open")
@patch("bridge_app.services.gemini.genai.Client")
def test_extract_transaction_data_success(mock_client_class, mock_image_open):
    # Setup environment
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test_api_key"}):
        # Mock PIL image
        mock_image = MagicMock()
        mock_image_open.return_value = mock_image

        # Mock GenAI response
        mock_response = MagicMock()
        mock_response.text = '{"date": "2026-10-27", "amount": 12.34, "currency": "EUR", "merchant": "Coffee Shop", "is_credit": false, "used_historical_name": false}'

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client

        # Call function
        result = extract_transaction_data(b"fake_image_bytes")

        # Verifications
        assert result == {
            "date": "2026-10-27",
            "amount": 12.34,
            "currency": "EUR",
            "merchant": "Coffee Shop",
            "is_credit": False,
            "used_historical_name": False
        }
        mock_image_open.assert_called_once()
        mock_client_class.assert_called_once_with(api_key="test_api_key")

        # Verify prompt details
        args, kwargs = mock_client.models.generate_content.call_args
        assert kwargs["model"] == "gemini-3.5-flash"
        contents = kwargs["contents"]
        assert len(contents) == 2
        prompt = contents[0]
        assert "You are a financial data extractor" in prompt
        assert "HISTORICAL MERCHANT NAMES" not in prompt

@patch("bridge_app.services.gemini.Image.open")
@patch("bridge_app.services.gemini.genai.Client")
def test_extract_transaction_data_with_historical_merchants(mock_client_class, mock_image_open):
    # Setup environment
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test_api_key"}):
        mock_image = MagicMock()
        mock_image_open.return_value = mock_image

        mock_response = MagicMock()
        mock_response.text = '```json\n{"date": "2026-10-27", "amount": 45.67, "currency": "USD", "merchant": "Target", "is_credit": false, "used_historical_name": true}\n```'

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client

        historical_names = ["Target", "Walmart", "Costco"]
        result = extract_transaction_data(b"fake_image_bytes", historical_merchant_names=historical_names)

        assert result == {
            "date": "2026-10-27",
            "amount": 45.67,
            "currency": "USD",
            "merchant": "Target",
            "is_credit": False,
            "used_historical_name": True
        }

        # Verify prompt contains the matching block and names
        args, kwargs = mock_client.models.generate_content.call_args
        prompt = kwargs["contents"][0]
        assert "HISTORICAL MERCHANT NAMES" in prompt
        assert "Target" in prompt
        assert "Walmart" in prompt

@patch("bridge_app.services.gemini.Image.open")
@patch("bridge_app.services.gemini.genai.Client")
def test_extract_transaction_data_missing_used_historical_name_key(mock_client_class, mock_image_open):
    # Setup environment
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test_api_key"}):
        mock_image = MagicMock()
        mock_image_open.return_value = mock_image

        mock_response = MagicMock()
        mock_response.text = '{"date": "2026-10-27", "amount": 1.00, "currency": "USD", "merchant": "Unknown", "is_credit": false}'

        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_client_class.return_value = mock_client

        result = extract_transaction_data(b"fake_image_bytes")

        # It should default to False because the key was missing in the raw JSON
        assert result["used_historical_name"] is False

@patch("bridge_app.services.gemini.Image.open")
def test_extract_transaction_data_image_error(mock_image_open):
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test_api_key"}):
        mock_image_open.side_effect = Exception("Cannot identify image file")

        result = extract_transaction_data(b"corrupt_bytes")
        assert "error" in result
        assert "Cannot identify image file" in result["error"]

@patch("bridge_app.services.gemini.Image.open")
@patch("bridge_app.services.gemini.genai.Client")
def test_extract_transaction_data_sdk_error(mock_client_class, mock_image_open):
    with patch.dict(os.environ, {"GEMINI_API_KEY": "test_api_key"}):
        mock_image = MagicMock()
        mock_image_open.return_value = mock_image

        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API connection failure")
        mock_client_class.return_value = mock_client

        result = extract_transaction_data(b"fake_image_bytes")
        assert "error" in result
        assert "API connection failure" in result["error"]
