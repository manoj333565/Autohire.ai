"""
tests/test_scoring_service.py — Unit & Integration Tests for Job Scoring Engine (F3)
=====================================================================================
Validates:
  T1: Heuristic fallback scoring with weighted proficiency tiers (1.0 vs 0.7 vs 0.3)
  T2: Strict structured schema compliance on all scored jobs
  T3: Experience and location penalties
  T4: Prompt construction and candidate context formatting
  T5: JSON markdown fence extraction
  T6: Batch scoring, ranking order (descending by relevance_score)
  T7: SQLite persistence in `job_scores` and `applications` tables
  T8: Simulated Gemini batch response parsing and mapping
"""

import sys
import os
import uuid
import json
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService
from services.scoring_service import ScoringService, compute_url_hash


def make_test_job(title: str, desc: str, loc: str = "Remote", exp: str = "0-2 years") -> dict:
    unique_id = uuid.uuid4().hex[:8]
    url = f"https://example.com/jobs/test-{unique_id}"
    return {
        "title": title,
        "company": f"Company-{unique_id}",
        "location": loc,
        "description": desc,
        "job_url": url,
        "url": url,
        "portal": "remoteok",
        "experience": exp,
        "salary": "$90,000",
        "posted_date": "2026-11-17"
    }


def test_heuristic_proficiency_weights():
    """T1: Verify that Proficient skills (RAG, Agentic AI) produce higher scores than Learning skills."""
    service = ScoringService(mock_mode=True)

    # Job heavily requiring candidate's Proficient skills (RAG, Agentic AI, Vector Databases)
    proficient_job = make_test_job(
        "Agentic AI & RAG Engineer",
        "Building autonomous agents with RAG, Vector Databases, and Agentic AI workflows. Python backend."
    )

    # Job requiring candidate's Learning skills only (Docker)
    learning_job = make_test_job(
        "Docker Systems Assistant",
        "Maintain Docker containers and basic deployment pipelines. Minimal AI work."
    )

    scored_proficient = service.score_single_job(proficient_job)
    scored_learning = service.score_single_job(learning_job)

    assert scored_proficient["relevance_score"] > scored_learning["relevance_score"], (
        f"Proficient job score ({scored_proficient['relevance_score']}) must exceed "
        f"Learning job score ({scored_learning['relevance_score']})"
    )
    assert scored_proficient["relevance_score"] >= 7.0, "Strong fit should score >= 7.0"
    print(f"  ✅ test_heuristic_proficiency_weights passed (Proficient: {scored_proficient['relevance_score']} vs Learning: {scored_learning['relevance_score']})")


def test_scoring_schema_compliance():
    """T2: Verify that scored job objects adhere strictly to the assignment schema."""
    service = ScoringService(mock_mode=True)
    job = make_test_job(
        "Generative AI Developer",
        "Develop LangChain and FastAPI microservices with vector search."
    )

    scored = service.score_single_job(job)

    required_keys = [
        "job_hash",
        "relevance_score",
        "skills_match_ratio",
        "experience_match",
        "location_match",
        "matched_skills",
        "missing_skills",
        "reasoning",
        "raw_gemini_response"
    ]

    for key in required_keys:
        assert key in scored, f"Missing required scoring key '{key}' in scored job"

    assert isinstance(scored["relevance_score"], (int, float))
    assert 0.0 <= scored["relevance_score"] <= 10.0
    assert isinstance(scored["skills_match_ratio"], float)
    assert 0.0 <= scored["skills_match_ratio"] <= 1.0
    assert isinstance(scored["experience_match"], bool)
    assert isinstance(scored["location_match"], bool)
    assert isinstance(scored["matched_skills"], list)
    assert isinstance(scored["missing_skills"], list)
    assert isinstance(scored["reasoning"], str) and len(scored["reasoning"]) > 0

    print("  ✅ test_scoring_schema_compliance passed")


def test_experience_and_location_penalties():
    """T3: Verify penalties for experience mismatches (3+ years) and foreign locations."""
    service = ScoringService(mock_mode=True)

    base_job = make_test_job(
        "Python Developer",
        "Python FastAPI backend with RAG workflows.",
        loc="Remote",
        exp="0-2 years"
    )

    senior_job = make_test_job(
        "Python Developer",
        "Python FastAPI backend with RAG workflows. 4+ years industry experience required.",
        loc="Remote",
        exp="4+ years"
    )

    tokyo_job = make_test_job(
        "Python Developer",
        "Python FastAPI backend with RAG workflows.",
        loc="Tokyo, Japan",
        exp="0-2 years"
    )

    base_scored = service.score_single_job(base_job)
    senior_scored = service.score_single_job(senior_job)
    tokyo_scored = service.score_single_job(tokyo_job)

    assert not senior_scored["experience_match"], "Senior job must flag experience_match=False"
    assert senior_scored["relevance_score"] < base_scored["relevance_score"], "Experience penalty not applied"

    assert not tokyo_scored["location_match"], "Tokyo job must flag location_match=False"
    assert tokyo_scored["relevance_score"] < base_scored["relevance_score"], "Location penalty not applied"

    print("  ✅ test_experience_and_location_penalties passed")


def test_prompt_construction():
    """T4: Verify Gemini prompt construction contains candidate details and skill tiers."""
    service = ScoringService(mock_mode=True)
    job = make_test_job("AI Engineer", "Building LangGraph agents and FastAPI services.")

    ctx = service._get_candidate_context()
    prompt = service._build_gemini_prompt([job], ctx)

    assert "Manoj Kumar Mandal" in prompt
    assert "Proficient" in prompt
    assert "Intermediate" in prompt
    assert "Learning" in prompt
    assert "relevance_score" in prompt
    assert "skills_match_ratio" in prompt

    print("  ✅ test_prompt_construction passed")


def test_json_markdown_fence_cleaning():
    """T5: Verify cleaning of code fences from LLM responses."""
    service = ScoringService(mock_mode=True)

    raw_with_fences = """```json
[
  {
    "job_id": 0,
    "relevance_score": 9.2
  }
]
```"""
    cleaned = service._extract_json_string(raw_with_fences)
    parsed = json.loads(cleaned)
    assert isinstance(parsed, list)
    assert parsed[0]["relevance_score"] == 9.2

    print("  ✅ test_json_markdown_fence_cleaning passed")


def test_batch_scoring_and_ranking():
    """T6: Verify batch scoring ranks jobs in descending order by relevance_score."""
    service = ScoringService(mock_mode=True)

    jobs = [
        make_test_job("Basic Support Specialist", "Customer inquiries and spreadsheet entry."),
        make_test_job("RAG & Agentic AI Specialist", "RAG, Vector Databases, Agentic AI with Python and FastAPI."),
        make_test_job("Junior Python Developer", "General Python scripting, SQLite, and REST APIs."),
    ]

    ranked = service.score_jobs(jobs, persist_to_db=False)

    assert len(ranked) == 3
    # Check descending order
    scores = [j["relevance_score"] for j in ranked]
    assert scores == sorted(scores, reverse=True), f"Jobs not sorted descending: {scores}"
    assert "Agentic AI" in ranked[0]["title"], "Top-ranked job should be the Agentic AI role"

    print(f"  ✅ test_batch_scoring_and_ranking passed (Ranked scores: {scores})")


def test_sqlite_persistence():
    """T7: Verify saving scores to SQLite job_scores table and updating applications.relevance_score."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ScoringService(db_manager=db, mock_mode=True)

    # First, insert a test application so it can be updated
    unique_id = uuid.uuid4().hex[:8]
    test_url = f"https://example.com/apply/{unique_id}"
    expected_hash = compute_url_hash(test_url)
    db.save_application({
        "job_hash": expected_hash,
        "job_url": test_url,
        "job_title": "AI Research Associate",
        "company": "DeepResearch Labs",
        "location": "Remote",
        "portal": "remoteok",
        "job_description": "Work on LangChain, RAG, and FastAPI.",
        "status": "pending",
        "relevance_score": 0.0
    })

    job = {
        "title": "AI Research Associate",
        "company": "DeepResearch Labs",
        "location": "Remote",
        "description": "Work on LangChain, RAG, and FastAPI.",
        "job_url": test_url,
        "url": test_url,
        "portal": "remoteok",
        "experience": "0-1 year",
        "salary": "$85,000",
        "posted_date": "2026-11-17"
    }

    scored_jobs = service.score_jobs([job], persist_to_db=True)
    assert len(scored_jobs) == 1

    # Verify score was saved in job_scores table
    conn = db._get_conn()
    score_row = conn.execute("SELECT * FROM job_scores WHERE job_hash = ?", (expected_hash,)).fetchone()
    assert score_row is not None, "Score record missing in job_scores table"
    assert score_row["relevance_score"] > 0.0
    assert score_row["relevance_score"] == scored_jobs[0]["relevance_score"]

    # Verify applications table relevance_score was updated
    app_row = conn.execute("SELECT relevance_score FROM applications WHERE job_hash = ?", (expected_hash,)).fetchone()
    assert app_row is not None
    assert app_row["relevance_score"] == score_row["relevance_score"]

    print("  ✅ test_sqlite_persistence passed")


def test_simulated_gemini_batch_mapping():
    """T8: Verify batch parsing and property mapping with simulated Gemini response."""
    service = ScoringService(mock_mode=True)

    job1 = make_test_job("AI Engineer", "RAG and Vector DBs")
    job2 = make_test_job("Backend Python", "FastAPI microservices")

    simulated_gemini_json = json.dumps([
        {
            "job_id": 0,
            "relevance_score": 9.4,
            "skills_match_ratio": 0.9,
            "experience_match": True,
            "location_match": True,
            "matched_skills": ["RAG", "Vector Databases", "Python"],
            "missing_skills": ["Docker"],
            "reasoning": "Direct match with proficient candidate skills."
        },
        {
            "job_id": 1,
            "relevance_score": 8.1,
            "skills_match_ratio": 0.75,
            "experience_match": True,
            "location_match": True,
            "matched_skills": ["FastAPI", "Python"],
            "missing_skills": ["AWS"],
            "reasoning": "Good intermediate stack match."
        }
    ])

    # Test the JSON extraction and mapping logic
    score_entries = json.loads(service._extract_json_string(simulated_gemini_json))
    score_map = {item["job_id"]: item for item in score_entries}

    assert score_map[0]["relevance_score"] == 9.4
    assert score_map[1]["relevance_score"] == 8.1
    assert "RAG" in score_map[0]["matched_skills"]

    print("  ✅ test_simulated_gemini_batch_mapping passed")


if __name__ == "__main__":
    print("\nRunning Milestone 6 Job Scoring Engine (F3) Tests...\n")

    tests = [
        test_heuristic_proficiency_weights,
        test_scoring_schema_compliance,
        test_experience_and_location_penalties,
        test_prompt_construction,
        test_json_markdown_fence_cleaning,
        test_batch_scoring_and_ranking,
        test_sqlite_persistence,
        test_simulated_gemini_batch_mapping,
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
        print(f"🎉 ALL {passed_count} JOB SCORING TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
