import asyncio
import os
import sys
import pickle

sys.path.append(os.getcwd())
from bridge_app.database import get_db
from bridge_app.models import Credentials
from monarchmoney import MonarchMoney

async def manual_session_save():
    mm = MonarchMoney()
    # Populate the headers with the browser's cookies and csrf token
    mm._headers.update({
        "cookie": "ajs_anonymous_id=5eb1ea1f-d771-42e9-a089-9e7dc838e2c2; osano_consentmanager_uuid=b7fd1c5f-1089-4a32-81ba-da1187debba8; osano_consentmanager=GnMkCMDauJUoskwpu6zg2pXr2V1kl8Mp3mWtwtTRlMz2WfW01eokvfVTPbEOLtzcoR04hmJefw3aOh27-aR_GkEWY6oLXy9GJpjUN9v3FuOcuESwmLtIU1ZApEJnnQMnOSIO7m-NGyGK_f0Y7cOWJ4QstFbQcVVzKs1Rh0ZlgfexToXckSvER2RZmwa5OWVupJIZ3SU_QIQJUSJ5mmxifq8ZpJpIXAbo2wZy_ERIlYRCp5iCkT5bfElHxAaPFqWc7r4WlkTIIYHIXKx4b7DS02X349Aaz2uM-tqXN21yY82re-Df-XYBOIu8HqY3-8k8s6WPWQdDzuk=; ajs_user_id=ac447137-6bcf-4dfe-96ce-521ccfe599be; afUserId=e0b209db-f69d-4bd8-9a28-64b7ddf7423b-p; AF_SYNC=1778833891117; session_id=6a6841829c08029b085d4c52309e65ac; csrftoken=RtNl1and8jpJ5obEkqYwlqYsLu7C8Upi; cf_clearance=zE_wdGQrWscooYAB_CpHsHGBroJOj1VexGmyofC16ec-1778838994-1.2.1.1-qcfchfbTH4ZPeXM_gMqVkGvSUzNEqE_1yRWG06OyZkOw.SDwe1TaxKa7gAZiwwuSUr132CbTiergfVHMBDA1fQkQNFzO2_sED3w0kRVvOgyOq.9YLA0TIo4yKIu8KodK.BxjF0XMCRERAg7H7okcIeCAvmm7wTvB4RK1jpBmYYT16H_Mi9rB9ifmzukdHyl9GaHc8rO6L1My5fyhoWrEPbIkwqJzGKk.E.OoN46OX0IfgHm2QwMh8RRDeAmCBhN06LbapOtXbOG9g.ie3nl6zi1VR77HhV1QHpJlHYxKxRS7rTalEnq8lix6oDz0cdEuYH0hFkPAentBT.H5LznCkg; __cf_bm=aoRkwF.uvTtaCNPYQMPBZugQFa5wvAy2AOSai67rpw8-1778838994.5422864-1.0.1.1-wYxT3ylupER_LCtWul7bCrIy6YZh2ecunRv4067aYk6ROadAT5Iml2Zi6o1wNEWmn_B9iOUMHeQ2XwU4_r7vq9OuYS_h3vmB2z1M62KZNJW8GkBBoBWODAEHPI7ezKeS",
        "x-csrftoken": "RtNl1and8jpJ5obEkqYwlqYsLu7C8Upi",
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "client-platform": "web"
    })
    
    if "Authorization" in mm._headers:
        del mm._headers["Authorization"]
    
    mm._token = None
    
    session_data = {
        "token": mm._token,
        "headers": mm._headers,
    }
    session_bytes = pickle.dumps(session_data)
    
    email = input("Confirm your email to save session to: ").strip()
    print("DEBUG: Connecting to the database...")
    
    try:
        async for db in get_db():
            print("DEBUG: Database connection acquired.")
            from sqlalchemy import select
            print(f"DEBUG: Looking up credentials for email: {email}")
            result = await db.execute(select(Credentials).where(Credentials.email == email))
            creds = result.scalar_one_or_none()
            
            if creds:
                 print(f"Updating session for {email}...")
                 creds.monarch_session = session_bytes
                 from datetime import datetime, timezone
                 creds.last_update_date = datetime.now(timezone.utc)
            else:
                 from bridge_app.utils.crypto import encrypt
                 print(f"User {email} not found in DB. Creating placeholder.")
                 payload = encrypt('{"password": "", "mfa_secret": ""}')
                 from datetime import datetime, timezone
                 creds = Credentials(email=email, encrypted_payload=payload, monarch_session=session_bytes, last_update_date=datetime.now(timezone.utc))
                 db.add(creds)
            
            print("DEBUG: Committing to database...")
            await db.commit()
            print("Session saved to database successfully! You should now be logged in.")
            break
            
    except Exception as e:
        print(f"DEBUG: Exception occurred during DB operation: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(manual_session_save())
