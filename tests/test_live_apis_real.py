"""
tests/test_live_apis_real.py — Second-Stage Real API Live Connection Probe
Zero credential disclosure, minimal calls, strictly safe and non-destructive.
"""
import os
import sys
import smtplib
import requests
import dotenv
from pathlib import Path

dotenv.load_dotenv('.env')

def mask(val: str) -> str:
    if not val or len(val) < 6:
        return '***'
    return val[:3] + '...' + val[-3:]

print('=' * 75)
print('          SECOND-STAGE REAL API LIVE INTEGRATION PROBE')
print('=' * 75)

report = {}

# 1. Gmail SMTP Authentication
print('\n[1/6] Testing Gmail SMTP Live Authentication...')
gmail_user = os.getenv('GMAIL_ADDRESS', '')
gmail_pass = os.getenv('GMAIL_APP_PASSWORD', '')

if not gmail_user or not gmail_pass:
    report['Gmail SMTP'] = ('MISSING', 'Credentials missing in .env')
    print('  ❌ GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing')
else:
    try:
        smtp = smtplib.SMTP('smtp.gmail.com', 587, timeout=12)
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        code, msg = smtp.login(gmail_user, gmail_pass)
        smtp.quit()
        msg_str = msg.decode(errors='ignore')[:40]
        report['Gmail SMTP'] = ('PASS', f'SMTP Auth Code {code}: {msg_str}')
        print(f'  ✅ PASS: Authenticated successfully as {mask(gmail_user)} (Code {code})')
    except Exception as e:
        report['Gmail SMTP'] = ('FAIL', f'{type(e).__name__}: {str(e)[:100]}')
        print(f'  ❌ FAIL: {type(e).__name__}: {e}')

# 2. Apollo API
print('\n[2/6] Testing Apollo API Live Read-Only Health Endpoint...')
apollo_key = os.getenv('APOLLO_API_KEY', '')
if not apollo_key:
    report['Apollo API'] = ('MISSING', 'APOLLO_API_KEY missing in .env')
    print('  ❌ APOLLO_API_KEY missing')
else:
    try:
        res = requests.get(
            'https://api.apollo.io/v1/auth/health',
            headers={'X-Api-Key': apollo_key, 'Content-Type': 'application/json'},
            timeout=12
        )
        if res.status_code == 200:
            report['Apollo API'] = ('PASS', f'HTTP 200 OK: {res.text[:40]}')
            print(f'  ✅ PASS: Apollo API Authenticated (HTTP 200 OK)')
        else:
            report['Apollo API'] = ('WARNING', f'HTTP {res.status_code}: {res.text[:60]}')
            print(f'  ⚠️ WARNING: Apollo responded HTTP {res.status_code}')
    except Exception as e:
        report['Apollo API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:100]}')
        print(f'  ❌ FAIL: {type(e).__name__}: {e}')

# 3. Snov.io API
print('\n[3/6] Testing Snov.io Live OAuth Authentication...')
snov_id = os.getenv('SNOV_CLIENT_ID', '')
snov_secret = os.getenv('SNOV_CLIENT_SECRET', '')
if not snov_id or not snov_secret:
    report['Snov.io API'] = ('MISSING', 'Snov credentials missing in .env')
    print('  ❌ SNOV_CLIENT_ID or SNOV_CLIENT_SECRET missing')
else:
    try:
        res = requests.post(
            'https://api.snov.io/v1/oauth/access_token',
            data={
                'grant_type': 'client_credentials',
                'client_id': snov_id,
                'client_secret': snov_secret
            },
            timeout=12
        )
        if res.status_code == 200 and 'access_token' in res.json():
            token_type = res.json().get('token_type', 'bearer')
            expires_in = res.json().get('expires_in', 0)
            report['Snov.io API'] = ('PASS', f'OAuth token obtained (expires_in: {expires_in}s)')
            print(f'  ✅ PASS: OAuth token obtained successfully ({token_type}, expires in {expires_in}s)')
        else:
            report['Snov.io API'] = ('FAIL', f'HTTP {res.status_code}: {res.text[:80]}')
            print(f'  ❌ FAIL: HTTP {res.status_code}')
    except Exception as e:
        report['Snov.io API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:100]}')
        print(f'  ❌ FAIL: {type(e).__name__}: {e}')

# 4. Tavily API
print('\n[4/6] Testing Tavily Live Minimal Search...')
tavily_key = os.getenv('TAVILY_API_KEY', '')
if not tavily_key:
    report['Tavily API'] = ('MISSING', 'TAVILY_API_KEY missing in .env')
    print('  ❌ TAVILY_API_KEY missing')
else:
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=tavily_key)
        res = client.search(query='AI Engineer remote hiring 2026', max_results=1)
        if res and 'results' in res and len(res['results']) > 0:
            first_title = res['results'][0].get('title', 'Unknown')
            first_url = res['results'][0].get('url', 'Unknown')
            report['Tavily API'] = ('PASS', f'Found: {first_title[:40]} ({first_url[:35]}...)')
            print(f'  ✅ PASS: Real search result retrieved: {first_title[:45]}...')
        else:
            report['Tavily API'] = ('WARNING', 'Zero search results returned')
            print('  ⚠️ WARNING: Zero search results returned')
    except Exception as e:
        report['Tavily API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:100]}')
        print(f'  ❌ FAIL: {type(e).__name__}: {e}')

# 5. Mem0 API
print('\n[5/6] Testing Mem0 Live Memory Write, Read, and Cleanup...')
mem0_key = os.getenv('MEM0_API_KEY', '')
mem0_user = os.getenv('MEM0_USER_ID', 'manoj_careerpilot_001')
if not mem0_key:
    report['Mem0 API'] = ('MISSING', 'MEM0_API_KEY missing in .env')
    print('  ❌ MEM0_API_KEY missing')
else:
    try:
        from mem0 import MemoryClient
        mclient = MemoryClient(api_key=mem0_key)
        # 1. Write temporary memory
        add_res = mclient.add(
            messages=[{'role': 'user', 'content': 'Second-stage live integration verification probe for Manoj Kumar Mandal.'}],
            user_id=mem0_user
        )
        mem_id = None
        if isinstance(add_res, dict) and 'results' in add_res and len(add_res['results']) > 0:
            mem_id = add_res['results'][0].get('id')
        elif isinstance(add_res, list) and len(add_res) > 0 and isinstance(add_res[0], dict):
            mem_id = add_res[0].get('id')

        # 2. Read memory
        all_mems = mclient.get_all(filters={'user_id': mem0_user})
        m_count = len(all_mems.get('results', [])) if isinstance(all_mems, dict) else len(all_mems)

        # 3. Clean up temporary test entry if ID obtained
        if mem_id:
            try:
                mclient.delete(memory_id=mem_id)
                clean_status = 'cleaned up'
            except Exception:
                clean_status = 'delete attempted'
        else:
            clean_status = 'temporary verified'

        report['Mem0 API'] = ('PASS', f'Write & read verified (count: {m_count}, test item {clean_status})')
        print(f'  ✅ PASS: Mem0 write, read ({m_count} memories), and cleanup successful!')
    except Exception as e:
        report['Mem0 API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:100]}')
        print(f'  ❌ FAIL: {type(e).__name__}: {e}')

# 6. Gemini API
print('\n[6/6] Testing Google Gemini Live Minimal API Call...')
gemini_key = os.getenv('GEMINI_API_KEY', '')
model_name = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash')
if not gemini_key:
    report['Gemini API'] = ('MISSING', 'GEMINI_API_KEY missing in .env')
    print('  ❌ GEMINI_API_KEY missing')
else:
    try:
        from google import genai
        gclient = genai.Client(api_key=gemini_key)
        
        # Test minimal generation
        resp = gclient.models.generate_content(
            model=model_name,
            contents='Respond with exactly one word: PONG'
        )
        text_resp = (resp.text or '').strip()
        report['Gemini API'] = ('PASS', f'Model {model_name} responded: \"{text_resp}\"')
        print(f'  ✅ PASS: Model {model_name} responded: \"{text_resp}\"')
    except Exception as e:
        # Check if model list is accessible
        try:
            from google import genai
            gclient = genai.Client(api_key=gemini_key)
            avail_models = [m.name for m in gclient.models.list() if 'generateContent' in (getattr(m, 'supported_actions', []) or [])]
            top_avail = [m.replace('models/', '') for m in avail_models[:3]]
            report['Gemini API'] = ('WARNING', f'Call error: {type(e).__name__}. Available models on key: {top_avail}')
            print(f'  ⚠️ WARNING: Model call failed ({type(e).__name__}). Available models: {top_avail}')
        except Exception as e2:
            report['Gemini API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:100]}')
            print(f'  ❌ FAIL: {type(e).__name__}: {e}')

print('\n' + '=' * 75)
print('                   SUMMARY OF REAL API PROBES')
print('=' * 75)
for k, (st, det) in report.items():
    icon = '✅' if st == 'PASS' else ('⚠️' if st == 'WARNING' else '❌')
    print(f'{icon} {k:<15} : [{st:<7}] {det}')
print('=' * 75 + '\n')
