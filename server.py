from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re

app = FastAPI()

# Allow all origins (so your Cloudflare frontend can hit this)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

class ScanRequest(BaseModel):
    url: str

@app.post("/scan")
def scan_endpoint(req: ScanRequest):
    # This is the "Heavy Lifting" - Scanning the target
    try:
        response = requests.get(req.url, timeout=10)
        text = response.text
        headers = response.headers
        findings = []

        # --- LOGIC: Scan for Vulnerabilities ---
        # 1. Check Headers
        if 'X-Frame-Options' not in headers:
            findings.append("Missing X-Frame-Options (Clickjacking Risk)")
        
        # 2. Check Secrets (Regex)
        if re.search(r"AKIA[0-9A-Z]{16}", text):
            findings.append("CRITICAL: AWS Access Key leaked in source code")
        
        # 3. Check PII
        emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        if emails:
            findings.append(f"Privacy: {len(emails)} Email addresses exposed")

        return {
            "status": "success",
            "findings": findings,
            # We send a snippet of code to the AI for context
            "code_snippet": text[:1500] 
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
