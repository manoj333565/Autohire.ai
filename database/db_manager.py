"""
database/db_manager.py — Centralized SQLite Database Architecture for CareerPilot AI
Features:
- Enforces 14 relational tables with Foreign Keys and UNIQUE constraints
- Write-Ahead Logging (WAL) mode for multi-reader / multi-writer concurrency
- Safe, non-destructive migrations preserving 100% of legacy records
- Complete CRUD methods for Applications, Approval Queue, Candidate Profile & Skills,
  Job Scores, Memory Store, Email History, Agent Runs, and API Cache.
"""
import sqlite3
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger("CareerPilot.Database")
_INITIALIZED_DBS: set = set()


class DatabaseManager:
    """Enterprise SQLite Database Manager for CareerPilot AI."""

    def __init__(self, db_path: str):
        self.db_path = str(Path(db_path).resolve())
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        if self.db_path not in _INITIALIZED_DBS:
            self._init_db()
            self._seed_candidate_profile_if_empty()

    def _get_conn(self) -> sqlite3.Connection:
        """Create a connection with WAL mode, foreign keys, dictionary rows, and 30s busy timeout."""
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.execute("PRAGMA journal_mode = WAL;")
        conn.execute("PRAGMA synchronous = NORMAL;")
        conn.execute("PRAGMA busy_timeout = 30000;")
        return conn


    def _init_db(self):
        """Execute non-destructive schema migrations and initialize all 14 tables."""
        with self._get_conn() as conn:
            # 1. applications
            conn.execute("""
                CREATE TABLE IF NOT EXISTS applications (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_hash TEXT UNIQUE NOT NULL,
                    job_url TEXT,
                    job_title TEXT,
                    company TEXT,
                    location TEXT,
                    portal TEXT,
                    job_description TEXT,
                    status TEXT DEFAULT 'new',
                    applied_date TEXT,
                    hr_email TEXT,
                    email_sent INTEGER DEFAULT 0,
                    email_sent_date TEXT,
                    relevance_score REAL DEFAULT 0,
                    salary TEXT,
                    experience TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 2. hr_contacts
            conn.execute("""
                CREATE TABLE IF NOT EXISTS hr_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT,
                    company_domain TEXT,
                    hr_name TEXT,
                    hr_email TEXT,
                    source TEXT,
                    verified INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(company_domain, hr_email)
                );
            """)

            # 3. daily_stats
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date TEXT PRIMARY KEY,
                    total_scraped INTEGER DEFAULT 0,
                    total_new INTEGER DEFAULT 0,
                    total_applied INTEGER DEFAULT 0,
                    total_emailed INTEGER DEFAULT 0,
                    total_failed INTEGER DEFAULT 0,
                    run_time_seconds REAL DEFAULT 0
                );
            """)

            # 4. pdf_hr_outreach (Legacy table name preserved)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pdf_hr_outreach (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    name TEXT,
                    company TEXT,
                    designation TEXT,
                    sent_date TEXT,
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Check existing columns in pdf_hr_outreach
            hr_cols = {r["name"] for r in conn.execute("PRAGMA table_info(pdf_hr_outreach)").fetchall()}
            if "created_at" not in hr_cols:
                conn.execute("ALTER TABLE pdf_hr_outreach ADD COLUMN created_at TEXT;")
            if "follow_up_date" not in hr_cols:
                conn.execute("ALTER TABLE pdf_hr_outreach ADD COLUMN follow_up_date TEXT;")
            if "reply_status" not in hr_cols:
                conn.execute("ALTER TABLE pdf_hr_outreach ADD COLUMN reply_status TEXT DEFAULT 'pending';")

            # Check existing columns in hr_contacts
            contact_cols = {r["name"] for r in conn.execute("PRAGMA table_info(hr_contacts)").fetchall()}
            if "designation" not in contact_cols:
                conn.execute("ALTER TABLE hr_contacts ADD COLUMN designation TEXT;")
            if "outreach_status" not in contact_cols:
                conn.execute("ALTER TABLE hr_contacts ADD COLUMN outreach_status TEXT DEFAULT 'uncontacted';")
            if "last_contact_date" not in contact_cols:
                conn.execute("ALTER TABLE hr_contacts ADD COLUMN last_contact_date TEXT;")
            if "follow_up_date" not in contact_cols:
                conn.execute("ALTER TABLE hr_contacts ADD COLUMN follow_up_date TEXT;")
            if "reply_status" not in contact_cols:
                conn.execute("ALTER TABLE hr_contacts ADD COLUMN reply_status TEXT DEFAULT 'pending';")

            # Create alias view 'drip_outreach' for architecture compatibility idempotently
            try:
                view_exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='view' AND name='drip_outreach';"
                ).fetchone()
                if not view_exists:
                    conn.execute("""
                        CREATE VIEW IF NOT EXISTS drip_outreach AS 
                        SELECT id, email, name, company, designation, sent_date, status
                        FROM pdf_hr_outreach;
                    """)
            except sqlite3.OperationalError as e:
                logger.debug(f"Idempotent drip_outreach view notice: {e}")

            # 5. candidate_profile
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_profile (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    email TEXT NOT NULL,
                    phone TEXT,
                    location TEXT,
                    education_degree TEXT,
                    education_college TEXT,
                    education_year TEXT,
                    education_cgpa TEXT,
                    linkedin_url TEXT,
                    github_url TEXT,
                    portfolio_url TEXT,
                    resume_path TEXT,
                    summary TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 6. candidate_skills
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_skills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL REFERENCES candidate_profile(id) ON DELETE CASCADE,
                    skill_name TEXT NOT NULL,
                    category TEXT DEFAULT 'General',
                    proficiency TEXT NOT NULL CHECK(proficiency IN ('Learning', 'Intermediate', 'Proficient')),
                    verified INTEGER DEFAULT 1,
                    source TEXT DEFAULT 'manual_declaration',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(profile_id, skill_name)
                );
            """)

            # 7. candidate_preferences
            conn.execute("""
                CREATE TABLE IF NOT EXISTS candidate_preferences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL REFERENCES candidate_profile(id) ON DELETE CASCADE,
                    target_roles TEXT,
                    target_keywords TEXT,
                    exclude_keywords TEXT,
                    preferred_locations TEXT,
                    matching_threshold REAL DEFAULT 7.0,
                    min_salary_lpa REAL DEFAULT 3.6,
                    allow_remote INTEGER DEFAULT 1,
                    allow_internships INTEGER DEFAULT 1,
                    max_apply_per_day INTEGER DEFAULT 30,
                    max_email_per_day INTEGER DEFAULT 25,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 8. profile_history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS profile_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_id INTEGER NOT NULL,
                    change_type TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    old_value TEXT,
                    new_value TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 9. agent_runs
            conn.execute("""
                CREATE TABLE IF NOT EXISTS agent_runs (
                    run_id TEXT PRIMARY KEY,
                    start_time TEXT NOT NULL,
                    end_time TEXT,
                    duration_seconds REAL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'running',
                    dry_run INTEGER DEFAULT 1,
                    total_scraped INTEGER DEFAULT 0,
                    total_new INTEGER DEFAULT 0,
                    total_scored INTEGER DEFAULT 0,
                    total_applied INTEGER DEFAULT 0,
                    total_emailed INTEGER DEFAULT 0,
                    total_failed INTEGER DEFAULT 0,
                    pdf_emails_sent INTEGER DEFAULT 0,
                    logs TEXT,
                    errors TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 10. email_history
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_hash TEXT,
                    recipient_email TEXT NOT NULL,
                    recipient_name TEXT,
                    company TEXT,
                    subject TEXT NOT NULL,
                    body TEXT NOT NULL,
                    email_type TEXT NOT NULL CHECK(email_type IN ('job_outreach', 'drip_outreach', 'daily_report')),
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'sent', 'rejected', 'failed', 'dry_run')),
                    sent_at TEXT,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 11. approval_queue (Mandatory Human Safety Gate)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_type TEXT NOT NULL CHECK(item_type IN ('job_application', 'cold_email', 'drip_email')),
                    reference_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    company TEXT NOT NULL,
                    details TEXT,
                    preview_payload TEXT,
                    score REAL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'approved', 'rejected', 'cancelled')),
                    decided_at TEXT,
                    reviewer_notes TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

                        # Safe migration: approval queue execution tracking
            approval_cols = {
                row["name"]
                for row in conn.execute(
                    "PRAGMA table_info(approval_queue)"
                ).fetchall()
            }

            if "execution_status" not in approval_cols:
                conn.execute("""
                    ALTER TABLE approval_queue
                    ADD COLUMN execution_status TEXT
                    NOT NULL DEFAULT 'unknown'
                    CHECK (
                        execution_status IN (
                            'unknown',
                            'not_started',
                            'manual_required',
                            'applied',
                            'failed'
                        )
                    )
                """)
                logger.info(
                    "[Database] Added approval_queue.execution_status"
                )
            
            
            # Atomic execution claims for approved job applications
            conn.execute("""
                CREATE TABLE IF NOT EXISTS approval_execution_claims (
                    queue_id INTEGER PRIMARY KEY,
                    claim_token TEXT NOT NULL UNIQUE,
                    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finalized_at TEXT,
                    final_status TEXT CHECK (
                        final_status IS NULL OR
                        final_status IN (
                            'manual_required',
                            'applied',
                            'failed'
                        )
                    ),
                    FOREIGN KEY(queue_id)
                        REFERENCES approval_queue(id)
                        ON DELETE CASCADE
                );
            """)

            # Atomic execution claims for approved cold emails and drip outreach
            conn.execute("""
                CREATE TABLE IF NOT EXISTS email_execution_claims (
                    queue_id INTEGER PRIMARY KEY,
                    claim_token TEXT NOT NULL UNIQUE,
                    claimed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    finalized_at TEXT,
                    final_status TEXT CHECK (
                        final_status IS NULL OR
                        final_status IN ('sent', 'uncertain', 'failed')
                    ),
                    FOREIGN KEY(queue_id)
                        REFERENCES approval_queue(id)
                        ON DELETE CASCADE
                );
            """)

            # 12. inbox_processed_messages (IMAP UID deduplication & audit trail)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS inbox_processed_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    mailbox TEXT NOT NULL DEFAULT 'INBOX',
                    imap_uid TEXT NOT NULL,
                    message_id TEXT,
                    sender TEXT,
                    subject TEXT,
                    received_at TEXT,
                    classification TEXT,
                    processing_status TEXT NOT NULL DEFAULT 'processed',
                    bounced_recipient TEXT,
                    processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(mailbox, imap_uid)
                );
            """)

            # 13. csv_drip_contacts (Secondary HR drip campaign with CSV import)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS csv_drip_contacts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    company TEXT NOT NULL,
                    name TEXT NOT NULL,
                    email TEXT UNIQUE NOT NULL,
                    role TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'staged', 'approved', 'sent', 'failed', 'skipped', 'completed', 'bounced')),
                    source_file TEXT,
                    queue_id INTEGER REFERENCES approval_queue(id),
                    sent_at TEXT,
                    error_message TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # Ensure 'staged' and 'bounced' statuses are permitted
            table_info = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='csv_drip_contacts'").fetchone()
            if table_info and "'staged'" not in (table_info["sql"] or ""):
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS csv_drip_contacts_v2 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        company TEXT NOT NULL,
                        name TEXT NOT NULL,
                        email TEXT UNIQUE NOT NULL,
                        role TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending', 'staged', 'approved', 'sent', 'failed', 'skipped', 'completed', 'bounced')),
                        source_file TEXT,
                        queue_id INTEGER REFERENCES approval_queue(id),
                        sent_at TEXT,
                        error_message TEXT,
                        created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                        updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                    );
                """)
                conn.execute("INSERT OR IGNORE INTO csv_drip_contacts_v2 SELECT * FROM csv_drip_contacts;")
                conn.execute("DROP TABLE csv_drip_contacts;")
                conn.execute("ALTER TABLE csv_drip_contacts_v2 RENAME TO csv_drip_contacts;")

            # 14. job_scores
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_hash TEXT UNIQUE NOT NULL REFERENCES applications(job_hash) ON DELETE CASCADE,
                    relevance_score REAL NOT NULL,
                    skills_match_ratio REAL DEFAULT 0,
                    experience_match INTEGER DEFAULT 1,
                    location_match INTEGER DEFAULT 1,
                    matched_skills TEXT,
                    missing_skills TEXT,
                    reasoning TEXT,
                    raw_gemini_response TEXT,
                    embedding_score REAL,
                    llm_score REAL,
                    final_score REAL,
                    scored_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)


            # 13. memory_store (Cross-Session Context)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_store (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_key TEXT UNIQUE NOT NULL,
                    category TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
            """)

            # 14. api_cache
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_cache (
                    cache_key TEXT PRIMARY KEY,
                    api_provider TEXT NOT NULL,
                    request_hash TEXT NOT NULL,
                    response_data TEXT NOT NULL,
                    expires_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    endpoint TEXT,
                    request_params TEXT,
                    status_code INTEGER DEFAULT 200,
                    ttl_seconds INTEGER DEFAULT 86400,
                    is_valid INTEGER DEFAULT 1
                );
            """)

            # Safe Column Migrations for api_cache
            cache_cols = {r["name"] for r in conn.execute("PRAGMA table_info(api_cache)").fetchall()}
            if "endpoint" not in cache_cols:
                conn.execute("ALTER TABLE api_cache ADD COLUMN endpoint TEXT;")
            if "request_params" not in cache_cols:
                conn.execute("ALTER TABLE api_cache ADD COLUMN request_params TEXT;")
            if "status_code" not in cache_cols:
                conn.execute("ALTER TABLE api_cache ADD COLUMN status_code INTEGER DEFAULT 200;")
            if "ttl_seconds" not in cache_cols:
                conn.execute("ALTER TABLE api_cache ADD COLUMN ttl_seconds INTEGER DEFAULT 86400;")
            if "is_valid" not in cache_cols:
                conn.execute("ALTER TABLE api_cache ADD COLUMN is_valid INTEGER DEFAULT 1;")

            # 15. daily_memory_summaries (Cross-Session Historical Memory)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS daily_memory_summaries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    date TEXT NOT NULL,
                    total_scraped INTEGER DEFAULT 0,
                    total_filtered INTEGER DEFAULT 0,
                    total_scored INTEGER DEFAULT 0,
                    total_applications INTEGER DEFAULT 0,
                    total_emails_sent INTEGER DEFAULT 0,
                    pending_approvals INTEGER DEFAULT 0,
                    failed_operations INTEGER DEFAULT 0,
                    important_errors TEXT DEFAULT '[]',
                    completed_actions TEXT DEFAULT '[]',
                    summary_text TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(run_id, date)
                );
            """)

            # 16. memory_embeddings (Semantic Vector Memory Retrieval)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS memory_embeddings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER NOT NULL REFERENCES memory_store(id) ON DELETE CASCADE,
                    memory_key TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(memory_id)
                );
            """)

            # 17. tavily_usage (Preserve existing table)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tavily_usage (
                    date TEXT PRIMARY KEY,
                    count INTEGER DEFAULT 0
                );
            """)

            # Safe Column Migrations for existing 'applications' table
            existing_cols = {r["name"] for r in conn.execute("PRAGMA table_info(applications)").fetchall()}
            if "salary" not in existing_cols:
                conn.execute("ALTER TABLE applications ADD COLUMN salary TEXT;")
            if "experience" not in existing_cols:
                conn.execute("ALTER TABLE applications ADD COLUMN experience TEXT;")
            if "updated_at" not in existing_cols:
                conn.execute("ALTER TABLE applications ADD COLUMN updated_at TEXT;")

            # Safe Column Migrations for 'job_scores' table (F3 Bonus: Embedding & LLM Breakdown)
            score_cols = {r["name"] for r in conn.execute("PRAGMA table_info(job_scores)").fetchall()}
            if "embedding_score" not in score_cols:
                conn.execute("ALTER TABLE job_scores ADD COLUMN embedding_score REAL;")
            if "llm_score" not in score_cols:
                conn.execute("ALTER TABLE job_scores ADD COLUMN llm_score REAL;")
            if "final_score" not in score_cols:
                conn.execute("ALTER TABLE job_scores ADD COLUMN final_score REAL;")

            # Indexes for ultra-fast dashboard and query operations
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_job_hash ON applications(job_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_status ON applications(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_applied_date ON applications(applied_date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_relevance ON applications(relevance_score);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_status ON approval_queue(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scores_job_hash ON job_scores(job_hash);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_store(memory_key);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_email_status ON email_history(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runs_start ON agent_runs(start_time);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_portal_status ON applications(portal, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_apps_created_at ON applications(created_at DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pdf_hr_status ON pdf_hr_outreach(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_approval_status_score ON approval_queue(status, score DESC);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_inbox_uid ON inbox_processed_messages(mailbox, imap_uid);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_csv_drip_email ON csv_drip_contacts(email);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_csv_drip_status ON csv_drip_contacts(status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_summaries_date ON daily_memory_summaries(date);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_daily_summaries_run_id ON daily_memory_summaries(run_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_mem_emb_key ON memory_embeddings(memory_key);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_cache_endpoint ON api_cache(endpoint);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_api_cache_expires ON api_cache(expires_at);")

        if self.db_path not in _INITIALIZED_DBS:
            _INITIALIZED_DBS.add(self.db_path)
            logger.info(f"CareerPilot 14-table database initialized at: {self.db_path}")

    def _seed_candidate_profile_if_empty(self):
        """Seed Manoj Kumar Mandal's profile, skills, and preferences from user_profile.json if empty."""
        from config import PROFILE_PATH
        with self._get_conn() as conn:
            existing = conn.execute("SELECT id FROM candidate_profile LIMIT 1;").fetchone()
            if existing:
                return

            p_path = Path(PROFILE_PATH)
            if not p_path.exists():
                return

            try:
                with open(p_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                personal = data.get("personal", {})
                education = data.get("education", {})
                verified_skills = data.get("verified_skills", {})

                # Insert candidate profile
                cursor = conn.execute("""
                    INSERT INTO candidate_profile (
                        name, email, phone, location, education_degree,
                        education_college, education_year, education_cgpa,
                        linkedin_url, github_url, portfolio_url, resume_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    personal.get("name", "Manoj Kumar Mandal"),
                    personal.get("email", "manojkumarmandal1111@gmail.com"),
                    personal.get("phone", "+91-8404970494"),
                    personal.get("location", "India"),
                    education.get("degree", "B.Tech Computer Science and Engineering"),
                    education.get("college", "Satya Sai Engineering College Balasore, Odisha"),
                    education.get("year", "2026"),
                    education.get("cgpa", "8.01/10"),
                    personal.get("linkedin_url", ""),
                    personal.get("github_url", ""),
                    personal.get("portfolio_url", ""),
                    personal.get("resume_path", "data/resume.pdf"),
                ))
                profile_id = cursor.lastrowid

                # Insert verified skills with exact proficiency levels
                for proficiency_level, skills in verified_skills.items():
                    level_name = proficiency_level.capitalize()
                    if level_name in ("Proficient", "Intermediate", "Learning"):
                        for skill in skills:
                            conn.execute("""
                                INSERT OR IGNORE INTO candidate_skills (
                                    profile_id, skill_name, category, proficiency, verified, source
                                ) VALUES (?, ?, ?, ?, 1, 'verified_declaration')
                            """, (profile_id, skill, "AI/Software", level_name))

                # Insert resume qualifications as verified skills
                resume_qualifications = data.get("resume_qualifications", {})
                for category, skills in resume_qualifications.items():
                    cat_name = category.replace("_", " ").title()
                    for s in skills:
                        conn.execute("""
                            INSERT OR IGNORE INTO candidate_skills (
                                profile_id, skill_name, category, proficiency, verified, source
                            ) VALUES (?, ?, ?, 'Intermediate', 1, 'resume_extracted')
                        """, (profile_id, s, cat_name))

                # Insert candidate preferences
                conn.execute("""
                    INSERT INTO candidate_preferences (
                        profile_id, target_roles, target_keywords, exclude_keywords,
                        preferred_locations, matching_threshold, min_salary_lpa,
                        allow_remote, allow_internships, max_apply_per_day, max_email_per_day
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    profile_id,
                    json.dumps(data.get("target_roles", [])),
                    json.dumps(data.get("target_keywords", [])),
                    json.dumps(data.get("exclude_keywords", [])),
                    json.dumps(data.get("preferred_locations", [])),
                    float(data.get("matching_threshold", 7.0)),
                    float(data.get("min_salary_lpa", 3.6)),
                    1 if data.get("allow_remote", True) else 0,
                    1 if data.get("allow_internships", True) else 0,
                    int(data.get("max_apply_per_day", 30)),
                    int(data.get("max_email_per_day", 25)),
                ))
                logger.info(f"Seeded Candidate Profile, verified skills, and preferences for Manoj Kumar Mandal (ID: {profile_id}).")
            except Exception as e:
                logger.error(f"Error seeding candidate profile: {e}")

    # =========================================================================
    # APPLICATION CRUD
    # =========================================================================

    
    @staticmethod
    def make_job_hash(job_url: str) -> str:
        """Create a deterministic SHA-256 job hash from a valid URL."""
        if not isinstance(job_url, str):
            return ""

        normalized_url = job_url.strip()

        if not normalized_url:
            return ""

        return hashlib.sha256(
            normalized_url.encode("utf-8")
        ).hexdigest()[:16]

    

    def is_already_seen(self, job_url: str) -> bool:
        """Check if job has already been fully processed ('applied' or 'email_sent')."""
        job_hash = self.make_job_hash(job_url)
        with self._get_conn() as conn:
            row = conn.execute("SELECT status FROM applications WHERE job_hash = ?", (job_hash,)).fetchone()
        if not row:
            return False
        return row["status"] in ("applied", "email_sent")

    def add_job(self, job: Dict[str, Any]) -> bool:
        """Insert a newly scraped job. Returns True if inserted, False if already exists."""
        job_url = job.get("url") or job.get("job_url", "")
        if not job_url:
            return False
        job_hash = self.make_job_hash(job_url)

        with self._get_conn() as conn:
            existing = conn.execute("SELECT 1 FROM applications WHERE job_hash = ?", (job_hash,)).fetchone()
            if existing:
                return False

            try:
                conn.execute("""
                    INSERT INTO applications (
                        job_hash, job_url, job_title, company, location, portal,
                        job_description, status, relevance_score, salary, experience
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?, ?)
                """, (
                    job_hash,
                    job_url,
                    job.get("job_title") or job.get("title", "Untitled Job"),
                    job.get("company_name") or job.get("company", "Unknown"),
                    job.get("location", "Remote"),
                    job.get("source") or job.get("portal", "direct"),
                    (job.get("job_description") or job.get("description", "") or "")[:4000],
                    float(job.get("relevance_score", 0.0)),
                    job.get("salary", "") or "",
                    job.get("experience_required") or job.get("experience", "") or "",
                ))
                return True
            except sqlite3.IntegrityError:
                return False

    def save_application(self, job: Dict[str, Any]) -> bool:
        """Alias for add_job for consistency across services."""
        return self.add_job(job)

    
    
    def update_application_status(
        self,
        job_hash: str,
        status: str,
        notes: str = ""
    ) -> bool:
        """Update application status with validation and record-existence checks."""

        if not isinstance(job_hash, str) or not job_hash.strip():
            raise ValueError("A valid job_hash is required.")

        valid_statuses = {
            "new",
            "applied",
            "manual_required",
            "interview",
            "rejected",
            "failed",
            "email_sent",
        }

        if not isinstance(status, str):
            raise ValueError("Application status must be a string.")

        normalized_status = status.strip().lower()

        if normalized_status not in valid_statuses:
            raise ValueError(
                f"Invalid application status: {normalized_status}"
            )

        if not isinstance(notes, str):
            raise ValueError("Application notes must be a string.")

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE applications
                SET
                    status = ?,
                    applied_date = CASE
                        WHEN ? = 'applied'
                        THEN ?
                        ELSE applied_date
                    END,
                    notes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE job_hash = ?
                """,
                (
                    normalized_status,
                    normalized_status,
                    datetime.now().isoformat(),
                    notes.strip(),
                    job_hash.strip(),
                ),
            )

            return cursor.rowcount > 0


    def get_applications(
        self,
        page: int = 1,
        per_page: int = 20,
        status: str = "all",
        portal: str = "all",
        search: str = ""
    ) -> Dict[str, Any]:
        """Fetch paginated applications with multi-field search and filters."""
        with self._get_conn() as conn:
            where_clauses = []
            params = []

            if status != "all":
                where_clauses.append("status = ?")
                params.append(status)
            if portal != "all":
                where_clauses.append("portal = ?")
                params.append(portal)
            if search:
                where_clauses.append("(job_title LIKE ? OR company LIKE ? OR location LIKE ?)")
                query_param = f"%{search}%"
                params.extend([query_param, query_param, query_param])

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            total = conn.execute(f"SELECT COUNT(*) FROM applications {where_sql}", params).fetchone()[0]
            offset = max(0, (page - 1) * per_page)

            rows = conn.execute(f"""
                SELECT job_hash, job_title, company, location, portal, status,
                       applied_date, hr_email, email_sent, relevance_score, job_url,
                       salary, experience, notes, created_at
                FROM applications {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, params + [per_page, offset]).fetchall()

            return {
                "applications": [dict(r) for r in rows],
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if total > 0 else 1,
            }

    # =========================================================================
    # JOB SCORING CRUD
    # =========================================================================

    def save_job_score(self, score_data: Dict[str, Any]):
        """Persist structured LLM and embedding score details."""
        rel_score = float(score_data.get("final_score") or score_data.get("relevance_score", 0.0))
        llm_sc = score_data.get("llm_score")
        if llm_sc is not None:
            llm_sc = float(llm_sc)
        emb_sc = score_data.get("embedding_score")
        if emb_sc is not None:
            emb_sc = float(emb_sc)
        final_sc = float(score_data.get("final_score", rel_score))

        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO job_scores (
                    job_hash, relevance_score, skills_match_ratio, experience_match,
                    location_match, matched_skills, missing_skills, reasoning, raw_gemini_response,
                    embedding_score, llm_score, final_score
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_hash) DO UPDATE SET
                    relevance_score = excluded.relevance_score,
                    skills_match_ratio = excluded.skills_match_ratio,
                    experience_match = excluded.experience_match,
                    location_match = excluded.location_match,
                    matched_skills = excluded.matched_skills,
                    missing_skills = excluded.missing_skills,
                    reasoning = excluded.reasoning,
                    raw_gemini_response = excluded.raw_gemini_response,
                    embedding_score = excluded.embedding_score,
                    llm_score = excluded.llm_score,
                    final_score = excluded.final_score,
                    scored_at = CURRENT_TIMESTAMP
            """, (
                score_data["job_hash"],
                rel_score,
                float(score_data.get("skills_match_ratio", 0.0)),
                1 if score_data.get("experience_match", True) else 0,
                1 if score_data.get("location_match", True) else 0,
                json.dumps(score_data.get("matched_skills", [])),
                json.dumps(score_data.get("missing_skills", [])),
                score_data.get("reasoning", ""),
                score_data.get("raw_gemini_response", ""),
                emb_sc,
                llm_sc,
                final_sc
            ))
            # Also update relevance_score in applications table
            conn.execute("""
                UPDATE applications SET relevance_score = ?, updated_at = CURRENT_TIMESTAMP
                WHERE job_hash = ?
            """, (final_sc, score_data["job_hash"]))

    def get_job_score(self, job_hash: str) -> Optional[Dict[str, Any]]:
        """Retrieve score details for a job."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM job_scores WHERE job_hash = ?", (job_hash,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["matched_skills"] = json.loads(res.get("matched_skills") or "[]")
            res["missing_skills"] = json.loads(res.get("missing_skills") or "[]")
            return res

    # =========================================================================
    # APPROVAL QUEUE CRUD (Mandatory Human Safety Control)
    # =========================================================================

    
    def enqueue_for_approval(
        self,
        item_type: str,
        reference_id: str,
        title: str,
        company: str,
        details: str,
        preview_payload: Dict[str, Any],
        score: float = 0.0
    ) -> int:
        """Enqueue an application or email action for user review."""

        if item_type not in ("job_application", "cold_email", "drip_email"):
            raise ValueError(f"Invalid approval item_type: {item_type}")

        # New job applications can be considered for execution.
        # Email records remain outside the application execution lifecycle.
        execution_status = (
            "not_started"
            if item_type == "job_application"
            else "unknown"
        )

        with self._get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO approval_queue (
                    item_type,
                    reference_id,
                    title,
                    company,
                    details,
                    preview_payload,
                    score,
                    status,
                    execution_status
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                item_type,
                reference_id,
                title,
                company,
                details,
                json.dumps(preview_payload),
                score,
                execution_status
            ))

            return cursor.lastrowid


    
    def get_approval_queue(
        self,
        status: str = "pending",
        limit: int = 50,
        item_type: Optional[str] = None,
        execution_status: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Fetch approval queue items with optional type and execution filters."""

        query = """
            SELECT * FROM approval_queue
            WHERE status = ?
        """
        params = [status]

        if item_type:
            query += " AND item_type = ?"
            params.append(item_type)

        if execution_status:
            query += " AND execution_status = ?"
            params.append(execution_status)

        query += """
            ORDER BY score DESC, created_at DESC
            LIMIT ?
        """
        params.append(limit)

        with self._get_conn() as conn:
            rows = conn.execute(query, params).fetchall()

            items = []

            for row in rows:
                item = dict(row)
                item["preview_payload"] = json.loads(
                    item.get("preview_payload") or "{}"
                )
                items.append(item)

        return items


    
    
    def claim_approval_execution(
        self,
        queue_id: int,
        job_hash: str
    ) -> Optional[str]:
        """Atomically claim one approved job application."""

        import uuid

        if (
            not isinstance(queue_id, int)
            or isinstance(queue_id, bool)
            or queue_id <= 0
        ):
            raise ValueError("queue_id must be a positive integer.")

        if not isinstance(job_hash, str) or not job_hash.strip():
            raise ValueError("A valid job_hash is required.")

        claim_token = uuid.uuid4().hex

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO approval_execution_claims (
                    queue_id,
                    claim_token
                )
                SELECT id, ?
                FROM approval_queue
                WHERE id = ?
                  AND reference_id = ?
                  AND status = 'approved'
                  AND item_type = 'job_application'
                  AND execution_status = 'not_started'
                ON CONFLICT(queue_id) DO NOTHING
                """,
                (claim_token, queue_id, job_hash.strip())
            )

            if cursor.rowcount != 1:
                return None

        return claim_token


    def update_approval_execution_status(
        self,
        queue_id: int,
        execution_status: str,
        claim_token: str
    ) -> bool:
        """Finalize execution only with the matching active claim."""

        allowed_statuses = {
            "manual_required",
            "applied",
            "failed"
        }

        if (
            not isinstance(queue_id, int)
            or isinstance(queue_id, bool)
            or queue_id <= 0
        ):
            raise ValueError("queue_id must be a positive integer.")

        if execution_status not in allowed_statuses:
            raise ValueError(
                f"Invalid execution status: {execution_status}"
            )

        if not isinstance(claim_token, str) or not claim_token.strip():
            raise ValueError("A valid claim token is required.")

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE approval_execution_claims
                SET
                    finalized_at = CURRENT_TIMESTAMP,
                    final_status = ?
                WHERE queue_id = ?
                  AND claim_token = ?
                  AND finalized_at IS NULL
                """,
                (execution_status, queue_id, claim_token)
            )

            if cursor.rowcount != 1:
                return False

            queue_cursor = conn.execute(
                """
                UPDATE approval_queue
                SET execution_status = ?
                WHERE id = ?
                  AND item_type = 'job_application'
                  AND status = 'approved'
                  AND execution_status = 'not_started'
                """,
                (execution_status, queue_id)
            )

            if queue_cursor.rowcount != 1:
                raise RuntimeError(
                    "Execution claim finalized but approval status "
                    "could not be updated."
                )

            return True





    def claim_email_execution(
        self,
        queue_id: int
    ) -> Optional[str]:
        """Atomically claim one approved cold email."""

        import uuid

        if (
            not isinstance(queue_id, int)
            or isinstance(queue_id, bool)
            or queue_id <= 0
        ):
            raise ValueError("queue_id must be a positive integer.")

        claim_token = uuid.uuid4().hex

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO email_execution_claims (
                    queue_id,
                    claim_token
                )
                SELECT id, ?
                FROM approval_queue
                WHERE id = ?
                  AND status = 'approved'
                  AND item_type IN ('cold_email', 'drip_email')
                ON CONFLICT(queue_id) DO NOTHING
                """,
                (claim_token, queue_id)
            )

            if cursor.rowcount != 1:
                return None

        return claim_token


    def finalize_email_execution(
        self,
        queue_id: int,
        claim_token: str,
        final_status: str
    ) -> bool:
        """Finalize an email claim exactly once."""

        allowed_statuses = {"sent", "uncertain", "failed"}

        if (
            not isinstance(queue_id, int)
            or isinstance(queue_id, bool)
            or queue_id <= 0
        ):
            raise ValueError("queue_id must be a positive integer.")

        if final_status not in allowed_statuses:
            raise ValueError("Invalid email execution status.")

        if not isinstance(claim_token, str) or not claim_token.strip():
            raise ValueError("A valid claim token is required.")

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE email_execution_claims
                SET
                    finalized_at = CURRENT_TIMESTAMP,
                    final_status = ?
                WHERE queue_id = ?
                  AND claim_token = ?
                  AND finalized_at IS NULL
                """,
                (final_status, queue_id, claim_token)
            )

            return cursor.rowcount == 1

    # ------------------------------------------------------------------
    # F5: Persistent Daily Sending Limits & Concurrency Safety
    # ------------------------------------------------------------------

    def get_daily_sent_email_count(self) -> int:
        """
        Return the count of successful real email transmissions sent today.
        Counts real successful transmissions from email_history.
        Dry-run or failed attempts are not counted.
        Persistent across application restarts and multiple runs.
        """
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT COUNT(*) as cnt
                FROM email_history
                WHERE status = 'sent'
                  AND date(COALESCE(sent_at, created_at)) = date('now', 'localtime')
                """
            ).fetchone()
            return int(row["cnt"]) if row else 0

    def can_send_email(self, limit: Optional[int] = None) -> Tuple[bool, str]:
        """
        Check if daily email transmission limit has been reached.
        Enforced before every live transmission.
        """
        if limit is None:
            try:
                import config as cfg
                max_limit = int(getattr(cfg, "MAX_EMAILS_PER_DAY", 25))
            except Exception:
                max_limit = 25
        else:
            max_limit = int(limit)

        sent_today = self.get_daily_sent_email_count()
        if sent_today >= max_limit:
            return False, f"REJECT_DAILY_EMAIL_LIMIT_REACHED: {sent_today}/{max_limit} emails sent today"
        return True, f"OK: {sent_today}/{max_limit} emails sent today"

    # ------------------------------------------------------------------
    # F5: IMAP Inbox Message Deduplication
    # ------------------------------------------------------------------

    def is_inbox_message_processed(self, mailbox: str, imap_uid: str) -> bool:
        """Check if an IMAP message UID has already been processed in this mailbox."""
        if not imap_uid:
            return False
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM inbox_processed_messages
                WHERE mailbox = ? AND imap_uid = ?
                LIMIT 1
                """,
                (mailbox, str(imap_uid))
            ).fetchone()
            return bool(row)

    def record_inbox_processed_message(
        self,
        mailbox: str,
        imap_uid: str,
        message_id: Optional[str] = None,
        sender: Optional[str] = None,
        subject: Optional[str] = None,
        received_at: Optional[str] = None,
        classification: Optional[str] = None,
        processing_status: str = "processed",
        bounced_recipient: Optional[str] = None
    ) -> bool:
        """Record an IMAP message UID to prevent repeated processing across polling cycles."""
        if not imap_uid:
            return False
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO inbox_processed_messages (
                    mailbox, imap_uid, message_id, sender, subject,
                    received_at, classification, processing_status, bounced_recipient
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mailbox, imap_uid) DO NOTHING
                """,
                (
                    mailbox, str(imap_uid), message_id or "", sender or "", subject or "",
                    received_at or "", classification or "", processing_status, bounced_recipient or ""
                )
            )
            return cursor.rowcount > 0

    # ------------------------------------------------------------------
    # F5: CSV Secondary HR Drip Campaign Management
    # ------------------------------------------------------------------

    def import_csv_drip_contacts(
        self,
        contacts: List[Dict[str, Any]],
        source_file: str = "manual_import.csv"
    ) -> Dict[str, int]:
        """
        Import contacts into csv_drip_contacts table with strict duplicate prevention.
        """
        stats = {"imported": 0, "duplicates": 0, "invalid": 0}
        with self._get_conn() as conn:
            for c in contacts:
                email = (c.get("email") or "").strip().lower()
                company = (c.get("company") or "").strip()
                name = (c.get("name") or "").strip() or "Hiring Manager"
                role = (c.get("role") or "").strip() or "Engineering"

                if not email or "@" not in email or not company:
                    stats["invalid"] += 1
                    continue

                cursor = conn.execute(
                    """
                    INSERT INTO csv_drip_contacts (company, name, email, role, source_file, status)
                    VALUES (?, ?, ?, ?, ?, 'pending')
                    ON CONFLICT(email) DO NOTHING
                    """,
                    (company, name, email, role, source_file)
                )
                if cursor.rowcount == 1:
                    stats["imported"] += 1
                else:
                    stats["duplicates"] += 1
        return stats

    def get_pending_csv_drip_contacts(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Fetch pending CSV drip contacts sequentially by ID."""
        with self._get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, company, name, email, role, status, source_file, queue_id
                FROM csv_drip_contacts
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT ?
                """,
                (max(1, int(limit)),)
            ).fetchall()
            return [dict(r) for r in rows]

    def update_csv_drip_contact_status(
        self,
        contact_id: int,
        status: str,
        queue_id: Optional[int] = None,
        error_message: Optional[str] = None
    ) -> bool:
        """Update status of a CSV drip contact."""
        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE csv_drip_contacts
                SET status = ?,
                    queue_id = COALESCE(?, queue_id),
                    error_message = ?,
                    sent_at = CASE WHEN ? = 'sent' THEN CURRENT_TIMESTAMP ELSE sent_at END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (status, queue_id, error_message, status, contact_id)
            )
            return cursor.rowcount > 0

    def get_csv_drip_stats(self) -> Dict[str, Any]:
        """Return metrics for the CSV secondary HR drip campaign."""
        with self._get_conn() as conn:
            row = conn.execute(
                """
                SELECT
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) as pending,
                    COALESCE(SUM(CASE WHEN status = 'staged' THEN 1 ELSE 0 END), 0) as staged,
                    COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) as approved,
                    COALESCE(SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END), 0) as sent,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed,
                    COALESCE(SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END), 0) as skipped,
                    COALESCE(SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END), 0) as completed,
                    COALESCE(SUM(CASE WHEN status = 'bounced' THEN 1 ELSE 0 END), 0) as bounced
                FROM csv_drip_contacts
                """
            ).fetchone()
            return dict(row) if row else {"total": 0, "pending": 0, "staged": 0, "approved": 0, "sent": 0, "failed": 0, "skipped": 0, "completed": 0, "bounced": 0}

    
    def decide_approval(
        self,
        queue_id: int,
        decision: str,
        notes: str = ""
    ) -> bool:
        """Decide an approval item only while it is pending."""

        if not isinstance(queue_id, int) or isinstance(queue_id, bool) or queue_id <= 0:
            raise ValueError("A valid positive queue_id is required.")

        if not isinstance(decision, str):
            raise ValueError("Decision must be a string.")

        normalized_decision = decision.strip().lower()

        if normalized_decision not in ("approved", "rejected", "cancelled"):
            raise ValueError(
                f"Invalid approval decision: {normalized_decision}"
            )

        if not isinstance(notes, str):
            raise ValueError("Reviewer notes must be a string.")

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                UPDATE approval_queue
                SET
                    status = ?,
                    decided_at = ?,
                    reviewer_notes = ?
                WHERE id = ?
                AND status = 'pending'
                """,
                (
                    normalized_decision,
                    datetime.now().isoformat(),
                    notes.strip(),
                    queue_id
                )
            )

            return cursor.rowcount == 1


    # =========================================================================
    # CANDIDATE PROFILE & SKILLS CRUD
    # =========================================================================

    def get_candidate_profile(self, profile_id: int = 1) -> Optional[Dict[str, Any]]:
        """Fetch full candidate profile details."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM candidate_profile WHERE id = ?", (profile_id,)).fetchone()
            if not row:
                return None
            profile = dict(row)
            skills = conn.execute("SELECT * FROM candidate_skills WHERE profile_id = ? ORDER BY proficiency ASC", (profile_id,)).fetchall()
            prefs = conn.execute("SELECT * FROM candidate_preferences WHERE profile_id = ?", (profile_id,)).fetchone()

            profile["skills"] = [dict(s) for s in skills]
            if prefs:
                p = dict(prefs)
                profile["preferences"] = {
                    "target_roles": json.loads(p.get("target_roles") or "[]"),
                    "target_keywords": json.loads(p.get("target_keywords") or "[]"),
                    "exclude_keywords": json.loads(p.get("exclude_keywords") or "[]"),
                    "preferred_locations": json.loads(p.get("preferred_locations") or "[]"),
                    "matching_threshold": p.get("matching_threshold", 7.0),
                    "min_salary_lpa": p.get("min_salary_lpa", 3.6),
                    "allow_remote": bool(p.get("allow_remote", 1)),
                    "allow_internships": bool(p.get("allow_internships", 1)),
                    "max_apply_per_day": p.get("max_apply_per_day", 30),
                    "max_email_per_day": p.get("max_email_per_day", 25),
                }
            return profile

    def update_candidate_profile(self, profile_id: int, updates: Dict[str, Any]) -> bool:
        """Update candidate details and log to history."""
        allowed = [
            "name", "email", "phone", "location", "education_degree",
            "education_college", "education_year", "education_cgpa",
            "linkedin_url", "github_url", "portfolio_url", "resume_path", "summary"
        ]
        set_clauses = []
        params = []
        for k in allowed:
            if k in updates:
                set_clauses.append(f"{k} = ?")
                params.append(updates[k])

        if not set_clauses:
            return False

        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        params.append(profile_id)

        with self._get_conn() as conn:
            cursor = conn.execute(f"UPDATE candidate_profile SET {', '.join(set_clauses)} WHERE id = ?", params)
            return cursor.rowcount > 0

    def add_candidate_skill(self, profile_id: int, skill_name: str, proficiency: str, category: str = "General") -> bool:
        """Add or update a skill with specified proficiency (Learning, Intermediate, Proficient)."""
        if proficiency not in ("Learning", "Intermediate", "Proficient"):
            raise ValueError("Proficiency must be 'Learning', 'Intermediate', or 'Proficient'")
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO candidate_skills (profile_id, skill_name, proficiency, category, verified, source)
                VALUES (?, ?, ?, ?, 1, 'manual_input')
                ON CONFLICT(profile_id, skill_name) DO UPDATE SET
                    proficiency = excluded.proficiency,
                    category = excluded.category
            """, (profile_id, skill_name.strip(), proficiency, category))
            return True

    def delete_candidate_skill(self, skill_id: int) -> bool:
        """Remove a skill entry."""
        with self._get_conn() as conn:
            cursor = conn.execute("DELETE FROM candidate_skills WHERE id = ?", (skill_id,))
            return cursor.rowcount > 0

    def update_candidate_preferences(self, profile_id: int, prefs: Dict[str, Any]) -> bool:
        """Update candidate matching preferences."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE candidate_preferences SET
                    target_roles = ?,
                    target_keywords = ?,
                    exclude_keywords = ?,
                    preferred_locations = ?,
                    matching_threshold = ?,
                    min_salary_lpa = ?,
                    allow_remote = ?,
                    allow_internships = ?,
                    max_apply_per_day = ?,
                    max_email_per_day = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE profile_id = ?
            """, (
                json.dumps(prefs.get("target_roles", [])),
                json.dumps(prefs.get("target_keywords", [])),
                json.dumps(prefs.get("exclude_keywords", [])),
                json.dumps(prefs.get("preferred_locations", [])),
                float(prefs.get("matching_threshold", 7.0)),
                float(prefs.get("min_salary_lpa", 3.6)),
                1 if prefs.get("allow_remote", True) else 0,
                1 if prefs.get("allow_internships", True) else 0,
                int(prefs.get("max_apply_per_day", 30)),
                int(prefs.get("max_email_per_day", 25)),
                profile_id
            ))
            return True

    # =========================================================================
    # AGENT RUNS & DAILY STATS CRUD
    # =========================================================================

    def record_agent_run_start(self, run_id: str, dry_run: bool = True):
        """Record the initiation of an agent pipeline execution."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO agent_runs (run_id, start_time, status, dry_run)
                VALUES (?, ?, 'running', ?)
            """, (run_id, datetime.now().isoformat(), 1 if dry_run else 0))

    def record_agent_run_complete(
        self,
        run_id: str,
        status: str,
        duration_seconds: float,
        scraped: int = 0,
        new_jobs: int = 0,
        scored: int = 0,
        applied: int = 0,
        emailed: int = 0,
        failed: int = 0,
        pdf_emails: int = 0,
        logs: str = "",
        errors: str = ""
    ):
        """Record agent completion metrics."""
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE agent_runs SET
                    end_time = ?,
                    duration_seconds = ?,
                    status = ?,
                    total_scraped = ?,
                    total_new = ?,
                    total_scored = ?,
                    total_applied = ?,
                    total_emailed = ?,
                    total_failed = ?,
                    pdf_emails_sent = ?,
                    logs = ?,
                    errors = ?
                WHERE run_id = ?
            """, (
                datetime.now().isoformat(),
                duration_seconds,
                status,
                scraped,
                new_jobs,
                scored,
                applied,
                emailed,
                failed,
                pdf_emails,
                logs,
                errors,
                run_id
            ))

    def update_daily_stats(self, date: str, **kwargs):
        """Upsert daily running statistics."""
        with self._get_conn() as conn:
            existing = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (date,)).fetchone()
            if existing:
                updates = ", ".join(f"{k} = {k} + ?" for k in kwargs)
                conn.execute(
                    f"UPDATE daily_stats SET {updates} WHERE date = ?",
                    list(kwargs.values()) + [date]
                )
            else:
                cols = "date, " + ", ".join(kwargs.keys())
                vals = "?, " + ", ".join("?" * len(kwargs))
                conn.execute(
                    f"INSERT INTO daily_stats ({cols}) VALUES ({vals})",
                    [date] + list(kwargs.values())
                )

    def get_daily_stats(self, date: str) -> Dict[str, Any]:
        """Fetch statistics for a given calendar date."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM daily_stats WHERE date = ?", (date,)).fetchone()
            return dict(row) if row else {}

    def get_today_applied_count(self) -> int:
        """Count applications submitted today."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM applications WHERE status = 'applied' AND applied_date LIKE ?",
                (f"{today_str}%",)
            ).fetchone()
            return row["cnt"] if row else 0

    def get_all_time_stats(self) -> Dict[str, Any]:
        """Aggregate system-wide statistics for the dashboard."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT 
                    COUNT(*) as total_jobs_seen,
                    COALESCE(SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END), 0) as total_applied,
                    COALESCE(SUM(CASE WHEN email_sent = 1 THEN 1 ELSE 0 END), 0) as total_emailed,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as total_failed,
                    COUNT(DISTINCT company) as unique_companies,
                    COALESCE(ROUND(AVG(relevance_score), 1), 0.0) as avg_match_score
                FROM applications;
            """).fetchone()
            stats = dict(row) if row else {}
            # Failed emails from email_history
            failed_emails_row = conn.execute("SELECT COUNT(*) FROM email_history WHERE status = 'failed';").fetchone()
            stats["total_failed_emails"] = failed_emails_row[0] if failed_emails_row else 0
            # HR outreach sent
            hr_sent_row = conn.execute("SELECT COUNT(*) FROM pdf_hr_outreach WHERE status = 'sent';").fetchone()
            stats["total_hr_outreach_sent"] = hr_sent_row[0] if hr_sent_row else 0
            return stats

    def get_chart_stats(self, days: int = 14) -> List[Dict[str, Any]]:
        """Fetch daily trends for Chart.js visualization."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT date, total_applied, total_emailed, total_scraped
                FROM daily_stats
                ORDER BY date DESC
                LIMIT ?
            """, (days,)).fetchall()
            return [dict(r) for r in reversed(rows)]

    # =========================================================================
    # HR CONTACTS & PDF DRIP CAMPAIGN CRUD
    # =========================================================================

    def add_hr_contact(self, company: str, domain: str, hr_name: str, email: str, source: str):
        """Add an HR contact record."""
        with self._get_conn() as conn:
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO hr_contacts (company, company_domain, hr_name, hr_email, source)
                    VALUES (?, ?, ?, ?, ?)
                """, (company, domain, hr_name, email.strip().lower(), source))
            except Exception:
                pass

    
    def get_pdf_hr_batch(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch only explicitly pending HR contacts for outreach."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT email, name, company, designation, status
                FROM pdf_hr_outreach
                WHERE status = 'pending'
                ORDER BY id ASC
                LIMIT ?
            """, (max(1, int(limit)),)).fetchall()

            return [dict(row) for row in rows]


    def mark_pdf_hr_sent(self, email: str, status: str = "sent"):
        """Update outreach status for a contact."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            conn.execute("""
                UPDATE pdf_hr_outreach
                SET status = ?, sent_date = ?
                WHERE email = ?
            """, (status, today, email.strip().lower()))

    def get_pdf_hr_stats(self) -> Dict[str, int]:
        """Fetch PDF HR drip campaign progress metrics."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COALESCE(COUNT(*), 0) as total,
                    COALESCE(SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END), 0) as sent,
                    COALESCE(SUM(CASE WHEN sent_date = ? AND status = 'sent' THEN 1 ELSE 0 END), 0) as sent_today
                FROM pdf_hr_outreach;
            """, (today,)).fetchone()
            if not row:
                return {"total": 0, "sent": 0, "sent_today": 0}
            return {
                "total": int(row["total"]),
                "sent": int(row["sent"]),
                "sent_today": int(row["sent_today"]),
            }

    # =========================================================================
    # MEMORY STORE & API CACHING
    # =========================================================================

    def store_memory(self, memory_key: str, category: str, content: str, metadata: Dict[str, Any] = None):
        """Persist a cross-session contextual memory string."""
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO memory_store (memory_key, category, content, metadata, updated_at)
                VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(memory_key) DO UPDATE SET
                    content = excluded.content,
                    metadata = excluded.metadata,
                    updated_at = CURRENT_TIMESTAMP
            """, (memory_key, category, content, json.dumps(metadata or {})))

    def get_memory(self, memory_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve a stored memory by key."""
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM memory_store WHERE memory_key = ?", (memory_key,)).fetchone()
            if not row:
                return None
            res = dict(row)
            res["metadata"] = json.loads(res.get("metadata") or "{}")
            return res

    def get_api_cache(self, cache_key: str) -> Optional[str]:
        """Fetch unexpired API response cache."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT response_data FROM api_cache
                WHERE cache_key = ? AND (expires_at IS NULL OR expires_at > datetime('now'))
            """, (cache_key,)).fetchone()
            return row["response_data"] if row else None

    def set_api_cache(self, cache_key: str, api_provider: str, request_hash: str, response_data: str, ttl_hours: int = 24):
        """Cache an external API response to prevent redundant quota usage."""
        expires_at = (datetime.now() + timedelta(hours=ttl_hours)).isoformat()
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO api_cache (cache_key, api_provider, request_hash, response_data, expires_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_data = excluded.response_data,
                    expires_at = excluded.expires_at,
                    created_at = CURRENT_TIMESTAMP
            """, (cache_key, api_provider, request_hash, response_data, expires_at))

    # ── Daily Memory Summaries ────────────────────────────────────────────────
    def save_daily_memory_summary(self, summary: Dict[str, Any]) -> int:
        """Store persistent daily workflow summary reflecting actual database operations."""
        run_id = summary.get("run_id") or f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        date_str = summary.get("date") or datetime.now().strftime("%Y-%m-%d")

        scraped = int(summary.get("total_scraped", 0))
        filtered = int(summary.get("total_filtered", 0))
        scored = int(summary.get("total_scored", 0))
        applied = int(summary.get("total_applications", 0))
        emailed = int(summary.get("total_emails_sent", 0))
        pending = int(summary.get("pending_approvals", 0))
        failed = int(summary.get("failed_operations", 0))

        important_errors = summary.get("important_errors", [])
        if not isinstance(important_errors, str):
            important_errors = json.dumps(important_errors)

        completed_actions = summary.get("completed_actions", [])
        if not isinstance(completed_actions, str):
            completed_actions = json.dumps(completed_actions)

        summary_text = summary.get("summary_text", "")

        with self._get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO daily_memory_summaries (
                    run_id, date, total_scraped, total_filtered, total_scored,
                    total_applications, total_emails_sent, pending_approvals,
                    failed_operations, important_errors, completed_actions,
                    summary_text
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, date) DO UPDATE SET
                    total_scraped = excluded.total_scraped,
                    total_filtered = excluded.total_filtered,
                    total_scored = excluded.total_scored,
                    total_applications = excluded.total_applications,
                    total_emails_sent = excluded.total_emails_sent,
                    pending_approvals = excluded.pending_approvals,
                    failed_operations = excluded.failed_operations,
                    important_errors = excluded.important_errors,
                    completed_actions = excluded.completed_actions,
                    summary_text = excluded.summary_text,
                    created_at = CURRENT_TIMESTAMP
            """, (
                run_id, date_str, scraped, filtered, scored, applied, emailed,
                pending, failed, important_errors, completed_actions, summary_text
            ))
            return cur.lastrowid

    def get_daily_memory_summaries(self, limit: int = 10, date: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve historical daily memory summaries."""
        with self._get_conn() as conn:
            if date:
                rows = conn.execute("""
                    SELECT * FROM daily_memory_summaries
                    WHERE date = ?
                    ORDER BY id DESC LIMIT ?
                """, (date, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM daily_memory_summaries
                    ORDER BY id DESC LIMIT ?
                """, (limit,)).fetchall()

            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["important_errors"] = json.loads(item.get("important_errors") or "[]")
                except Exception:
                    item["important_errors"] = []
                try:
                    item["completed_actions"] = json.loads(item.get("completed_actions") or "[]")
                except Exception:
                    item["completed_actions"] = []
                results.append(item)
            return results

    def get_latest_daily_memory_summary(self) -> Optional[Dict[str, Any]]:
        """Fetch the most recent daily memory summary."""
        summaries = self.get_daily_memory_summaries(limit=1)
        return summaries[0] if summaries else None

    def get_actual_workflow_counts(self, run_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculate actual, verified operation counts directly from authoritative database records.
        Guarantees that metrics reflect real records rather than unverified state counters.
        """
        with self._get_conn() as conn:
            # 1. Actual applications
            applied_row = conn.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN status IN ('applied', 'manual_required') THEN 1 ELSE 0 END), 0) as confirmed_applied,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed_applied
                FROM applications;
            """).fetchone()

            # 2. Actual sent cold emails
            emails_row = conn.execute("""
                SELECT
                    COALESCE(SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END), 0) as confirmed_sent,
                    COALESCE(SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END), 0) as failed_emails
                FROM email_history;
            """).fetchone()

            # 3. Pending human approvals
            pending_row = conn.execute("""
                SELECT COUNT(*) as pending_count
                FROM approval_queue
                WHERE status = 'pending';
            """).fetchone()

            return {
                "confirmed_applications": int(applied_row["confirmed_applied"] if applied_row else 0),
                "failed_applications": int(applied_row["failed_applied"] if applied_row else 0),
                "confirmed_emails_sent": int(emails_row["confirmed_sent"] if emails_row else 0),
                "failed_emails": int(emails_row["failed_emails"] if emails_row else 0),
                "pending_approvals": int(pending_row["pending_count"] if pending_row else 0),
            }

    def get_applied_job_hashes(self) -> set:
        """Fetch set of all job hashes that have reached applied or manual_required status."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT job_hash FROM applications
                WHERE status IN ('applied', 'manual_required') AND job_hash IS NOT NULL
            """).fetchall()
            return {r["job_hash"] for r in rows}

    def get_sent_email_recipients(self) -> set:
        """Fetch set of email addresses that have already received confirmed outreach."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT DISTINCT LOWER(TRIM(recipient_email)) as email
                FROM email_history
                WHERE status = 'sent' AND recipient_email IS NOT NULL
            """).fetchall()
            return {r["email"] for r in rows if r["email"]}

    def get_pending_approval_refs(self) -> List[Dict[str, Any]]:
        """Fetch reference IDs and types of items currently awaiting human decision."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, item_type, reference_id, title, company, score
                FROM approval_queue
                WHERE status = 'pending'
            """).fetchall()
            return [dict(r) for r in rows]

    # ── Enhanced Persistent API Response Cache ────────────────────────────────
    def set_api_response_cache(
        self,
        cache_key: str,
        endpoint: str,
        source: str,
        request_params: Optional[str],
        response_data: str,
        ttl_seconds: int = 86400,
        status_code: int = 200,
    ) -> str:
        """Cache an external API response with deterministic key, TTL, and status validation."""
        now_utc = datetime.now(timezone.utc)
        expires_at = (now_utc + timedelta(seconds=ttl_seconds)).strftime("%Y-%m-%d %H:%M:%S")
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO api_cache (
                    cache_key, api_provider, request_hash, response_data,
                    expires_at, endpoint, request_params, status_code,
                    ttl_seconds, is_valid
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(cache_key) DO UPDATE SET
                    response_data = excluded.response_data,
                    expires_at = excluded.expires_at,
                    endpoint = excluded.endpoint,
                    request_params = excluded.request_params,
                    status_code = excluded.status_code,
                    ttl_seconds = excluded.ttl_seconds,
                    is_valid = 1,
                    created_at = CURRENT_TIMESTAMP
            """, (
                cache_key, source, cache_key, response_data,
                expires_at, endpoint, request_params, status_code,
                ttl_seconds
            ))
            return cache_key

    def get_api_response_cache(self, cache_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve valid, unexpired API response cache entry."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM api_cache
                WHERE cache_key = ?
                  AND is_valid = 1
                  AND (expires_at IS NULL OR expires_at > datetime('now'))
            """, (cache_key,)).fetchone()
            if not row:
                return None
            return dict(row)

    def invalidate_api_cache(
        self,
        cache_key: Optional[str] = None,
        endpoint: Optional[str] = None,
        source: Optional[str] = None,
    ) -> int:
        """Invalidate cached API responses by key, endpoint, or source."""
        with self._get_conn() as conn:
            if cache_key:
                cur = conn.execute("UPDATE api_cache SET is_valid = 0 WHERE cache_key = ?", (cache_key,))
            elif endpoint:
                cur = conn.execute("UPDATE api_cache SET is_valid = 0 WHERE endpoint = ?", (endpoint,))
            elif source:
                cur = conn.execute("UPDATE api_cache SET is_valid = 0 WHERE api_provider = ?", (source,))
            else:
                cur = conn.execute("UPDATE api_cache SET is_valid = 0")
            return cur.rowcount

    def prune_expired_api_cache(self) -> int:
        """Delete expired or invalidated API cache entries."""
        with self._get_conn() as conn:
            cur = conn.execute("""
                DELETE FROM api_cache
                WHERE is_valid = 0
                   OR (expires_at IS NOT NULL AND expires_at <= datetime('now'))
            """)
            return cur.rowcount

    # ── Semantic Memory Embeddings ────────────────────────────────────────────
    def save_memory_embedding(
        self,
        memory_id: int,
        memory_key: str,
        embedding: List[float],
        model_name: str = "all-MiniLM-L6-v2",
    ) -> int:
        """Store dense vector embedding associated with a memory_store row."""
        emb_json = json.dumps([round(float(x), 6) for x in embedding])
        with self._get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO memory_embeddings (memory_id, memory_key, embedding, model_name)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    embedding = excluded.embedding,
                    model_name = excluded.model_name,
                    created_at = CURRENT_TIMESTAMP
            """, (memory_id, memory_key, emb_json, model_name))
            return cur.lastrowid

    def get_memory_embeddings(self) -> List[Dict[str, Any]]:
        """Retrieve all stored memory embeddings with linked memory details."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT me.id, me.memory_id, me.memory_key, me.embedding, me.model_name,
                       ms.category, ms.content, ms.metadata, ms.created_at
                FROM memory_embeddings me
                JOIN memory_store ms ON me.memory_id = ms.id
            """).fetchall()
            results = []
            for r in rows:
                item = dict(r)
                try:
                    item["embedding"] = json.loads(item["embedding"])
                except Exception:
                    item["embedding"] = []
                try:
                    item["metadata"] = json.loads(item.get("metadata") or "{}")
                except Exception:
                    item["metadata"] = {}
                results.append(item)
            return results

    # ── Memory Pruning & Retention ────────────────────────────────────────────
    def prune_historical_memory(
        self,
        retention_days: int = 30,
        preserve_minimum_summaries: int = 5,
    ) -> Dict[str, Any]:
        """
        Safely prune eligible historical memory summaries and expired cache.
        Strict Safety Contract:
          - NEVER deletes approval_queue, applications, email_history, or email_execution_claims.
          - Never deletes candidate_insights or high-value company interactions.
          - Preserves at least `preserve_minimum_summaries` latest daily summaries.
        """
        cutoff_date = (datetime.now() - timedelta(days=retention_days)).strftime("%Y-%m-%d")

        with self._get_conn() as conn:
            # 1. Expired cache entries pruned
            cache_deleted = self.prune_expired_api_cache()

            # 2. Check total summaries to guarantee minimum preserved
            total_summaries = conn.execute("SELECT COUNT(*) FROM daily_memory_summaries").fetchone()[0]
            summaries_deleted = 0

            if total_summaries > preserve_minimum_summaries:
                # Find ID of N-th most recent summary
                cutoff_id_row = conn.execute("""
                    SELECT id FROM daily_memory_summaries
                    ORDER BY id DESC LIMIT 1 OFFSET ?
                """, (preserve_minimum_summaries - 1,)).fetchone()

                if cutoff_id_row:
                    max_allowed_id = cutoff_id_row[0]
                    cur = conn.execute("""
                        DELETE FROM daily_memory_summaries
                        WHERE date < ? AND id <= ?
                    """, (cutoff_date, max_allowed_id))
                    summaries_deleted = cur.rowcount

            return {
                "cache_pruned": cache_deleted,
                "summaries_pruned": summaries_deleted,
                "retention_days": retention_days,
                "cutoff_date": cutoff_date,
            }

    def get_tavily_usage_today(self) -> int:
        """Fetch Tavily searches used today."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            row = conn.execute("SELECT count FROM tavily_usage WHERE date = ?", (today,)).fetchone()
            return row["count"] if row else 0

    def increment_tavily_usage(self):
        """Increment Tavily usage counter."""
        today = datetime.now().strftime("%Y-%m-%d")
        with self._get_conn() as conn:
            conn.execute("""
                INSERT INTO tavily_usage (date, count) VALUES (?, 1)
                ON CONFLICT(date) DO UPDATE SET count = count + 1
            """, (today,))

    # =========================================================================
    # DASHBOARD HELPER METHODS
    # =========================================================================

    def get_portal_stats(self) -> List[Dict[str, Any]]:
        """Fetch breakdown by job portal for dashboard visualization."""
        with self._get_conn() as conn:
            rows = conn.execute("""
                SELECT portal, COUNT(*) as count,
                       COALESCE(SUM(CASE WHEN status='applied' THEN 1 ELSE 0 END), 0) as applied,
                       COALESCE(SUM(CASE WHEN email_sent=1 THEN 1 ELSE 0 END), 0) as emailed
                FROM applications
                WHERE portal IS NOT NULL AND portal != ''
                GROUP BY portal
                ORDER BY count DESC
            """).fetchall()
            return [dict(r) for r in rows]

    def get_approval_queue_counts(self) -> Dict[str, int]:
        """Fetch aggregate approval queue counts by decision status and item type."""
        with self._get_conn() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) as pending,
                    COALESCE(SUM(CASE WHEN item_type = 'job_application' AND status = 'pending' THEN 1 ELSE 0 END), 0) as pending_applications,
                    COALESCE(SUM(CASE WHEN item_type IN ('cold_email', 'drip_email') AND status = 'pending' THEN 1 ELSE 0 END), 0) as pending_emails,
                    COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) as approved,
                    COALESCE(SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END), 0) as rejected
                FROM approval_queue
            """).fetchone()
            if not row:
                return {
                    "total": 0, "pending": 0, "pending_applications": 0,
                    "pending_emails": 0, "approved": 0, "rejected": 0
                }
            return {
                "total": row["total"] or 0,
                "pending": row["pending"] or 0,
                "pending_applications": row["pending_applications"] or 0,
                "pending_emails": row["pending_emails"] or 0,
                "approved": row["approved"] or 0,
                "rejected": row["rejected"] or 0,
            }

    
    def bulk_decide_approval(
        self,
        decision: str,
        notes: str = "",
        item_ids: Optional[List[int]] = None
    ) -> int:
        """Bulk-decide only explicitly selected pending job applications."""

        if not isinstance(decision, str):
            raise ValueError("Decision must be a string.")

        normalized_decision = decision.strip().lower()

        if normalized_decision not in ("approved", "rejected"):
            raise ValueError("Decision must be 'approved' or 'rejected'.")

        if not isinstance(notes, str):
            raise ValueError("Reviewer notes must be a string.")

        # Never allow an empty selection to mean 'all pending items'.
        if not isinstance(item_ids, list) or not item_ids:
            raise ValueError("A non-empty list of selected item IDs is required.")

        if len(item_ids) > 100:
            raise ValueError("A maximum of 100 items can be decided at once.")

        if any(
            not isinstance(item_id, int)
            or isinstance(item_id, bool)
            or item_id <= 0
            for item_id in item_ids
        ):
            raise ValueError("Every item ID must be a positive integer.")

        if len(item_ids) != len(set(item_ids)):
            raise ValueError("Duplicate item IDs are not allowed.")

        now_str = datetime.now().isoformat()
        placeholders = ",".join("?" for _ in item_ids)

        with self._get_conn() as conn:
            cursor = conn.execute(
                f"""
                UPDATE approval_queue
                SET
                    status = ?,
                    decided_at = ?,
                    reviewer_notes = ?
                WHERE id IN ({placeholders})
                AND status = 'pending'
                AND item_type = 'job_application'
                """,
                [
                    normalized_decision,
                    now_str,
                    notes.strip(),
                    *item_ids,
                ],
            )

            return cursor.rowcount


    def get_jobs_feed(
        self,
        page: int = 1,
        per_page: int = 10,
        portal: str = "all",
        sort_by: str = "score",
        search: str = ""
    ) -> Dict[str, Any]:
        """Fetch paginated, filtered, and sorted job feed matching SaaS reference UI."""
        with self._get_conn() as conn:
            where_clauses = []
            params = []
            if portal != "all":
                where_clauses.append("a.portal = ?")
                params.append(portal)
            if search:
                where_clauses.append("(a.job_title LIKE ? OR a.company LIKE ? OR a.location LIKE ?)")
                q = f"%{search}%"
                params.extend([q, q, q])

            where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

            order_sql = "ORDER BY a.relevance_score DESC, a.created_at DESC"
            if sort_by == "recent":
                order_sql = "ORDER BY a.created_at DESC"
            elif sort_by == "company":
                order_sql = "ORDER BY a.company ASC"

            total = conn.execute(f"SELECT COUNT(*) FROM applications a {where_sql}", params).fetchone()[0]
            offset = max(0, (page - 1) * per_page)

            rows = conn.execute(f"""
                SELECT a.job_hash, a.job_title, a.company, a.location, a.portal, a.status,
                       a.applied_date, a.hr_email, a.email_sent, a.relevance_score, a.salary,
                       a.experience, a.job_url, a.notes, a.created_at,
                       s.matched_skills, s.missing_skills, s.reasoning,
                       s.embedding_score, s.llm_score, s.final_score
                FROM applications a
                LEFT JOIN job_scores s ON a.job_hash = s.job_hash
                {where_sql}
                {order_sql}
                LIMIT ? OFFSET ?
            """, params + [per_page, offset]).fetchall()

            jobs = []
            for r in rows:
                item = dict(r)
                if item.get("matched_skills"):
                    try:
                        item["matched_skills"] = json.loads(item["matched_skills"])
                    except Exception:
                        item["matched_skills"] = []
                else:
                    item["matched_skills"] = []
                jobs.append(item)

            return {
                "jobs": jobs,
                "total": total,
                "page": page,
                "per_page": per_page,
                "total_pages": (total + per_page - 1) // per_page if total > 0 else 1
            }

    # =========================================================================
    # SYSTEM HARDENING & WAL MAINTENANCE (Milestone 14)
    # =========================================================================

    def checkpoint_wal(self, mode: str = "TRUNCATE") -> Dict[str, Any]:
        """
        Execute WAL checkpoint to flush transaction log pages to main database file.
        Modes: PASSIVE, FULL, RESTART, TRUNCATE.
        """
        valid_modes = ("PASSIVE", "FULL", "RESTART", "TRUNCATE")
        mode_upper = mode.upper()
        if mode_upper not in valid_modes:
            mode_upper = "TRUNCATE"

        with self._get_conn() as conn:
            row = conn.execute(f"PRAGMA wal_checkpoint({mode_upper});").fetchone()
            res = {
                "busy": bool(row[0]) if row else False,
                "log_pages": row[1] if row else 0,
                "checkpointed_pages": row[2] if row else 0,
                "mode": mode_upper,
                "status": "success" if (row and row[0] == 0) else "busy"
            }
            logger.info(f"[Database] WAL checkpoint ({mode_upper}): {res}")
            return res

    def optimize_database(self) -> Dict[str, Any]:
        """Run PRAGMA optimize and ANALYZE to refresh SQLite query planner statistics."""
        with self._get_conn() as conn:
            conn.execute("PRAGMA optimize;")
            conn.execute("ANALYZE;")
            logger.info("[Database] Executed PRAGMA optimize and ANALYZE.")
            return {"status": "optimized", "timestamp": datetime.now().isoformat()}

    def explain_query_plan(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        """Explain query plan to verify index utilization and prevent full table scans."""
        with self._get_conn() as conn:
            cursor = conn.execute(f"EXPLAIN QUERY PLAN {query}", params)
            rows = cursor.fetchall()
            return [dict(r) for r in rows]


