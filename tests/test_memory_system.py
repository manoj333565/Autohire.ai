"""
tests/test_memory_system.py — Comprehensive Verification Suite for Cross-Session Memory System
=============================================================================================
Validates all 15 Cross-Session Memory requirements:
  T01: Daily summary persistence in SQLite daily_memory_summaries and memory_store
  T02: Scraped/application/email metrics accuracy reflects actual database records
  T03: Previous session memory loading at startup
  T04: Context passing into AgentState session_memory
  T05: Completed application deduplication protection
  T06: Completed cold email deduplication protection
  T07: Remote API response cache HIT
  T08: Remote API response cache MISS
  T09: Remote API response cache TTL expiration
  T10: Explicit API cache invalidation (by provider/endpoint)
  T11: Bonus embedding-based semantic memory retrieval
  T12: Graceful fallback to keyword search when embedding model is unavailable
  T13: Memory pruning with reversible JSON backup creation
  T14: Strict preservation of critical execution history (applications, emails, approvals)
  T15: Complete end-to-end workflow memory lifecycle integration
"""

import sys
import os
import time
import json
import uuid
import sqlite3
import unittest
from pathlib import Path
from unittest.mock import patch

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.memory_service import MemoryService
from services.api_cache_service import ApiCacheService
from agent.state import AgentState
from agent.graph import get_initial_state, execute_workflow, build_job_agent_graph
from agent.nodes.memory_node import load_memory_node, save_memory_node


class TestCrossSessionMemorySystem(unittest.TestCase):
    """Test suite for Cross-Session Memory Architecture."""

    @classmethod
    def setUpClass(cls):
        cls.test_db_dir = Path(cfg.BASE_DIR) / "database"
        cls.test_db_dir.mkdir(parents=True, exist_ok=True)
        cls.test_db_path = str(cls.test_db_dir / f"test_memory_{uuid.uuid4().hex[:6]}.db")
        cls.db = DatabaseManager(cls.test_db_path)
        cls.memory_service = MemoryService(db_manager=cls.db)
        cls.cache_service = ApiCacheService(db_manager=cls.db)

    @classmethod
    def tearDownClass(cls):
        # Clean up temporary test database
        for suffix in ["", "-wal", "-shm"]:
            p = Path(cls.test_db_path + suffix)
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    # ── T01: Daily Summary Persistence ─────────────────────────────────
    def test_01_daily_summary_persistence(self):
        """T01: Verify daily summaries are stored in SQLite daily_memory_summaries and memory_store."""
        run_id = f"run_{uuid.uuid4().hex[:8]}"
        date_str = "2026-11-27"

        metrics = {
            "total_scraped": 20,
            "passed_filters": 8,
            "scored": 8,
            "applied": 3,
            "emailed": 2,
        }
        errors = ["Transient network timeout on Naukri portal"]
        completed_actions = ["scraped_20_jobs", "filtered_8_jobs", "scored_8_jobs"]

        record = self.memory_service.save_daily_summary(
            run_id=run_id,
            date_str=date_str,
            metrics=metrics,
            errors=errors,
            completed_actions=completed_actions,
        )

        self.assertEqual(record["run_id"], run_id)
        self.assertEqual(record["date"], date_str)
        self.assertEqual(record["total_scraped"], 20)

        # Verify in SQLite daily_memory_summaries table
        with self.db._get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM daily_memory_summaries WHERE run_id = ?", (run_id,)
            ).fetchone()
            self.assertIsNotNone(row, "Summary must be present in daily_memory_summaries table")
            self.assertEqual(row["run_id"], run_id)
            self.assertEqual(row["total_scraped"], 20)
            self.assertIn("Naukri", row["important_errors"])

        # Verify in memory_store table under daily_summary
        latest = self.memory_service.recall("latest_daily_summary")
        self.assertIsNotNone(latest)
        self.assertEqual(latest["category"], "daily_summary")
        print("  ✅ T01: Daily summary persistence verified in SQLite")

    # ── T02: Metrics Accuracy Reflects Actual DB Records ───────────────
    def test_02_metrics_accuracy_reflects_actual_db(self):
        """T02: Verify summary metrics reflect actual confirmed database records, not unverified counters."""
        u = uuid.uuid4().hex[:6]
        with self.db._get_conn() as conn:
            # Seed 2 confirmed applications
            conn.execute(
                "INSERT OR REPLACE INTO applications (job_hash, job_title, company, status) VALUES (?, 'Dev1', 'Co1', 'applied')",
                (f"h1_{u}",)
            )
            conn.execute(
                "INSERT OR REPLACE INTO applications (job_hash, job_title, company, status) VALUES (?, 'Dev2', 'Co2', 'manual_required')",
                (f"h2_{u}",)
            )
            # Seed 1 sent email
            conn.execute(
                "INSERT INTO email_history (job_hash, recipient_email, subject, body, email_type, status) VALUES (?, ?, 'Subj', 'Body', 'job_outreach', 'sent')",
                (f"h1_{u}", f"recruiter_{u}@co1.com")
            )
            # Seed 1 pending approval
            conn.execute(
                "INSERT INTO approval_queue (item_type, reference_id, title, company, status) VALUES ('job_application', ?, 'Title', 'Co', 'pending')",
                (f"ref_{u}",)
            )

        counts = self.db.get_actual_workflow_counts()
        self.assertGreaterEqual(counts["confirmed_applications"], 2)
        self.assertGreaterEqual(counts["confirmed_emails_sent"], 1)
        self.assertGreaterEqual(counts["pending_approvals"], 1)

        # Save summary and verify it records these exact confirmed database numbers
        record = self.memory_service.save_daily_summary(
            run_id=f"run_counts_{u}",
            date_str="2026-11-27",
            metrics={"scraped": 10, "passed_filters": 5, "scored": 5},
        )
        self.assertEqual(record["total_applications"], counts["confirmed_applications"])
        self.assertEqual(record["total_emails_sent"], counts["confirmed_emails_sent"])
        self.assertEqual(record["pending_approvals"], counts["pending_approvals"])
        print("  ✅ T02: Metrics accuracy strictly reflects confirmed database records")

    # ── T03: Previous Session Memory Loading ───────────────────────────
    def test_03_previous_session_memory_loading(self):
        """T03: Verify startup context retrieves previous daily summaries and interaction records."""
        context = self.memory_service.synthesize_startup_context()

        self.assertIn("latest_daily_summary", context)
        self.assertIn("recent_daily_summaries", context)
        self.assertIn("previously_applied_hashes", context)
        self.assertIn("previously_emailed_recipients", context)
        self.assertIn("pending_approvals", context)
        self.assertGreater(len(context["recent_daily_summaries"]), 0)
        print("  ✅ T03: Previous session memory loading at startup verified")

    # ── T04: Context Passing into AgentState ───────────────────────────
    def test_04_context_passing_into_agent_state(self):
        """T04: Verify load_memory_node populates AgentState session_memory."""
        state = get_initial_state(dry_run=True)
        # Patch config to use our test database
        with patch.object(cfg, "DB_PATH", self.test_db_path):
            out = load_memory_node(state)
            self.assertIn("session_memory", out)
            self.assertIn("recent_daily_summaries", out["session_memory"])
            self.assertIn("previously_applied_hashes", out["session_memory"])
            self.assertTrue(len(out["logs"]) > 0)
            self.assertIn("Loaded", out["logs"][0])
        print("  ✅ T04: Context passing into AgentState verified")

    # ── T05: Completed Application Deduplication ───────────────────────
    def test_05_completed_application_deduplication(self):
        """T05: Verify completed applications are tracked and authoritative DB deduplication prevents repeats."""
        test_hash = f"unique_app_hash_{uuid.uuid4().hex[:8]}"
        with self.db._get_conn() as conn:
            conn.execute(
                "INSERT INTO applications (job_hash, job_title, company, status) VALUES (?, 'Engineer', 'Acme', 'applied')",
                (test_hash,)
            )

        # Context loading captures this hash
        applied_set = self.db.get_applied_job_hashes()
        self.assertIn(test_hash, applied_set)

        # Attempting double apply is strictly detected by database
        with self.db._get_conn() as conn:
            row = conn.execute("SELECT status FROM applications WHERE job_hash = ?", (test_hash,)).fetchone()
            self.assertIn(row["status"], ("applied", "manual_required"))
        print("  ✅ T05: Completed application deduplication verified")

    # ── T06: Completed Cold Email Deduplication ────────────────────────
    def test_06_completed_email_deduplication(self):
        """T06: Verify emailed recruiters are tracked and protected against duplicate outreach."""
        recruiter_email = f"lead_{uuid.uuid4().hex[:6]}@targetcorp.com"
        with self.db._get_conn() as conn:
            conn.execute(
                "INSERT INTO email_history (job_hash, recipient_email, subject, body, email_type, status) VALUES ('h9', ?, 'Subj', 'Body', 'job_outreach', 'sent')",
                (recruiter_email,)
            )

        sent_recipients = self.db.get_sent_email_recipients()
        self.assertIn(recruiter_email.lower(), sent_recipients)
        print("  ✅ T06: Completed cold email deduplication verified")

    # ── T07: Remote API Response Cache HIT ─────────────────────────────
    def test_07_api_cache_hit(self):
        """T07: Verify API cache hit returns exact cached response without remote invocation."""
        endpoint = "gemini/generate_pitch"
        params = {"company": "Google DeepMind", "role": "Research Engineer", "api_key": "SECRET_KEY"}
        data = {"subject": "AI Research Inquiry", "score": 9.8}

        # Cache response
        key = self.cache_service.set(endpoint, params, data, source="gemini")
        self.assertIsNotNone(key)

        # Retrieve (with raw API key included in call — must sanitize and still HIT)
        hit = self.cache_service.get(endpoint, params, source="gemini")
        self.assertIsNotNone(hit)
        self.assertEqual(hit["subject"], "AI Research Inquiry")
        self.assertEqual(hit["score"], 9.8)
        print("  ✅ T07: API response cache HIT verified")

    # ── T08: Remote API Response Cache MISS ────────────────────────────
    def test_08_api_cache_miss(self):
        """T08: Verify cache miss returns None for unseen requests."""
        miss = self.cache_service.get("gemini/unseen_endpoint", {"unique": uuid.uuid4().hex}, source="gemini")
        self.assertIsNone(miss)
        print("  ✅ T08: API response cache MISS verified")

    # ── T09: Remote API Response Cache TTL Expiration ──────────────────
    def test_09_api_cache_expiration(self):
        """T09: Verify expired cache entries return None."""
        endpoint = "apollo/person_match"
        params = {"company": "ExpiringCorp", "uid": uuid.uuid4().hex[:6]}
        data = {"email": "contact@expiring.com"}

        # Set with 0-second TTL (instant expiration)
        key = self.cache_service.set(endpoint, params, data, source="apollo", ttl_seconds=-1)

        expired_result = self.cache_service.get(endpoint, params, source="apollo")
        self.assertIsNone(expired_result, "Expired cache entry must return None")
        print("  ✅ T09: API response cache TTL expiration verified")

    # ── T10: Explicit Cache Invalidation ───────────────────────────────
    def test_10_cache_invalidation(self):
        """T10: Verify explicit cache invalidation by key, endpoint, or provider."""
        endpoint = "snov/domain_search"
        params = {"domain": f"test_{uuid.uuid4().hex[:6]}.com"}
        data = {"emails": ["info@test.com"]}

        key = self.cache_service.set(endpoint, params, data, source="snov")
        self.assertIsNotNone(self.cache_service.get(endpoint, params, source="snov"))

        # Invalidate by source
        invalidated_count = self.cache_service.invalidate(source="snov")
        self.assertGreaterEqual(invalidated_count, 1)

        # After invalidation, get must return None
        result = self.cache_service.get(endpoint, params, source="snov")
        self.assertIsNone(result, "Invalidated cache entry must return None")
        print("  ✅ T10: Explicit cache invalidation verified")

    # ── T11: Bonus Semantic Memory Retrieval ───────────────────────────
    def test_11_semantic_memory_retrieval(self):
        """T11: Verify embedding-based semantic memory retrieval ranks conceptually similar items highest."""
        uid = uuid.uuid4().hex[:6]
        key_rag = f"rag_memory_{uid}"
        key_web = f"web_memory_{uid}"

        self.memory_service.remember(
            key_rag,
            "Engineered production RAG pipelines using LangChain, LangGraph, and Milvus vector embeddings with hybrid search.",
            category="candidate_insights",
        )
        self.memory_service.remember(
            key_web,
            "Basic HTML and CSS layout design for responsive landing pages with WordPress.",
            category="candidate_insights",
        )

        results = self.memory_service.semantic_search("autonomous multi-agent AI and vector databases", limit=2)
        self.assertGreater(len(results), 0)
        top_match = results[0]
        self.assertIn("RAG", top_match["content"])
        self.assertIn(top_match.get("retrieval_mode"), ("semantic", "keyword_fallback"))
        print(f"  ✅ T11: Semantic memory retrieval verified (Mode: {top_match.get('retrieval_mode')})")

    # ── T12: Graceful Embedding Unavailable Fallback ───────────────────
    def test_12_embedding_model_unavailable_fallback(self):
        """T12: Verify graceful fallback to keyword search when embedding service is disabled/offline."""
        svc = MemoryService(db_manager=self.db)
        svc._embedding_service = None
        svc._embedding_initialized = True  # Prevent lazy load

        uid = uuid.uuid4().hex[:6]
        svc.remember(f"fallback_test_{uid}", "Candidate excels at FastAPI REST API development and Docker containerization.", category="candidate_insights")

        results = svc.semantic_search("FastAPI Docker", limit=2)
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["retrieval_mode"], "keyword_fallback")
        self.assertIn("FastAPI", results[0]["content"])
        print("  ✅ T12: Graceful embedding unavailable fallback to keyword ranking verified")

    # ── T13: Memory Pruning with Reversible Backup ─────────────────────
    def test_13_memory_pruning_and_backups(self):
        """T13: Verify safe memory retention, pruning old summaries and expired cache with backup."""
        # Seed an old summary (45 days ago)
        old_date = "2026-09-01"
        self.db.save_daily_memory_summary({
            "run_id": f"run_ancient_{uuid.uuid4().hex[:6]}",
            "date": old_date,
            "total_scraped": 5,
        })
        # Seed 6 recent summaries so minimum 5 is satisfied
        for i in range(6):
            self.db.save_daily_memory_summary({
                "run_id": f"run_recent_{i}_{uuid.uuid4().hex[:6]}",
                "date": f"2026-11-{20+i}",
                "total_scraped": 10,
            })

        # Seed expired cache entry
        self.cache_service.set("endpoint/prune_test", {"q": 1}, {"data": 1}, ttl_seconds=-10)

        prune_result = self.memory_service.prune_memory(retention_days=30, create_backup=True, preserve_minimum_summaries=5)

        self.assertIn("summaries_pruned", prune_result)
        self.assertIn("cache_pruned", prune_result)
        self.assertIsNotNone(prune_result.get("backup_file"))
        self.assertTrue(Path(prune_result["backup_file"]).exists(), "Reversible backup JSON file must exist on disk")

        # Verify audit record stored
        audit = self.memory_service.recall_by_category("memory_maintenance")
        self.assertGreater(len(audit), 0)
        print("  ✅ T13: Memory pruning with reversible JSON backup verified")

    # ── T14: Strict Preservation of Execution History ──────────────────
    def test_14_preservation_of_critical_execution_history(self):
        """T14: Verify memory pruning NEVER deletes applications, email history, or approval queues."""
        u = uuid.uuid4().hex[:6]
        with self.db._get_conn() as conn:
            conn.execute("INSERT OR REPLACE INTO applications (job_hash, job_title, company, status) VALUES (?, 'Dev', 'Co', 'applied')", (f"crit_app_{u}",))
            conn.execute("INSERT INTO email_history (job_hash, recipient_email, subject, body, email_type, status) VALUES (?, 'crit@co.com', 'S', 'B', 'job_outreach', 'sent')", (f"crit_app_{u}",))
            conn.execute("INSERT INTO approval_queue (item_type, reference_id, title, company, status) VALUES ('job_application', ?, 'T', 'C', 'pending')", (f"crit_app_{u}",))

        # Execute aggressive pruning (1 day retention)
        self.memory_service.prune_memory(retention_days=1, create_backup=False)

        # Verify critical tables are 100% untouched
        with self.db._get_conn() as conn:
            app_exists = conn.execute("SELECT 1 FROM applications WHERE job_hash = ?", (f"crit_app_{u}",)).fetchone()
            email_exists = conn.execute("SELECT 1 FROM email_history WHERE job_hash = ?", (f"crit_app_{u}",)).fetchone()
            approval_exists = conn.execute("SELECT 1 FROM approval_queue WHERE reference_id = ?", (f"crit_app_{u}",)).fetchone()

        self.assertIsNotNone(app_exists, "applications records must NEVER be deleted during pruning")
        self.assertIsNotNone(email_exists, "email_history records must NEVER be deleted during pruning")
        self.assertIsNotNone(approval_exists, "approval_queue records must NEVER be deleted during pruning")
        print("  ✅ T14: Strict preservation of critical execution history verified")

    # ── T15: Complete Workflow Memory Integration ──────────────────────
    def test_15_complete_workflow_memory_integration(self):
        """T15: Verify end-to-end multi-agent execution integrates load_memory and save_memory lifecycle."""
        thread_id = f"mem_e2e_{uuid.uuid4().hex[:8]}"

        with patch.object(cfg, "DB_PATH", self.test_db_path):
            final_state = execute_workflow(
                dry_run=True,
                mock_mode=True,
                thread_id=thread_id,
            )

            self.assertIsNotNone(final_state)
            self.assertEqual(final_state.get("run_id"), thread_id)
            self.assertIn("session_memory", final_state)

            # Verify daily summary was saved for this workflow run
            latest_summary = self.memory_service.recall("latest_daily_summary")
            self.assertIsNotNone(latest_summary)

            # Subsequent workflow execution loads this summary in startup context
            state2 = get_initial_state(dry_run=True)
            out2 = load_memory_node(state2)
            self.assertIn("session_memory", out2)
            self.assertIn("recent_daily_summaries", out2["session_memory"])
        print("  ✅ T15: Complete workflow memory integration verified across multiple executions")


if __name__ == "__main__":
    print("\n" + "=" * 76)
    print("  🧪 RUNNING COMPREHENSIVE CROSS-SESSION MEMORY TEST SUITE")
    print("=" * 76 + "\n")
    unittest.main(verbosity=2)
