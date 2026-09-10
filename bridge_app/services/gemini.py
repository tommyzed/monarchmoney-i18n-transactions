import os
import json
from datetime import datetime
from google import genai
from PIL import Image
import io
from typing import Optional

def extract_transaction_data(image_bytes: bytes, historical_merchant_names: Optional[list] = None) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    # Initialize Client
    client = genai.Client(api_key=api_key)

    # Build the historical merchant hint block (injected only when names exist)
    merchant_hint_block = ""
    if historical_merchant_names:
        names_json = json.dumps(historical_merchant_names, ensure_ascii=False)
        merchant_hint_block = f"""

HISTORICAL MERCHANT NAMES (from prior transactions in this system):
{names_json}

MERCHANT MATCHING RULES:
- Compare the merchant name visible on the receipt against every name in the list above.
- If your confidence that the receipt merchant matches one of the historical names is 75% or higher,
  return that EXACT historical name as the value of "merchant" and set "used_historical_name" to true.
- If your confidence is below 75% for ALL historical names, return the merchant name exactly as it
  appears on the receipt and set "used_historical_name" to false.
- When in doubt, prefer the historical name — it is the already-standardised canonical form.
"""

    today_str = datetime.now().strftime("%Y-%m-%d")

    # Prompt engineering
    prompt = f"""
You are a financial data extractor. Extract the following from the receipt image:
- date (YYYY-MM-DD). STRICT RULE: Assume the receipt is recent. The current date is {today_str}. Future dates are impossible. Do NOT output dates from the distant past (e.g. 2024). If the date is ambiguous, missing, or cannot be determined, default to {today_str}.
- amount (float)
- currency (ISO code, assume EUR if not specified but likely European)
- merchant (string, clean name — see rules below)
- is_credit (boolean, true if the total on the receipt is shown in Green indicating a refund/credit,
  false otherwise)
- used_historical_name (boolean — see rules below; default false if no historical names provided)
{merchant_hint_block}
Return strictly valid JSON with keys: date, amount, currency, merchant, is_credit, used_historical_name.
Do NOT include any markdown fences or extra text — only the raw JSON object.
"""

    try:
        image = Image.open(io.BytesIO(image_bytes))

        # New SDK call
        model_name = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
        response = client.models.generate_content(
            model=model_name,
            contents=[prompt, image]
        )

        # Clean response to ensure it's JSON
        text_response = response.text.replace("```json", "").replace("```", "").strip()
        result = json.loads(text_response)

        # Ensure the field is always present (default false if model omits it)
        if "used_historical_name" not in result:
            result["used_historical_name"] = False

        return result
    except Exception as e:
        print(f"Gemini Extraction Error: {e}")
        # Return error dict instead of raising to avoid crashing the whole request if just OCR fails
        return {"error": str(e)}
