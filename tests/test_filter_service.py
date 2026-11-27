"""
tests/test_filter_service.py — Unit & Integration Tests for Filtering & Skills-Gap Detector (F2)
=================================================================================================
Validates:
  T1: SHA-256 URL hashing determinism and deduplication
  T2: Title & keyword blacklist filtering
  T3: Java-heavy JD rejection with Python/AI exemption
  T4: Whitelist tech & candidate target-role matching
  T5: Senior role (5+ years) seniority exclusion
  T6: Location preference filtering (Remote, WFH, Bangalore vs disallowed)
  T7: NLP Skills-Gap detector coverage logic & structured report
  T8: Full 7-stage pipeline end-to-end with statistics
  T9: Stage exception isolation (graceful fail-safe)
  T10: Live SQLite database integration (loading preferences, existing hashes, skills)
"""

import sys
import os
import hashlib
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.filter_service import (
    FilterService,
    compute_url_hash,
    stage1_url_deduplication,
    stage2_title_blacklist,
    stage3_java_heavy_rejection,
    stage4_whitelist_and_role_check,
    stage5_seniority_exclusion,
    stage6_location_preference,
    stage7_skills_gap_detector,
)

# ---------------------------------------------------------------------------
# Test Fixtures
# ---------------------------------------------------------------------------

def make_job(title, desc="Python and machine learning backend role", url=None, exp="0-2 years", loc="Remote"):
    return {
        "title": title,
        "company": "Test Innovators Inc",
        "location": loc,
        "description": desc,
        "job_url": url or f"https://example.com/jobs/{title.replace(' ', '-').lower()}",
        "portal": "remoteok",
        "experience": exp,
        "salary": "$80,000 - $110,000",
        "posted_date": "2026-11-17",
    }


AI_JOB = make_job(
    "AI/ML Engineer",
    "Build LLM-powered pipelines using Python, LangChain, LangGraph. "
    "Experience with RAG, vector databases, and Hugging Face transformers. "
    "FastAPI backend, SQLite, Docker.",
    url="https://jobs.example.com/ai-ml-engineer-001",
    loc="Remote"
)

JAVA_JOB = make_job(
    "Java Backend Developer",
    "We need a Java developer. Java Spring Boot experience required. "
    "Java microservices architecture. Java certification preferred.",
    url="https://jobs.example.com/java-dev-001",
    loc="Bangalore"
)

JAVA_AI_JOB = make_job(
    "Java + Python ML Engineer",
    "Java and Python stack. Java Spring Boot on backend. "
    "Python ML pipelines, LangChain, machine learning. Java micro-services.",
    url="https://jobs.example.com/java-ai-001",
    loc="Remote"
)

PHP_JOB = make_job(
    "PHP Developer",
    "Looking for PHP developer with Laravel and WordPress expertise.",
    url="https://jobs.example.com/php-dev-001",
    loc="Remote"
)

SENIOR_JOB = make_job(
    "Senior Machine Learning Engineer",
    "5+ years of ML experience required. Senior role in AI division. "
    "Lead cross-functional teams, mentor junior engineers.",
    url="https://jobs.example.com/senior-ml-001",
    exp="5+ years",
    loc="Remote"
)

FRESHER_ML_JOB = make_job(
    "Machine Learning Engineer",
    "0-2 years experience. Python, scikit-learn, PyTorch, ML model training. "
    "Build AI features for our SaaS platform. FastAPI REST endpoints.",
    url="https://jobs.example.com/fresher-ml-001",
    exp="0-2 years",
    loc="Bangalore"
)

DISALLOWED_LOC_JOB = make_job(
    "Python Developer",
    "Python backend engineering with FastAPI and Docker. RAG and AI pipelines.",
    url="https://jobs.example.com/tokyo-python-001",
    loc="Tokyo, Japan"
)

NO_TECH_JOB = make_job(
    "Operations Coordinator",
    "Manage office operations, schedule meetings, coordinate with HR. "
    "Strong communication skills required. No programming needed.",
    url="https://jobs.example.com/ops-coord-001",
    loc="Remote"
)

CANDIDATE_SKILLS = [
    "python", "rag", "agentic ai", "vector databases", "langchain", "langgraph",
    "hugging face", "transformers", "fastapi", "docker", "pytorch", "tensorflow",
    "machine learning", "deep learning", "nlp", "flask", "sql", "sqlite",
    "git", "api development", "rest api",
]


# ---------------------------------------------------------------------------
# Test Functions
# ---------------------------------------------------------------------------

def test_url_hashing_determinism():
    """T1: SHA-256 hash is deterministic and consistent."""
    url = "https://jobs.example.com/test-role-123"
    h1 = compute_url_hash(url)
    h2 = compute_url_hash(url)
    h3 = compute_url_hash(url + "/diff")
    assert h1 == h2, "Hash must be deterministic"
    assert h1 != h3, "Different URLs must produce different hashes"
    assert len(h1) == 64, "SHA-256 hex digest must be 64 characters"
    expected = hashlib.sha256(url.encode("utf-8")).hexdigest()
    assert h1 == expected, "Hash computation does not match hashlib standard"
    print("  ✅ test_url_hashing_determinism passed")


def test_stage1_deduplication():
    """T1b: Stage 1 correctly deduplicates known URLs."""
    existing = {compute_url_hash(AI_JOB["job_url"])}

    ok, reason = stage1_url_deduplication(AI_JOB, existing)
    assert not ok, "Known URL must be rejected"
    assert "REJECT_DUPLICATE" in reason

    new_job = make_job("New AI Role", url="https://jobs.example.com/unique-url-999")
    ok2, reason2 = stage1_url_deduplication(new_job, existing)
    assert ok2, "New unique URL must pass"
    assert "PASS_UNIQUE" in reason2

    no_url_job = dict(AI_JOB, job_url="")
    ok3, reason3 = stage1_url_deduplication(no_url_job, existing)
    assert not ok3, "Job with missing URL must be rejected"
    assert "REJECT_NO_URL" in reason3

    print("  ✅ test_stage1_deduplication passed")


def test_stage2_title_blacklist():
    """T2: Title blacklist catches irrelevant tech roles."""
    ok, reason = stage2_title_blacklist(PHP_JOB)
    assert not ok, "PHP developer must be rejected"
    assert "REJECT_BLACKLIST_TITLE" in reason

    ok2, reason2 = stage2_title_blacklist(JAVA_JOB)
    assert not ok2, "Java developer must be rejected by title blacklist"

    ok3, _ = stage2_title_blacklist(AI_JOB)
    assert ok3, "AI/ML Engineer must pass title blacklist"

    ok4, _ = stage2_title_blacklist(FRESHER_ML_JOB)
    assert ok4, "Machine Learning Engineer must pass title blacklist"

    print("  ✅ test_stage2_title_blacklist passed")


def test_stage3_java_rejection_with_exemption():
    """T3: Java-heavy JDs are rejected unless Python/AI ecosystem is present."""
    ok, reason = stage3_java_heavy_rejection(JAVA_JOB)
    assert not ok, "Pure Java JD must be rejected"
    assert "REJECT_JAVA_HEAVY" in reason

    ok2, reason2 = stage3_java_heavy_rejection(JAVA_AI_JOB)
    assert ok2, f"Java + Python/AI JD must pass exemption check, got: {reason2}"

    ok3, _ = stage3_java_heavy_rejection(AI_JOB)
    assert ok3, "Pure AI job must pass Java check"

    print("  ✅ test_stage3_java_rejection_with_exemption passed")


def test_stage4_whitelist_and_role_check():
    """T4: Whitelist check verifies candidate target roles and tech stack."""
    ok, reason = stage4_whitelist_and_role_check(NO_TECH_JOB)
    assert not ok, "Non-technical job must be rejected"
    assert "REJECT_NO_MATCH" in reason

    ok2, reason2 = stage4_whitelist_and_role_check(AI_JOB)
    assert ok2, f"AI job must pass whitelist check: {reason2}"

    ok3, _ = stage4_whitelist_and_role_check(FRESHER_ML_JOB)
    assert ok3, "ML job must pass whitelist check"

    print("  ✅ test_stage4_whitelist_and_role_check passed")


def test_stage5_seniority_exclusion():
    """T5: Excludes 5+ years experience and senior/lead positions."""
    ok, reason = stage5_seniority_exclusion(SENIOR_JOB)
    assert not ok, "Senior role must be excluded"
    assert "REJECT_SENIOR" in reason

    ok2, _ = stage5_seniority_exclusion(FRESHER_ML_JOB)
    assert ok2, "0-2 years fresher ML job must pass seniority check"

    sneaky = make_job(
        "AI Engineer",
        "Lead our team. 7+ years of industry experience required. PyTorch, Python.",
        url="https://jobs.example.com/lead-ai-7years"
    )
    ok3, reason3 = stage5_seniority_exclusion(sneaky)
    assert not ok3, f"7+ years requirement in JD must be rejected, got: {reason3}"

    print("  ✅ test_stage5_seniority_exclusion passed")


def test_stage6_location_preference():
    """T6: Location filtering matches preferred tech hubs and Remote."""
    ok, reason = stage6_location_preference(AI_JOB)
    assert ok, f"Remote job must pass location check: {reason}"

    ok2, reason2 = stage6_location_preference(FRESHER_ML_JOB)
    assert ok2, f"Bangalore job must pass location check: {reason2}"

    ok3, reason3 = stage6_location_preference(DISALLOWED_LOC_JOB, preferred_locations=["Remote", "India", "Bangalore", "Pune"])
    assert not ok3, f"Tokyo job should be rejected when not in preferred locations, got: {reason3}"
    assert "REJECT_LOCATION" in reason3

    print("  ✅ test_stage6_location_preference passed")


def test_stage7_skills_gap_detector():
    """T7: NLP-based Skills-Gap detector measures candidate coverage accurately."""
    ok, reason = stage7_skills_gap_detector(AI_JOB, CANDIDATE_SKILLS)
    assert ok, f"AI job should pass skills gap check: {reason}"
    assert "PASS_SKILLS_GAP" in reason

    fs = FilterService(db_manager=None)
    report = fs.get_skills_gap_report(AI_JOB)
    assert "job_title" in report
    assert "matched_skills" in report
    assert "missing_skills" in report
    assert "coverage_pct" in report
    assert isinstance(report["coverage_pct"], float)
    assert report["passes_threshold"] is True

    print("  ✅ test_stage7_skills_gap_detector passed")


def test_full_pipeline_end_to_end():
    """T8: End-to-end 7-stage filter pipeline execution with statistics."""
    fs = FilterService(db_manager=None)

    # Seed AI_JOB URL as duplicate
    fs._existing_hashes = {compute_url_hash(AI_JOB["job_url"])}

    raw_jobs = [
        AI_JOB,              # S1: Duplicate URL
        PHP_JOB,             # S2: Blacklisted title
        JAVA_JOB,            # S2: Blacklisted title
        NO_TECH_JOB,         # S4: No tech/role match
        SENIOR_JOB,          # S5: Seniority (5+ years)
        DISALLOWED_LOC_JOB,  # S6: Location mismatch (Tokyo)
        FRESHER_ML_JOB,      # PASS ALL
        make_job(            # PASS ALL
            "Python Backend Developer",
            "Build high-throughput REST APIs using FastAPI, SQLite, and Python. "
            "RAG pipelines with LangChain and vector databases.",
            url="https://jobs.example.com/unique-python-be-404",
            loc="Remote"
        ),
    ]

    passed, rejected = fs.run_pipeline(raw_jobs)

    assert len(passed) == 2, f"Expected exactly 2 passed jobs, got {len(passed)}: {[j['title'] for j in passed]}"
    assert len(rejected) == 6, f"Expected 6 rejected jobs, got {len(rejected)}"

    # Validate that passed jobs have structured skills_gap_report attached
    for pj in passed:
        assert "skills_gap_report" in pj, f"Passed job '{pj['title']}' missing skills_gap_report"
        assert pj["skills_gap_report"]["coverage_pct"] >= 25.0

    stats = fs.get_stats()
    assert stats["total_input"] == len(raw_jobs)
    assert stats["passed"] == 2
    assert sum(stats[f"rejected_stage{i}"] for i in range(1, 8)) == 6

    print("  ✅ test_full_pipeline_end_to_end passed")


def test_stage_exception_isolation():
    """T9: An unhandled exception in one stage fails-safe without crashing pipeline."""
    fs = FilterService(db_manager=None)

    malformed_job = {
        "title": "Python AI Developer",
        "company": "Resilient AI",
        "location": "Remote",
        "description": None,  # None instead of string
        "job_url": "https://jobs.example.com/malformed-resilient-101",
        "portal": "test",
        "experience": None,
        "salary": None,
        "posted_date": None,
    }

    passed, rejected = fs.run_pipeline([malformed_job])
    assert (len(passed) + len(rejected)) == 1, "Malformed job was dropped unexpectedly"
    print("  ✅ test_stage_exception_isolation passed")


def test_live_database_integration():
    """T10: Verify loading candidate preferences, skills, and existing application hashes from SQLite."""
    db = DatabaseManager(cfg.DB_PATH)
    fs = FilterService(db_manager=db)

    fs._load_data_from_db()

    # Verify existing hashes loaded from applications table
    assert isinstance(fs._existing_hashes, set), "Existing hashes must be a set"

    # Verify candidate skills loaded from candidate_skills table
    assert len(fs._candidate_skills) >= 10, f"Expected >=10 candidate skills, got {len(fs._candidate_skills)}"
    assert "FastAPI" in fs._candidate_skills or "fastapi" in [s.lower() for s in fs._candidate_skills]
    assert "LangChain" in fs._candidate_skills or "langchain" in [s.lower() for s in fs._candidate_skills]

    # Verify preferences loaded from candidate_preferences
    assert len(fs._target_roles) > 0, "Target roles should be populated from candidate_preferences"
    assert len(fs._preferred_locations) > 0, "Preferred locations should be populated"

    print("  ✅ test_live_database_integration passed")


# ---------------------------------------------------------------------------
# Test Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\nRunning Milestone 5 Filtering & Skills-Gap Detector Tests...\n")

    tests = [
        test_url_hashing_determinism,
        test_stage1_deduplication,
        test_stage2_title_blacklist,
        test_stage3_java_rejection_with_exemption,
        test_stage4_whitelist_and_role_check,
        test_stage5_seniority_exclusion,
        test_stage6_location_preference,
        test_stage7_skills_gap_detector,
        test_full_pipeline_end_to_end,
        test_stage_exception_isolation,
        test_live_database_integration,
    ]

    passed_count = 0
    failed_count = 0

    for test_fn in tests:
        try:
            test_fn()
            passed_count += 1
        except AssertionError as e:
            print(f"  ❌ {test_fn.__name__} FAILED: {e}")
            failed_count += 1
        except Exception as e:
            print(f"  💥 {test_fn.__name__} ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1

    print(f"\n{'='*68}")
    if failed_count == 0:
        print(f"🎉 ALL {passed_count} FILTERING & SKILLS-GAP TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
