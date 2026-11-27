# 📖 CareerPilot AI (Autohire.ai) — Operational Guide (HOW TO RUN)

This document provides step-by-step instructions for running, operating, configuring, and verifying the **CareerPilot AI / Autohire.ai** platform.

---

## 🛠️ 1. Environment & Prerequisites

### System Requirements
- **OS:** Windows 10/11, macOS, or Linux.
- **Python:** Python 3.11 or 3.12 (installed in `D:\Autohire.ai\.venv`).
- **Database:** SQLite 3.35+ (automatically initialized in WAL mode at `database/tracker.db`).

### Activating the Python Virtual Environment

```powershell
# On Windows PowerShell
cd D:\Autohire.ai
.\.venv\Scripts\Activate.ps1
```

```bash
# On Linux / macOS / Git Bash
cd D:\Autohire.ai
source .venv/bin/activate
```

### Environment Configuration (`.env`)

A default `.env` file is located at the project root. Essential keys:

```dotenv
# Gemini Scoring Engine
GEMINI_API_KEY="your-gemini-api-key"
GEMINI_MODEL="gemini-2.0-flash"

# Safety Toggles (Keep True for safety)
DRY_RUN=True
REQUIRE_HUMAN_APPROVAL=True

# SMTP Email Configuration
EMAIL_HOST="smtp.gmail.com"
EMAIL_PORT=587
EMAIL_USER="manojkumarmandal1111@gmail.com"
EMAIL_PASSWORD="your-app-password"
EMAIL_RECIPIENT="manojkumarmandal1111@gmail.com"

# Candidate Profile
CANDIDATE_NAME="Manoj Kumar Mandal"
CANDIDATE_EMAIL="manojkumarmandal1111@gmail.com"

# Dashboard Credentials
DASHBOARD_USERNAME="admin"
DASHBOARD_PASSWORD="admin123"
```

> [!NOTE]
> When `GEMINI_API_KEY` is not provided, the platform automatically engages its **Deterministic Heuristic Fallback Engine**, ensuring full offline/sandboxed operation without failures.

---

## 💻 2. Command Line Interface (CLI) Execution

The master CLI entrypoint is `main.py`. It supports several execution modes:

### A. Health Diagnostic Check (`--health`)
Performs a comprehensive check of database integrity (all 14 tables), candidate profile, scraper modules, email tools, and disk storage:

```powershell
python main.py --health
```

### B. Autonomous Pipeline Simulation (`--dry-run`)
Executes the full LangGraph workflow in simulated safety mode. Scrapes jobs, filters them, scores them, stages high matches into the approval queue, simulates portal applications, and generates the daily HTML report without modifying external live portals or sending real emails:

```powershell
python main.py --dry-run
```

### C. Live Autonomous Execution (`--run-now`)
Executes an immediate live autonomous workflow run. If `REQUIRE_HUMAN_APPROVAL=True`, jobs are staged for review; if approved, live applications and cold emails are dispatched:

```powershell
python main.py --run-now
```

### D. Standalone Scraper Engine (`--scrape`)
Runs only the multi-portal scraper engine across all 6 portals (RemoteOK, WeWorkRemotely, Internshala, Wellfound, LinkedIn, Naukri) and outputs normalized listings:

```powershell
python main.py --scrape --max-jobs 10
```

### E. Test Email Dispatch (`--test-email`)
Validates SMTP credentials and sends a test email to the configured candidate recipient:

```powershell
python main.py --test-email
```

### F. Scheduled Autonomous Daemon (`--schedule`)
Starts the background scheduler daemon that wakes up every day at `09:00 IST` (or configured cron time) to run the autonomous workflow automatically:

```powershell
python main.py --schedule
```

---

## 🌐 3. Launching & Operating the SaaS Dashboard (F8)

### Starting the Dashboard Server
Start the Flask SaaS monitoring dashboard via CLI:

```powershell
python main.py --dashboard --port 5000
```
Or run directly:
```powershell
python dashboard/app.py
```

Open your browser and navigate to:
```
http://127.0.0.1:5000
```

### Dashboard Authentication
- **Default Username:** `admin`
- **Default Password:** `admin123`

### Navigating the 8 Sidebar Tabs

1. **Dashboard (Overview):**
   - High-level KPIs: Total Jobs Seen, Applications Sent, Active Outreach, Average Match Score.
   - 7-day velocity chart powered by Chart.js.
   - Quick action buttons to trigger a scrape, run maintenance, or flush logs.
2. **Job Feed:**
   - Real-time tabular feed of normalized jobs across all portals.
   - Visual match score pills (`98%`, `94%`, `87%`) with color-coded portal badges.
   - Search and filtering by role title and minimum score.
3. **Approvals (Approval Queue):**
   - Staging area for items requiring human verification.
   - **One-click Approve (`POST /api/approvals/<id>/decide`)**: Dispatches the application or cold email.
   - **One-click Reject (`POST /api/approvals/<id>/decide`)**: Dismisses the job listing.
   - **Batch Action Buttons**: "Approve All High Matches (≥8.5)" or "Reject All".
4. **Candidate Profile & Skills:**
   - Visual display of candidate identity: **Manoj Kumar Mandal**.
   - Verified 3-tier skill badges:
     * *Proficient (1.0)*: RAG, Agentic AI, Vector Databases, Python, AI
     * *Intermediate (0.7)*: LangChain, LangGraph, FastAPI, Transformers
     * *Learning (0.3)*: Docker, PyTorch, TensorFlow
   - Interface to add or remove skills dynamically.
5. **Cold Outreach & Drip:**
   - Recruiter discovery intelligence and cold email logs.
   - 1,831 PDF HR contacts drip tracker (current batch, remaining quota, daily 50-limit).
6. **Applications Tracker:**
   - Complete audit trail of all candidate applications with status badges (`applied`, `interview`, `offered`, `rejected`).
7. **System Memory & Insights:**
   - Cross-session memory viewer displaying cached preferences, company interaction records, and agent insights.
8. **Live SSE Log Console & Settings:**
   - Real-time Server-Sent Events (SSE) streaming terminal directly from the agent.
   - Toggle buttons for `DRY_RUN` and `REQUIRE_HUMAN_APPROVAL`.
   - Database WAL checkpoint and optimization trigger (`/api/admin/maintenance`).

---

## 📊 4. Daily HTML Executive Report (F9)

The platform automatically generates a daily executive HTML intelligence report saved to:
```
logs/reports/daily_report_YYYY-MM-DD.html
logs/reports/daily_report_YYYY-MM-DD.json
```

### To Generate a Report On-Demand:
```powershell
python -c "from database.db_manager import DatabaseManager; from services.report_service import ReportService; import config as cfg; db = DatabaseManager(cfg.DB_PATH); rs = ReportService(db); rep = rs.generate_daily_report(); print('Generated report at:', rep['html_path'])"
```

The report features:
- Royal Blue (`#1A56DB`) executive layout matching brand standards.
- 5-stage conversion funnel (Jobs Scraped → Filtered → Scored → Applications → Interviews).
- 7-day performance metrics and portal yield breakdown.
- Recruiter inbox intelligence (Interview invitations vs bounces vs general replies).
- Progress on the 1,831 PDF HR contact drip campaign.

---

## 🧪 5. Running Automated Verification Suites

The platform includes **15 automated test suites** covering all 15 milestones.

### Running the Master Integration Suite (Milestone 15)
```powershell
python tests/test_integration_end_to_end.py
```

### Running All 15 Test Suites Sequentially
To execute the complete regression suite across all 116 test cases:

```powershell
python -c "import subprocess, sys

test_files = [
    'test_foundation.py',
    'test_database.py',
    'test_profile_service.py',
    'test_scrapers.py',
    'test_filter_service.py',
    'test_scoring_service.py',
    'test_agent_workflow.py',
    'test_apply_service.py',
    'test_email_service.py',
    'test_memory_service.py',
    'test_scheduler_cli.py',
    'test_dashboard_api.py',
    'test_report_service.py',
    'test_system_hardening.py',
    'test_integration_end_to_end.py'
]

for tf in test_files:
    res = subprocess.run([sys.executable, f'tests/{tf}'], capture_output=True, text=True)
    status = '✅ PASSED' if res.returncode == 0 else f'❌ FAILED (code {res.returncode})'
    print(f'{tf:30} : {status}')
"
```

---

## 🔧 6. Maintenance & Troubleshooting

| Issue | Cause | Solution |
| :--- | :--- | :--- |
| `sqlite3.OperationalError: database is locked` | High concurrent access on Windows | `db_manager.py` enforces WAL mode and `PRAGMA busy_timeout = 30000;`. Trigger a WAL checkpoint via `python -c "from database.db_manager import DatabaseManager; DatabaseManager('database/tracker.db').checkpoint_wal()"` or click Maintenance in Dashboard. |
| `Gemini API Quota Exceeded / 429` | Exceeded Google GenAI free tier RPM | The platform automatically falls back to deterministic heuristic scoring without interrupting the pipeline. |
| Application blocked with `REJECT_ALREADY_APPLIED` | Duplicate apply prevention | By design, SHA-256 URL hashing prevents reapplying to the same listing or company within the cooldown period. |
| Dashboard Port in Use | Another process is using port 5000 | Run with `--port 5001` or any open port: `python main.py --dashboard --port 5001`. |
