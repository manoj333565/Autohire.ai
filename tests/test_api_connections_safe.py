"""
tests/test_api_connections_safe.py
Safely probes external APIs with zero credential exposure and strictly read-only/test calls.
"""
import os
import sys
import smtplib
import requests
import dotenv

dotenv.load_dotenv('.env')

results = {}

# 1. Gemini API
gemini_key = os.getenv('GEMINI_API_KEY', '')
if not gemini_key:
    results['Gemini API'] = ('MISSING', 'No GEMINI_API_KEY found')
else:
    try:
        from google import genai
        client = genai.Client(api_key=gemini_key)
        # Try gemini-2.0-flash or gemini-2.5-flash
        model_name = os.getenv('GEMINI_MODEL', 'gemini-2.0-flash')
        if '1.5' in model_name:
            # Fall back to 2.0-flash for new google-genai SDK
            model_name = 'gemini-2.0-flash'
        resp = client.models.generate_content(
            model=model_name,
            contents='Return the word: ACTIVE'
        )
        txt = (resp.text or '').strip()
        results['Gemini API'] = ('PASS', f'Model {model_name} responded: {txt[:20]}')
    except Exception as e:
        results['Gemini API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:120]}')

# 2. Gmail SMTP
gmail_addr = os.getenv('GMAIL_ADDRESS', '')
gmail_pass = os.getenv('GMAIL_APP_PASSWORD', '')
if not gmail_addr or not gmail_pass:
    results['Gmail SMTP'] = ('MISSING', 'GMAIL_ADDRESS or GMAIL_APP_PASSWORD missing')
else:
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(gmail_addr, gmail_pass)
        server.quit()
        results['Gmail SMTP'] = ('PASS', 'SMTP authentication successful (no email sent)')
    except Exception as e:
        results['Gmail SMTP'] = ('FAIL', f'{type(e).__name__}: {str(e)[:120]}')

# 3. Apollo API
apollo_key = os.getenv('APOLLO_API_KEY', '')
if not apollo_key:
    results['Apollo API'] = ('MISSING', 'APOLLO_API_KEY missing')
else:
    try:
        res = requests.get(
            'https://api.apollo.io/v1/auth/health',
            headers={'Cache-Control': 'no-cache', 'X-Api-Key': apollo_key},
            timeout=10
        )
        if res.status_code == 200:
            results['Apollo API'] = ('PASS', 'HTTP 200 - Authenticated')
        elif res.status_code == 401:
            results['Apollo API'] = ('FAIL', 'HTTP 401 - Invalid API key')
        elif res.status_code == 403:
            results['Apollo API'] = ('WARNING', 'HTTP 403 - Forbidden or plan restricted')
        else:
            results['Apollo API'] = ('WARNING', f'HTTP {res.status_code}: {res.text[:80]}')
    except Exception as e:
        results['Apollo API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:120]}')

# 4. Snov.io API
snov_id = os.getenv('SNOV_CLIENT_ID', '')
snov_secret = os.getenv('SNOV_CLIENT_SECRET', '')
if not snov_id or not snov_secret:
    results['Snov.io API'] = ('MISSING', 'SNOV_CLIENT_ID or SNOV_CLIENT_SECRET missing')
else:
    try:
        token_res = requests.post(
            'https://api.snov.io/v1/oauth/access_token',
            data={
                'grant_type': 'client_credentials',
                'client_id': snov_id,
                'client_secret': snov_secret
            },
            timeout=10
        )
        if token_res.status_code == 200 and 'access_token' in token_res.json():
            results['Snov.io API'] = ('PASS', 'OAuth access token obtained')
        else:
            results['Snov.io API'] = ('FAIL', f'HTTP {token_res.status_code}: {token_res.text[:80]}')
    except Exception as e:
        results['Snov.io API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:120]}')

# 5. Tavily API
tavily_key = os.getenv('TAVILY_API_KEY', '')
if not tavily_key:
    results['Tavily API'] = ('MISSING', 'TAVILY_API_KEY missing')
else:
    try:
        from tavily import TavilyClient
        tavily = TavilyClient(api_key=tavily_key)
        search_res = tavily.search(query='AI engineer jobs remote', max_results=1)
        if search_res and 'results' in search_res:
            n_res = len(search_res['results'])
            results['Tavily API'] = ('PASS', f'Search returned {n_res} result')
        else:
            results['Tavily API'] = ('WARNING', 'Search returned empty response')
    except Exception as e:
        results['Tavily API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:120]}')

# 6. Mem0 API
mem0_key = os.getenv('MEM0_API_KEY', '')
if not mem0_key:
    results['Mem0 API'] = ('MISSING', 'MEM0_API_KEY missing')
else:
    try:
        from mem0 import MemoryClient
        mem_client = MemoryClient(api_key=mem0_key)
        user_id = os.getenv('MEM0_USER_ID', 'test_user')
        memories = mem_client.get_all(filters={'user_id': user_id})
        count = len(memories.get('results', [])) if isinstance(memories, dict) else len(memories)
        results['Mem0 API'] = ('PASS', f'Connected (retrieved {count} memories)')
    except Exception as e:
        results['Mem0 API'] = ('FAIL', f'{type(e).__name__}: {str(e)[:120]}')

print('\n================ API INTEGRATION TEST AUDIT ================')
for k, (status, detail) in results.items():
    print(f'{k:<15} : [{status:<7}] {detail}')
print('============================================================\n')
