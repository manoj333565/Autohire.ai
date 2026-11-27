import os, re, sqlite3

print("=== 1. CHECKING FOR DEB / REFERENCE NAMES IN CODEBASE ===")
pattern = re.compile(r'\b(deb|debjit|deba|deb_job_agent)\b', re.IGNORECASE)
found_refs = []
for root, dirs, files in os.walk('.'):
    if any(p in root for p in ['.venv', '.git', '__pycache__', '.system_generated', 'scratch']):
        continue
    for f in files:
        if f.endswith(('.py', '.json', '.html', '.js', '.css', '.md', '.txt', '.sh', '.env', '.example')):
            if f in ('scratch_request.txt', 'audit_references.py'):
                continue
            p = os.path.join(root, f)
            try:
                with open(p, 'r', encoding='utf-8', errors='ignore') as fp:
                    for i, line in enumerate(fp, 1):
                        if pattern.search(line):
                            found_refs.append(f"{p}:{i} -> {line.strip()[:100]}")
            except Exception as e:
                pass

print(f"Total references found: {len(found_refs)}")
for r in found_refs[:40]:
    print(r)
if len(found_refs) > 40:
    print(f"... and {len(found_refs) - 40} more.")

print("\n=== 2. CHECKING DATABASE tracker.db TABLE RECORD COUNTS ===")
db_path = 'database/tracker.db'
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        cur.execute(f'SELECT COUNT(*) FROM "{t}"')
        cnt = cur.fetchone()[0]
        print(f"{t:30}: {cnt} rows")
    
    # Check if any Deb / reference names exist in database
    print("\n=== 3. CHECKING DATABASE RECORDS FOR REFERENCE DATA ===")
    for t in tables:
        cur.execute(f'PRAGMA table_info("{t}")')
        cols = [c[1] for c in cur.fetchall()]
        text_cols = []
        for col in cols:
            try:
                cur.execute(f'SELECT "{col}" FROM "{t}" WHERE LOWER(CAST("{col}" AS TEXT)) LIKE "%deb%" LIMIT 3')
                matches = cur.fetchall()
                if matches:
                    print(f"  Found reference data in table '{t}', column '{col}': {len(matches)} matches (sample: {matches[0][0]})")
            except Exception:
                pass
    conn.close()
else:
    print("tracker.db does not exist")
