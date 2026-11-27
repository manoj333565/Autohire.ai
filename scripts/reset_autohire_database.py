"""
scripts/reset_autohire_database.py — Autonomous Database Reset & Sanitization Script
===================================================================================
Completely decouples Autohire.ai from reference historical records while preserving
all 15 database table definitions, foreign keys, triggers, constraints, and WAL mode.

Features:
- Automatic timestamped backup before any reset action (e.g. database/tracker_backup_YYYYMMDD_HHMMSS.db)
- Mandatory confirmation gate (--confirm flag or interactive 'yes')
- Safe deletion of historical applications, outreach, contacts, stats, queues, and runs
- SQLite auto-increment sequence resets (sqlite_sequence)
- Re-seeding of candidate_profile, candidate_skills, and candidate_preferences strictly
  from data/user_profile.json for Manoj Kumar Mandal
- Full post-reset integrity check and verification reporting
"""

import os
import sys
import shutil
import sqlite3
import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("Autohire.DBReset")

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import config as cfg

TABLES_TO_TRUNCATE = [
    "applications",
    "approval_queue",
    "email_history",
    "pdf_hr_outreach",
    "hr_contacts",
    "agent_runs",
    "daily_stats",
    "job_scores",
    "memory_store",
    "api_cache",
    "tavily_usage",
    "profile_history",
]


def backup_database(db_path: Path, backup_dir: Path) -> Path:
    """Create a verified copy of the existing SQLite database before modification."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = backup_dir / f"tracker_backup_{timestamp}.db"
    
    logger.info(f"Creating pre-reset backup: {backup_file}")
    # Use SQLite VACUUM INTO or sqlite3 backup API for rock-solid transaction-safe backup
    src_conn = sqlite3.connect(str(db_path))
    dst_conn = sqlite3.connect(str(backup_file))
    src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    
    if not backup_file.exists() or backup_file.stat().st_size == 0:
        raise RuntimeError(f"Database backup failed or generated empty file: {backup_file}")
    
    logger.info(f"Backup verified ({backup_file.stat().st_size:,} bytes).")
    return backup_file


def reseed_candidate_profile(conn: sqlite3.Connection):
    """Cleanly re-seed candidate_profile, candidate_skills, and candidate_preferences from user_profile.json."""
    logger.info("Re-seeding candidate profile for Manoj Kumar Mandal...")
    
    # Clear existing profile records
    conn.execute("DELETE FROM candidate_skills;")
    conn.execute("DELETE FROM candidate_preferences;")
    conn.execute("DELETE FROM candidate_profile;")
    
    profile_path = ROOT_DIR / "data" / "user_profile.json"
    if not profile_path.exists():
        raise FileNotFoundError(f"Candidate profile JSON not found at {profile_path}")
    
    with open(profile_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    personal = data.get("personal", {})
    education = data.get("education", {})
    verified_skills = data.get("verified_skills", {})
    resume_qualifications = data.get("resume_qualifications", {})
    
    # 1. Insert candidate_profile
    cursor = conn.execute("""
        INSERT INTO candidate_profile (
            id, name, email, phone, location, education_degree,
            education_college, education_year, education_cgpa,
            linkedin_url, github_url, portfolio_url, resume_path, summary
        ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        personal.get("name", "Manoj Kumar Mandal"),
        personal.get("email", "manojkumarmandal1111@gmail.com"),
        personal.get("phone", "+91-8404970494"),
        personal.get("location", "India"),
        education.get("degree", "B.Tech Computer Science and Engineering"),
        education.get("college", "Satya Sai Engineering College Balasore, Odisha"),
        str(education.get("year", "2026")),
        education.get("cgpa", "8.01/10"),
        personal.get("linkedin_url", "https://www.linkedin.com/in/manoj-kumar-59668527a/"),
        personal.get("github_url", "https://github.com/manoj333565"),
        personal.get("portfolio_url", ""),
        personal.get("resume_path", "data/resume.pdf"),
        "AI/ML & Agentic AI Engineer with core strengths in Python, NLP, Computer Vision, and autonomous agent systems."
    ))
    profile_id = 1
    
    # 2. Insert verified skills with exact proficiency tiers
    skill_count = 0
    for proficiency_tier, skills in verified_skills.items():
        level = proficiency_tier.capitalize()
        if level in ("Proficient", "Intermediate", "Learning"):
            for s in skills:
                conn.execute("""
                    INSERT INTO candidate_skills (
                        profile_id, skill_name, category, proficiency, verified, source
                    ) VALUES (?, ?, ?, ?, 1, 'verified_declaration')
                """, (profile_id, s, "AI/ML", level))
                skill_count += 1
    
    # Also add resume qualification skills if not already added
    for category, skills in resume_qualifications.items():
        cat_name = category.replace("_", " ").title()
        for s in skills:
            # Check if skill already exists
            existing = conn.execute(
                "SELECT id FROM candidate_skills WHERE profile_id = ? AND LOWER(skill_name) = LOWER(?)",
                (profile_id, s)
            ).fetchone()
            if not existing:
                conn.execute("""
                    INSERT INTO candidate_skills (
                        profile_id, skill_name, category, proficiency, verified, source
                    ) VALUES (?, ?, ?, 'Intermediate', 1, 'resume_extracted')
                """, (profile_id, s, cat_name))
                skill_count += 1
    
    # 3. Insert candidate_preferences
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
        json.dumps(data.get("preferred_locations", ["Remote", "India"])),
        float(data.get("matching_threshold", 7.0)),
        float(data.get("min_salary_lpa", 3.6)),
        1 if data.get("allow_remote", True) else 0,
        1 if data.get("allow_internships", True) else 0,
        15,
        25
    ))
    
    logger.info(f"Candidate profile initialized: Manoj Kumar Mandal with {skill_count} skills.")


def reset_database(db_path: Path, confirm: bool = False, dry_run: bool = False) -> dict:
    """Execute complete database sanitization."""
    if not db_path.exists():
        logger.warning(f"Database at {db_path} does not exist. Initializing fresh...")
        from database.db_manager import DatabaseManager
        db = DatabaseManager(str(db_path))
        return {"status": "initialized_new"}
    
    if not confirm and not dry_run:
        print("\n" + "!" * 70)
        print("WARNING: This will permanently purge all historical records from tracker.db:")
        for t in TABLES_TO_TRUNCATE:
            print(f"  - {t}")
        print("Candidate profile will be cleanly re-seeded for Manoj Kumar Mandal.")
        print("!" * 70)
        user_input = input("\nType 'yes' to proceed with backup and reset: ").strip().lower()
        if user_input != "yes":
            logger.info("Reset cancelled by user.")
            return {"status": "cancelled"}
    
    # 1. Back up database
    backup_file = backup_database(db_path, db_path.parent / "backups")
    
    if dry_run:
        logger.info("[DRY RUN] Simulating database reset — no records will be deleted.")
        conn = sqlite3.connect(str(db_path))
        counts = {}
        for t in TABLES_TO_TRUNCATE:
            try:
                cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                counts[t] = cnt
            except Exception:
                counts[t] = "N/A"
        conn.close()
        logger.info(f"[DRY RUN] Current record counts: {counts}")
        return {"status": "dry_run_complete", "counts": counts, "backup": str(backup_file)}
    
    # 2. Reset records within transaction
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA foreign_keys = OFF;")  # temporarily disable FKs during clean truncation
    
    deleted_counts = {}
    try:
        with conn:
            for t in TABLES_TO_TRUNCATE:
                try:
                    cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    conn.execute(f'DELETE FROM "{t}";')
                    deleted_counts[t] = cnt
                    logger.info(f"Truncated table '{t}' ({cnt} records removed).")
                except sqlite3.OperationalError as e:
                    logger.warning(f"Table '{t}' not present or failed to truncate: {e}")
                    deleted_counts[t] = 0
            
            # Reset sqlite_sequence for truncated tables
            for t in TABLES_TO_TRUNCATE + ["candidate_profile", "candidate_skills", "candidate_preferences"]:
                try:
                    conn.execute("DELETE FROM sqlite_sequence WHERE name = ?;", (t,))
                except sqlite3.OperationalError:
                    pass
            
            # Re-seed candidate profile
            reseed_candidate_profile(conn)
            
            # Run integrity check
            integrity = conn.execute("PRAGMA integrity_check;").fetchone()[0]
            logger.info(f"SQLite PRAGMA integrity_check: {integrity}")
            if integrity != "ok":
                raise RuntimeError(f"Integrity check failed: {integrity}")
            
    finally:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.close()
    
    # 3. Optimize & reclaim space
    logger.info("Executing VACUUM to reclaim space and optimize WAL...")
    vac_conn = sqlite3.connect(str(db_path), timeout=30.0)
    vac_conn.execute("PRAGMA journal_mode = WAL;")
    vac_conn.execute("VACUUM;")
    vac_conn.close()
    
    logger.info("=== DATABASE RESET COMPLETE ===")
    return {
        "status": "success",
        "backup_path": str(backup_file),
        "deleted_counts": deleted_counts
    }


def main():
    parser = argparse.ArgumentParser(description="Autohire.ai Database Reset & Sanitization Utility")
    parser.add_argument("--confirm", action="store_true", help="Bypass interactive confirmation")
    parser.add_argument("--dry-run", action="store_true", help="Simulate reset without altering data")
    parser.add_argument("--db-path", type=str, default=str(cfg.DB_PATH), help="Path to tracker.db")
    args = parser.parse_args()

    db_path = Path(args.db_path)
    result = reset_database(db_path, confirm=args.confirm, dry_run=args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
