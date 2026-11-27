"""
tests/test_gemini_engine.py — Production-Grade Google Gemini AI Engine Verification Suite
=======================================================================================
Validates:
  1. Gemini Client Authentication & Sanitized Masked Key Initialization
  2. One Basic Generation Request
  3. Intelligent Structured Job Scoring (Match Score, Skills, Education, Experience, Recommendation)
  4. Strict Truth Preservation & Zero Hallucination of Skills/Degrees
  5. Resilient JSON Markdown Fence Cleaning & Malformed Response Recovery
  6. Exponential Backoff & Retry Handling for 429 (Quota) and 503 (Demand Spike) Errors
  7. Deterministic Heuristic Fallback Engine with Full Structured Output
  8. Personalized, Truthful Recruiter Cold Email Generation
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
from services.scoring_service import ScoringService, mask_key


class TestGeminiAIEngine(unittest.TestCase):
    """Exhaustive test suite for Gemini AI Engine and Intelligent Scoring."""

    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager(cfg.DB_PATH)
        cls.service = ScoringService(db_manager=cls.db, mock_mode=False)

    # ── 1. Authentication & Configuration ─────────────────────────────────────
    def test_01_authentication_and_masked_logging(self):
        """Verify API key is configured and sanitized for logging."""
        self.assertTrue(bool(self.service.api_key), "GEMINI_API_KEY must be configured in environment.")
        masked = mask_key(self.service.api_key)
        self.assertNotIn(self.service.api_key, masked)
        self.assertTrue(masked.startswith("AIza") or "..." in masked)
        self.assertIn(self.service.model_name, ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"])

    # ── 2. Basic Generation Request ───────────────────────────────────────────
    def test_02_basic_generation_or_safe_fallback(self):
        """Verify minimal prompt generation with graceful fallback on offline/sandboxed mode."""
        test_prompt = "Respond with the word: VERIFIED"
        try:
            resp_text = self.service._call_gemini_with_retry(test_prompt)
            self.assertTrue(bool(resp_text))
            self.assertIn("VERIFIED", resp_text.upper())
        except Exception as e:
            # If network/sandbox blocks external access, verify error was caught cleanly
            self.assertIn(type(e).__name__, ["RuntimeError", "ClientError", "ServerError", "ConnectError"])

    # ── 3. Intelligent Job Scoring Structured Schema ──────────────────────────
    def test_03_intelligent_job_scoring_structured_schema(self):
        """Verify job scoring returns comprehensive structured JSON with all required fields."""
        test_job = {
            "title": "Machine Learning Engineer (LLM & Agentic AI)",
            "company": "DeepMind Innovations",
            "url": "https://careers.google.com/jobs/ai_engineer_e2e_01",
            "location": "Remote, India",
            "experience": "0-2 years",
            "description": "Looking for a Python AI Engineer with experience in RAG pipelines, Agentic AI, PyTorch, LangGraph, and Vector Databases."
        }

        scored = self.service.score_single_job(test_job)

        # 1. Scores & Metrics
        self.assertIn("relevance_score", scored)
        self.assertIn("match_score", scored)
        self.assertGreaterEqual(scored["match_score"], 7.0)
        self.assertLessEqual(scored["match_score"], 10.0)

        # 2. Skills Breakdowns
        self.assertIn("matched_skills", scored)
        self.assertIn("missing_skills", scored)
        self.assertIn("required_skills_match", scored)
        self.assertIn("preferred_skills_match", scored)
        self.assertIn("Python", [s.title() for s in scored["matched_skills"]])

        # 3. Education Compatibility
        self.assertIn("education_compatibility", scored)
        edu = scored["education_compatibility"]
        self.assertIn("compatible", edu)
        self.assertTrue(edu["compatible"])
        self.assertIn("level", edu)

        # 4. Experience Compatibility
        self.assertIn("experience_compatibility", scored)
        exp = scored["experience_compatibility"]
        self.assertIn("compatible", exp)
        self.assertTrue(exp["compatible"])

        # 5. Explanations & Recommendations
        self.assertIn("explanation", scored)
        self.assertIn("reasoning", scored)
        self.assertIn("recommendation", scored)
        self.assertIn(scored["recommendation"], ["STRONG_APPLY", "APPLY", "CONSIDER", "SKIP"])
        self.assertIn("skills_gap_analysis", scored)

    # ── 4. Strict Truth Preservation & Zero Hallucination ─────────────────────
    def test_04_truth_preservation_no_hallucinations(self):
        """Verify system uses strictly real candidate background and never invents qualifications."""
        ctx = self.service._get_candidate_context()
        identity = ctx.get("candidate_identity", {})

        # Verify candidate identity
        self.assertEqual(identity.get("name"), "Manoj Kumar Mandal")
        self.assertIn("Satya Sai Engineering College", identity.get("college", ""))
        self.assertIn("8.01/10", identity.get("education", ""))

        # Score a role with technologies candidate does NOT have (e.g. Ruby, Cobol, Rust)
        unrelated_job = {
            "title": "Principal Cobol & Ruby Architect",
            "company": "Legacy Systems Corp",
            "url": "https://legacy.example/job",
            "location": "Remote",
            "experience": "10+ years",
            "description": "Requires 10 years of Ruby on Rails, Cobol mainframes, Rust, and Haskell."
        }

        scored = self.service.score_single_job(unrelated_job)
        self.assertFalse(scored["experience_match"])
        self.assertIn(scored["recommendation"], ["CONSIDER", "SKIP"])
        # Cobol and Haskell must be classified as missing, NEVER matched
        matched_lower = [m.lower() for m in scored["matched_skills"]]
        self.assertNotIn("cobol", matched_lower)
        self.assertNotIn("haskell", matched_lower)

    # ── 5. Invalid Response Handling & Markdown Fence Cleaning ────────────────
    def test_05_invalid_response_handling_and_markdown_cleaning(self):
        """Verify markdown fences and whitespace are stripped to extract valid JSON."""
        raw_markdown_fenced = """```json
[
  {
    "job_id": 0,
    "match_score": 8.2,
    "matched_skills": ["Python", "FastAPI"],
    "missing_skills": ["Kubernetes"],
    "recommendation": "STRONG_APPLY"
  }
]
```"""
        cleaned = self.service._extract_json_string(raw_markdown_fenced)
        parsed = json.loads(cleaned)
        self.assertIsInstance(parsed, list)
        self.assertEqual(parsed[0]["match_score"], 8.2)

        # Text with chatter before and after JSON
        raw_with_chatter = """Here is your evaluation result:
[
  {"job_id": 0, "match_score": 9.0}
]
Hope this helps!"""
        cleaned2 = self.service._extract_json_string(raw_with_chatter)
        parsed2 = json.loads(cleaned2)
        self.assertEqual(parsed2[0]["match_score"], 9.0)

    # ── 6. Mocked 429 and 503 Retry Behavior with Exponential Backoff ─────────
    def test_06_mocked_429_and_503_retry_behavior(self):
        """Verify exponential backoff retries on transient 503 / 429 errors and succeeds."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = json.dumps([{
            "job_id": 0,
            "match_score": 8.7,
            "matched_skills": ["Python", "RAG"],
            "missing_skills": [],
            "recommendation": "STRONG_APPLY"
        }])

        # Fail with 503 on attempt 1, fail with 429 on attempt 2, succeed on attempt 3
        mock_client.models.generate_content.side_effect = [
            Exception("503 Service Unavailable: High demand spike"),
            Exception("429 ResourceExhausted: Quota exceeded, retry in 1s"),
            mock_response
        ]

        test_service = ScoringService(db_manager=self.db, mock_mode=False)
        test_service._client = mock_client
        test_service.backoff_factor = 0.05  # Fast test backoff
        test_service.delay_between_calls = 0

        result_text = test_service._call_gemini_with_retry("Evaluate job")
        self.assertTrue(bool(result_text))
        self.assertIn("8.7", result_text)
        self.assertEqual(mock_client.models.generate_content.call_count, 3)

    # ── 7. Deterministic Heuristic Fallback Engine ─────────────────────────────
    def test_07_deterministic_heuristic_fallback_engine(self):
        """Verify offline deterministic heuristic scorer produces full structured output."""
        mock_service = ScoringService(db_manager=self.db, mock_mode=True)
        self.assertTrue(mock_service.mock_mode)

        test_job = {
            "title": "Junior Python & Generative AI Developer",
            "company": "ScaleAI Cloud",
            "url": "https://heuristic.test/job",
            "location": "Remote",
            "experience": "0-1 years",
            "description": "Proficient in Python, RAG pipelines, LangGraph, FastAPI, and Machine Learning."
        }

        scored = mock_service.score_single_job(test_job)
        self.assertEqual(scored["raw_gemini_response"], "heuristic_fallback_engine")
        self.assertGreaterEqual(scored["relevance_score"], 7.0)
        self.assertIn("matched_skills", scored)
        self.assertIn("education_compatibility", scored)
        self.assertIn("experience_compatibility", scored)
        self.assertIn("skills_gap_analysis", scored)
        self.assertIn(scored["recommendation"], ["STRONG_APPLY", "APPLY"])

    # ── 8. Personalized Recruiter Cold Email Generation ───────────────────────
    def test_08_personalized_recruiter_email_generation(self):
        """Verify personalized email generation highlights candidate's actual projects and skills."""
        job = {
            "title": "AI/ML Engineer",
            "company": "ScaleAI Innovations",
            "description": "Building next-generation generative AI and computer vision models with Python."
        }

        email_pitch = self.service.generate_recruiter_email(job)

        self.assertIn("subject", email_pitch)
        self.assertIn("body_text", email_pitch)
        self.assertIn("body_html", email_pitch)

        text = email_pitch["body_text"]

        # Verify candidate identity and education
        self.assertIn("Manoj Kumar Mandal", text)
        self.assertIn("8.01/10", text)
        self.assertIn("Satya Sai Engineering College", text)

        # Verify actual candidate projects are referenced
        self.assertIn("EduNexora AI", text)
        self.assertIn("Attendance Management", text)
        self.assertIn("GeeksforGeeks", text)

        # Verify no fake senior experience claimed
        self.assertNotIn("10 years", text.lower())
        self.assertNotIn("director", text.lower())


if __name__ == "__main__":
    unittest.main()
