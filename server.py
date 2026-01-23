from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re

app = FastAPI()

# --- FIX: THIS BLOCK ALLOWS YOUR HTML TO TALK TO PYTHON ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allows ALL origins (crucial for your setup)
    allow_credentials=True,
    allow_methods=["*"],  # Allows GET, POST, OPTIONS, etc.
    allow_headers=["*"],  # Allows all headers
)
# ----------------------------------------------------------

class ScanRequest(BaseModel):
    url: str

@app.post("/scan")
def scan_endpoint(req: ScanRequest):
    try:
        # Use a fake browser user-agent to avoid being blocked by target sites
        headers_agent = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        
        # 1. Connect to the target
        response = requests.get(req.url, headers=headers_agent, timeout=10)
        text = response.text
        headers = response.headers
        findings = []

        # 2. Run Scans
        if 'X-Frame-Options' not in headers:
            findings.append("Missing 'X-Frame-Options' header (Clickjacking Risk)")
        
        if re.search(r"AKIA[0-9A-Z]{16}", text):
            findings.append("CRITICAL: AWS Access Key leaked in source code")
            
        emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        if emails:
            # Filter out junk emails
            clean_emails = [e for e in emails if "example.com" not in e and "w3.org" not in e]
            if clean_emails:
                findings.append(f"Privacy: {len(clean_emails)} Email addresses exposed (e.g., {clean_emails[0]})")

        return {
            "status": "success",
            "findings": findings,
            "code_snippet": text[:1500]  # Send first 1500 chars to AI
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
