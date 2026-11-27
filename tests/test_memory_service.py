"""
tests/test_memory_service.py — Unit & Integration Tests for Cross-Session Memory (Milestone 10)
================================================================================================
Validates:
  T1: Memory persistence and retrieval (remember & recall) in SQLite
  T2: Category-based grouping and retrieval
  T3: Semantic keyword search with relevance ranking
  T4: Company interaction history tracking (applications, outreach, outcomes)
  T5: Local JSON cache file synchronization (`data/mem0_cache.json`)
  T6: System context synthesis for agent session initialization
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
from services.memory_service import MemoryService


def test_memory_persistence_and_recall():
    """T1: Verify storing and retrieving contextual memory in SQLite."""
    db = DatabaseManager(cfg.DB_PATH)
    service = MemoryService(db_manager=db)

    unique_key = f"candidate_pref_{uuid.uuid4().hex[:8]}"
    content = "Candidate prefers remote AI/ML engineering roles with FastAPI and LangGraph."
    metadata = {"salary_min": "4 LPA", "remote_only": True}

    ok = service.remember(
        key=unique_key,
        content=content,
        category="candidate_insights",
        metadata=metadata
    )
    assert ok is True

    # Recall
    recalled = service.recall(unique_key)
    assert recalled is not None
    assert recalled["content"] == content
    assert recalled["category"] == "candidate_insights"
    assert recalled["metadata"]["salary_min"] == "4 LPA"

    print("  ✅ test_memory_persistence_and_recall passed")


def test_category_retrieval():
    """T2: Verify retrieving all memories under a specific category."""
    db = DatabaseManager(cfg.DB_PATH)
    service = MemoryService(db_manager=db)

    cat_name = f"test_cat_{uuid.uuid4().hex[:6]}"
    service.remember(f"{cat_name}_1", "First insight", category=cat_name)
    service.remember(f"{cat_name}_2", "Second insight", category=cat_name)

    items = service.recall_by_category(cat_name)
    assert len(items) == 2
    contents = [i["content"] for i in items]
    assert "First insight" in contents
    assert "Second insight" in contents

    print("  ✅ test_category_retrieval passed")


def test_semantic_keyword_search():
    """T3: Verify semantic keyword search and relevance ranking."""
    db = DatabaseManager(cfg.DB_PATH)
    service = MemoryService(db_manager=db)

    unique_id = uuid.uuid4().hex[:6]
    service.remember(
        f"rag_insight_{unique_id}",
        "High match for RAG pipelines and vector database indexing with Milvus and Chroma.",
        category="candidate_insights"
    )
    service.remember(
        f"docker_insight_{unique_id}",
        "Learning level skill Docker containerization for simple deployments.",
        category="candidate_insights"
    )

    # Search for RAG
    results = service.search_memory("RAG vector database", limit=3)
    assert len(results) > 0
    top_result = results[0]
    assert "RAG" in top_result["content"]
    assert "vector database" in top_result["content"]

    print("  ✅ test_semantic_keyword_search passed")


def test_company_interaction_tracking():
    """T4: Verify tracking and recalling company interactions."""
    db = DatabaseManager(cfg.DB_PATH)
    service = MemoryService(db_manager=db)

    test_company = f"ScaleAI-{uuid.uuid4().hex[:6]}"

    # Record first interaction
    service.record_company_interaction(
        company=test_company,
        action="applied",
        outcome="in_review",
        notes="Applied via RemoteOK direct link."
    )

    # Record second interaction
    service.record_company_interaction(
        company=test_company,
        action="recruiter_email",
        outcome="interview_requested",
        notes="HR scheduled a technical screening."
    )

    # Retrieve interaction history
    history_record = service.get_company_interaction(test_company)
    assert history_record is not None
    assert history_record["metadata"]["last_outcome"] == "interview_requested"
    assert len(history_record["metadata"]["history"]) == 2

    print("  ✅ test_company_interaction_tracking passed")


def test_local_json_cache_synchronization():
    """T5: Verify local JSON cache synchronization."""
    db = DatabaseManager(cfg.DB_PATH)
    service = MemoryService(db_manager=db)

    cache_file = Path(service.cache_path)
    assert cache_file.exists(), "Local cache file must exist"

    # Store a memory
    key = f"cache_sync_test_{uuid.uuid4().hex[:6]}"
    service.remember(key, "Testing local JSON cache write", category="test_cache")

    # Read directly from cache file on disk
    with open(cache_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert "entries" in data
    assert key in data["entries"]
    assert data["entries"][key]["content"] == "Testing local JSON cache write"

    print("  ✅ test_local_json_cache_synchronization passed")


def test_system_context_synthesis():
    """T6: Verify system context aggregation for agent runs."""
    db = DatabaseManager(cfg.DB_PATH)
    service = MemoryService(db_manager=db)

    context = service.get_system_context()

    assert "all_time_stats" in context
    assert "today_applied" in context
    assert "daily_quota_remaining" in context
    assert isinstance(context["daily_quota_remaining"], int)
    assert "cached_memory_keys" in context

    print(f"  ✅ test_system_context_synthesis passed (Quota Remaining: {context['daily_quota_remaining']})")


if __name__ == "__main__":
    print("\nRunning Milestone 10 Cross-Session Memory Tests...\n")

    tests = [
        test_memory_persistence_and_recall,
        test_category_retrieval,
        test_semantic_keyword_search,
        test_company_interaction_tracking,
        test_local_json_cache_synchronization,
        test_system_context_synthesis,
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
        print(f"🎉 ALL {passed_count} CROSS-SESSION MEMORY TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
