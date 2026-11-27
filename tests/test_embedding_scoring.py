"""
tests/test_embedding_scoring.py — Comprehensive Test Suite for Embedding-Based Semantic Similarity Scoring (F3 Bonus)
=======================================================================================================================
Validates:
  1. Highly relevant Python Developer job
  2. Relevant AI/ML Engineer job
  3. Unrelated Sales / Telemarketing job
  4. Senior Java Developer job (seniority penalty + stack mismatch)
  5. Candidate profile changes and SHA-256 cache invalidation
  6. Embedding model unavailable / graceful fallback preserving 100% LLM score
  7. Similarity score boundaries [0.0, 10.0]
  8. Final score combination math with configurable weights
  9. SQLite persistence of embedding_score, llm_score, and final_score
  10. Feed & Dashboard score field exposure
"""

import os
import sys
import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService
from services.scoring_service import ScoringService
from services.embedding_service import EmbeddingService


class TestEmbeddingSemanticScoring(unittest.TestCase):
    """Exhaustive test suite for F3 Bonus Embedding-Based Semantic Similarity."""

    @classmethod
    def setUpClass(cls):
        # Use isolated test database
        cls.test_db_path = str(BASE_DIR / "database" / "test_embedding_suite.db")
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except Exception:
                pass

        cls.db = DatabaseManager(cls.test_db_path)
        cls.profile_service = ProfileService(cls.db)
        cls.service = ScoringService(
            db_manager=cls.db,
            profile_service=cls.profile_service,
            mock_mode=True
        )
        cls.candidate_ctx = cls.service._get_candidate_context()

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.test_db_path):
            try:
                os.remove(cls.test_db_path)
            except Exception:
                pass

    # ── 1. Highly Relevant Python Developer Job ───────────────────────────────
    def test_01_highly_relevant_python_developer_job(self):
        """Verify high embedding similarity and high final score for Python Developer."""
        job = {
            "title": "Junior Python Developer",
            "company": "FastTech Solutions",
            "url": "https://fasttech.example/python_dev",
            "location": "Remote, India",
            "experience": "0-2 years",
            "description": "Looking for Junior Python Developer with hands-on experience in Python, SQL, FastAPI, REST APIs, and Git."
        }

        scored = self.service.score_single_job(job, self.candidate_ctx)

        self.assertIn("embedding_score", scored)
        self.assertIn("llm_score", scored)
        self.assertIn("final_score", scored)

        self.assertIsNotNone(scored["embedding_score"])
        # Python Developer should exhibit strong embedding similarity with candidate's Python background
        self.assertGreaterEqual(scored["embedding_score"], 5.0)
        self.assertGreaterEqual(scored["final_score"], 6.5)
        self.assertIn(scored["recommendation"], ["STRONG_APPLY", "APPLY"])
        self.assertIn("Python", [s.title() for s in scored["matched_skills"]])

    # ── 2. Relevant AI/ML Engineer Job ─────────────────────────────────────────
    def test_02_relevant_ai_ml_engineer_job(self):
        """Verify high embedding similarity and top-tier final score for AI/ML Engineer."""
        job = {
            "title": "Machine Learning Engineer (GenAI & RAG)",
            "company": "NextGen AI Labs",
            "url": "https://nextgen.example/ml_engineer",
            "location": "Bangalore, India",
            "experience": "0-1 years",
            "description": "Building production RAG pipelines, Agentic AI architectures, vector databases, LangGraph, and PyTorch models."
        }

        scored = self.service.score_single_job(job, self.candidate_ctx)

        self.assertIsNotNone(scored["embedding_score"])
        self.assertGreaterEqual(scored["embedding_score"], 6.0)
        self.assertGreaterEqual(scored["final_score"], 7.0)
        self.assertIn(scored["recommendation"], ["STRONG_APPLY", "APPLY"])

    # ── 3. Unrelated Sales Job ────────────────────────────────────────────────
    def test_03_unrelated_sales_job(self):
        """Verify low semantic similarity and low final score for unrelated non-tech role."""
        job = {
            "title": "B2B Inside Sales Executive & Cold Caller",
            "company": "Outbound Telemarketing Ltd",
            "url": "https://sales.example/cold_calling",
            "location": "Mumbai, India",
            "experience": "1-3 years",
            "description": "Outbound cold calling, lead generation, sales pipeline closing, CRM telemarketing, and revenue quotas."
        }

        scored = self.service.score_single_job(job, self.candidate_ctx)

        self.assertIsNotNone(scored["embedding_score"])
        # Unrelated sales role should have low embedding similarity with CS/AI profile
        self.assertLessEqual(scored["embedding_score"], 4.5)
        self.assertLessEqual(scored["final_score"], 4.0)
        self.assertEqual(scored["recommendation"], "SKIP")

    # ── 4. Senior Java Developer Job ──────────────────────────────────────────
    def test_04_senior_java_developer_job(self):
        """Verify seniority penalty and stack mismatch produce low score."""
        job = {
            "title": "Senior Java Architect",
            "company": "Legacy Systems Corp",
            "url": "https://legacy.example/java_architect",
            "location": "Berlin, Germany",
            "experience": "10+ years",
            "description": "Principal Java Architect with 10+ years in Spring Boot microservices, enterprise J2EE, Kafka, and Kubernetes."
        }

        scored = self.service.score_single_job(job, self.candidate_ctx)

        self.assertFalse(scored["experience_match"])
        self.assertLessEqual(scored["final_score"], 4.0)
        self.assertEqual(scored["recommendation"], "SKIP")

    # ── 5. Candidate Profile Changes & Cache Invalidation ─────────────────────
    def test_05_profile_changes_and_cache_invalidation(self):
        """Verify SHA-256 caching reuses embeddings when unchanged, and recomputes on change."""
        emb_service = self.service.embedding_service

        # 1. First call computes and caches
        emb1 = emb_service.get_candidate_embedding(self.candidate_ctx)
        hash1 = emb_service._cached_profile_hash
        self.assertIsNotNone(emb1)
        self.assertIsNotNone(hash1)

        # 2. Second call with identical context reuses cached vector
        emb2 = emb_service.get_candidate_embedding(self.candidate_ctx)
        hash2 = emb_service._cached_profile_hash
        self.assertEqual(hash1, hash2)
        self.assertIs(emb1, emb2)  # Identical reference from cache

        # 3. Altered profile context invalidates cache and computes new embedding
        altered_ctx = dict(self.candidate_ctx)
        altered_ctx["target_preferences"] = {
            "target_roles": ["Quantum Computing Researcher", "Theoretical Physicist"],
            "preferred_locations": ["Zurich, Switzerland"]
        }
        emb3 = emb_service.get_candidate_embedding(altered_ctx)
        hash3 = emb_service._cached_profile_hash

        self.assertNotEqual(hash1, hash3)
        self.assertIsNotNone(emb3)

    # ── 6. Embedding Model Unavailable Fallback ───────────────────────────────
    def test_06_embedding_model_unavailable_fallback(self):
        """Verify graceful degradation when embedding model fails or is disabled."""
        disabled_emb_service = EmbeddingService(enabled=False)
        self.assertFalse(disabled_emb_service.enabled)

        job = {
            "title": "Python ML Engineer",
            "company": "Test AI",
            "url": "https://test.example/job",
            "description": "Python, Machine Learning, RAG, SQL."
        }

        scores = disabled_emb_service.compute_similarity_scores([job], self.candidate_ctx)
        self.assertEqual(scores, [None])

        # Combined score should equal LLM score exactly
        combined = disabled_emb_service.combine_scores(llm_score=8.5, embedding_score=None)
        self.assertEqual(combined, 8.5)

    # ── 7. Similarity Score Boundaries [0.0, 10.0] ───────────────────────────
    def test_07_similarity_score_boundaries(self):
        """Verify normalized scores and combined scores stay strictly within [0.0, 10.0]."""
        emb_service = self.service.embedding_service

        # Extreme values test
        self.assertEqual(emb_service.combine_scores(10.0, 10.0), 10.0)
        self.assertEqual(emb_service.combine_scores(0.0, 0.0), 0.0)
        self.assertEqual(emb_service.combine_scores(12.5, 9.0), 10.0)  # Clamped upper
        self.assertEqual(emb_service.combine_scores(-5.0, 0.0), 0.0)   # Clamped lower

        # Standard range check
        sample_jobs = [
            {"title": "Job A", "company": "A", "description": "Text A"},
            {"title": "Job B", "company": "B", "description": "Text B"}
        ]
        sim_scores = emb_service.compute_similarity_scores(sample_jobs, self.candidate_ctx)
        for s in sim_scores:
            if s is not None:
                self.assertGreaterEqual(s, 0.0)
                self.assertLessEqual(s, 10.0)

    # ── 8. Final Score Calculation with Configurable Weights ──────────────────
    def test_08_final_score_calculation_weights(self):
        """Verify mathematical combination using custom configurable weights."""
        emb_service = self.service.embedding_service

        # Default weights (LLM=0.7, Emb=0.3)
        # Expected: (0.7 * 8.0 + 0.3 * 6.0) / 1.0 = 5.6 + 1.8 = 7.4
        c1 = emb_service.combine_scores(8.0, 6.0, llm_weight=0.7, embedding_weight=0.3)
        self.assertEqual(c1, 7.4)

        # Equal weights (0.5 / 0.5)
        # Expected: (0.5 * 8.0 + 0.5 * 6.0) / 1.0 = 7.0
        c2 = emb_service.combine_scores(8.0, 6.0, llm_weight=0.5, embedding_weight=0.5)
        self.assertEqual(c2, 7.0)

        # Pure LLM (LLM=1.0, Emb=0.0)
        c3 = emb_service.combine_scores(8.0, 6.0, llm_weight=1.0, embedding_weight=0.0)
        self.assertEqual(c3, 8.0)

    # ── 9. SQLite Persistence of All Three Score Fields ───────────────────────
    def test_09_sqlite_persistence_of_scores(self):
        """Verify embedding_score, llm_score, and final_score persist to SQLite."""
        test_url = "https://example.com/unique_emb_test_job_101"
        test_hash = self.db.make_job_hash(test_url)

        # Stage application record first
        self.db.save_application({
            "job_hash": test_hash,
            "job_url": test_url,
            "title": "Python & LLM Engineer",
            "company": "DeepIntelligence",
            "location": "Remote",
            "source": "remoteok",
            "status": "discovered"
        })

        job_to_score = {
            "title": "Python & LLM Engineer",
            "company": "DeepIntelligence",
            "url": test_url,
            "job_url": test_url,
            "description": "Python, Machine Learning, RAG, Agentic AI, Vector Databases.",
            "experience": "0-2 years",
            "location": "Remote"
        }

        # Score and persist
        scored_jobs = self.service.score_jobs([job_to_score], persist_to_db=True)
        self.assertEqual(len(scored_jobs), 1)

        # Retrieve from SQLite
        record = self.db.get_job_score(test_hash)
        self.assertIsNotNone(record)
        self.assertIn("embedding_score", record)
        self.assertIn("llm_score", record)
        self.assertIn("final_score", record)

        self.assertIsNotNone(record["embedding_score"])
        self.assertIsNotNone(record["llm_score"])
        self.assertIsNotNone(record["final_score"])

        self.assertEqual(record["final_score"], scored_jobs[0]["final_score"])

        # Verify applications table has synchronized relevance_score
        with self.db._get_conn() as conn:
            row = conn.execute("SELECT relevance_score FROM applications WHERE job_hash = ?", (test_hash,)).fetchone()
            self.assertEqual(row["relevance_score"], record["final_score"])

    # ── 10. Dashboard & Feed Score Field Exposure ─────────────────────────────
    def test_10_dashboard_feed_score_field_exposure(self):
        """Verify get_jobs_feed includes embedding_score, llm_score, and final_score."""
        feed = self.db.get_jobs_feed(page=1, per_page=10)
        self.assertIn("jobs", feed)
        self.assertGreater(len(feed["jobs"]), 0)

        # Check fields on retrieved items
        first_job = feed["jobs"][0]
        self.assertIn("embedding_score", first_job)
        self.assertIn("llm_score", first_job)
        self.assertIn("final_score", first_job)


if __name__ == "__main__":
    unittest.main()
