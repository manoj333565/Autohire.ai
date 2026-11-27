"""
tests/test_foundation.py — Milestone 1 Automated Foundation & Health Tests
Validates configuration loading, candidate profile integrity, database preservation,
logging, and safety switches.
"""
try:
    import pytest
except ImportError:
    pytest = None
import sys
import sqlite3
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

try:
    import PyPDF2 as pypdf
except ImportError:
    import pypdf

import config as cfg
from utils.logger import setup_logger


def test_config_loading():
    """Verify config loads correctly and resolves paths."""
    assert cfg.BASE_DIR.exists()
    assert cfg.DB_PATH != ""
    assert cfg.PROFILE_PATH != ""
    assert cfg.RESUME_PATH != ""
    assert cfg.CANDIDATE_NAME == "Manoj Kumar Mandal"
    assert cfg.PERSONAL_EMAIL == "manojkumarmandal1111@gmail.com"


def test_safety_switches():
    """Verify that default execution modes are strictly safe."""
    assert cfg.DRY_RUN is True, "DRY_RUN must default to True for user safety!"
    assert cfg.REQUIRE_HUMAN_APPROVAL is True, "REQUIRE_HUMAN_APPROVAL must default to True!"


def test_candidate_profile_integrity():
    """Verify Manoj Kumar Mandal's confirmed profile and distinct verified skills."""
    profile = cfg.load_user_profile(force_reload=True)
    assert profile["personal"]["name"] == "Manoj Kumar Mandal"
    assert profile["personal"]["email"] == "manojkumarmandal1111@gmail.com"
    assert len(profile["target_roles"]) >= 8

    # Verified Skills Check
    verified = profile.get("verified_skills", {})
    assert "RAG" in verified.get("proficient", [])
    assert "Agentic AI" in verified.get("proficient", [])
    assert "Vector Databases" in verified.get("proficient", [])

    assert "LangChain" in verified.get("intermediate", [])
    assert "LangGraph" in verified.get("intermediate", [])
    assert "FastAPI" in verified.get("intermediate", [])

    assert "Docker" in verified.get("learning", [])
    assert "PyTorch" in verified.get("learning", [])
    assert "TensorFlow" in verified.get("learning", [])


def test_resume_pdf_readability():
    """Verify the candidate resume PDF exists and is readable."""
    resume_path = Path(cfg.RESUME_PATH)
    assert resume_path.exists(), f"Resume not found at {resume_path}"
    reader = pypdf.PdfReader(str(resume_path))
    assert len(reader.pages) > 0
    text = reader.pages[0].extract_text()
    assert "manoj kumar mandal" in text.lower()


def test_database_backup_and_records():
    """Verify SQLite database exists, candidate profile is present, and reference backup is preserved."""
    db_path = Path(cfg.DB_PATH)
    backup_path = Path(cfg.BACKUP_DB_PATH)

    assert db_path.exists(), "Working database does not exist"
    assert backup_path.exists(), "Backup reference database does not exist"

    # Working database check
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM candidate_profile;")
    prof_count = cur.fetchone()[0]
    assert prof_count == 1, f"Expected 1 candidate profile, found {prof_count}"
    conn.close()

    # Reference backup archive check
    b_conn = sqlite3.connect(str(backup_path))
    b_cur = b_conn.cursor()
    b_cur.execute("SELECT COUNT(*) FROM applications;")
    app_count = b_cur.fetchone()[0]
    assert app_count >= 2500, f"Expected at least 2500 preserved applications in backup, found {app_count}"
    b_conn.close()


def test_logger_functionality(tmp_path):
    """Verify logger configuration and file output."""
    test_logger = setup_logger("TestLogger")
    assert test_logger is not None
    test_logger.info("Test logging message for Milestone 1 verification")

    today_logs = list(Path("logs").glob("careerpilot_*.log"))
    assert len(today_logs) > 0, "Daily log file was not created in logs directory"


if __name__ == "__main__":
    print("Running Milestone 1 Foundation Health Tests...")
    test_config_loading()
    print("  ✅ test_config_loading passed")
    test_safety_switches()
    print("  ✅ test_safety_switches passed")
    test_candidate_profile_integrity()
    print("  ✅ test_candidate_profile_integrity passed")
    test_resume_pdf_readability()
    print("  ✅ test_resume_pdf_readability passed")
    test_database_backup_and_records()
    print("  ✅ test_database_backup_and_records passed")
    test_logger_functionality(None)
    print("  ✅ test_logger_functionality passed")
    print("\n🎉 ALL MILESTONE 1 TESTS PASSED SUCCESSFULLY!")

