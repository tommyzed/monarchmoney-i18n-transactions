import os
import json
from google import genai
from PIL import Image
import io

def extract_transaction_data(image_bytes: bytes) -> dict:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return {"error": "GEMINI_API_KEY not set"}

    # Initialize Client
    client = genai.Client(api_key=api_key)
    
    # Prompt engineering
    prompt = """
    You are a financial data extractor. Extract the following from the receipt image:
    - date (YYYY-MM-DD)
    - amount (float)
    - currency (ISO code, assume EUR if not specified but likely European)
    - merchant (string, clean name)
    
    Return strictly valid JSON with keys: date, amount, currency, merchant.
    """
    
    try:
        image = Image.open(io.BytesIO(image_bytes))
        
        # New SDK call
        response = client.models.generate_content(
            model="gemini-3-flash-preview", 
            contents=[prompt, image]
        )
        
        # Clean response to ensure it's JSON
        # The new SDK response object also has a .text property
        text_response = response.text.replace("```json", "").replace("```", "").strip()
        return json.loads(text_response)
    except Exception as e:
        print(f"Gemini Extraction Error: {e}")
        # Return error dict instead of raising to avoid crashing the whole request if just OCR fails
        return {"error": str(e)}
