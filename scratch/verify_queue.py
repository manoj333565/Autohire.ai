import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlite3
import config as cfg

conn = sqlite3.connect(cfg.DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print("=== APPROVAL QUEUE (Recent 10) ===")
for r in cur.execute("SELECT id, item_type, status, execution_status, company, created_at FROM approval_queue ORDER BY id DESC LIMIT 10").fetchall():
    print(dict(r))

print("\n=== APPLICATIONS (Recent 5) ===")
for r in cur.execute("SELECT id, job_hash, status, email_sent, company FROM applications ORDER BY id DESC LIMIT 5").fetchall():
    print(dict(r))

print("\n=== EMAIL HISTORY ===")
cur.execute("SELECT COUNT(*) FROM email_history")
print("Total emails sent in history:", cur.fetchone()[0])

print("\n=== PROOF: REAL EMAILS / SUBMISSIONS ===")
cur.execute("SELECT COUNT(*) FROM applications WHERE status = 'applied' AND applied_date IS NOT NULL")
print("Live confirmed applications count:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM applications WHERE email_sent = 1")
print("Applications with email_sent = 1:", cur.fetchone()[0])
