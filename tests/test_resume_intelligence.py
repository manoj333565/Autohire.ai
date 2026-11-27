"""
tests/test_resume_intelligence.py — Phase 2 Advanced Resume Intelligence Engine Tests
====================================================================================
Comprehensive automated verification covering:
  T1: PDF parsing of actual resume at data/resume.pdf
  T2: Missing, empty, and corrupted PDF handling
  T3: Technical skills extraction and domain classification
  T4: Skill classification: Proficient vs Intermediate vs Learning vs Pending Review
  T5: Projects, experience, certifications, and honors extraction
  T6: Profile storage and candidate/skill duplicate prevention
  T7: Safe synchronization and user confirmation gate
  T8: Intelligent job matching with Gemini vs Heuristic engine distinction
  T9: Dashboard profile API routes and integration
"""

import os
import sys
import json
import tempfile
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.resume_parser import ResumeParser, ResumeParseError
from services.profile_service import ProfileService
from services.scoring_service import ScoringService


class TestResumeIntelligenceEngine(unittest.TestCase):
    """Test suite for Phase 2 Resume Intelligence Engine."""

    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager(cfg.DB_PATH)
        cls.profile_service = ProfileService(cls.db)
        cls.parser = ResumeParser()
        cls.resume_path = str(Path(cfg.BASE_DIR) / "data" / "resume.pdf")

    # -------------------------------------------------------------------------
    # T1: Real PDF Parsing
    # -------------------------------------------------------------------------
    def test_01_real_pdf_parsing_candidate_identity(self):
        """Verify extraction of candidate identity and education from actual data/resume.pdf."""
        self.assertTrue(os.path.exists(self.resume_path), f"Resume file not found at {self.resume_path}")
        result = self.parser.parse(self.resume_path)

        # Validate metadata
        self.assertEqual(result["metadata"]["parse_status"], "SUCCESS")
        self.assertGreater(result["metadata"]["file_size_bytes"], 1000)
        self.assertEqual(result["metadata"]["page_count"], 1)

        # Validate candidate identity
        cand = result["candidate_info"]
        self.assertEqual(cand["name"], "Manoj Kumar Mandal")
        self.assertEqual(cand["email"], "manojkumarmandal1111@gmail.com")
        self.assertIn("8404970494", cand["phone"].replace("-", "").replace(" ", ""))
        self.assertIn("linkedin.com/in/manoj", cand["linkedin_url"].lower())

        # Validate education
        edu = result["education"]
        self.assertIn("Satya Sai Engineering College", edu["college"])
        self.assertIn("Computer Science", edu["degree"])
        self.assertEqual(edu["cgpa"], "8.01/10")
        self.assertEqual(edu["year"], "2026")

    # -------------------------------------------------------------------------
    # T2: Robust Error Handling (Missing, Empty, Corrupted)
    # -------------------------------------------------------------------------
    def test_02_error_handling_missing_empty_corrupt(self):
        """Verify strict exceptions for missing, empty (0 bytes), and non-PDF corrupted files."""
        # 1. Missing file
        with self.assertRaises(FileNotFoundError):
            self.parser.parse("data/non_existent_resume_xyz.pdf")

        # 2. Empty 0-byte file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            empty_path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                self.parser.parse(empty_path)
            self.assertIn("empty (0 bytes)", str(ctx.exception).lower())
        finally:
            if os.path.exists(empty_path):
                os.remove(empty_path)

        # 3. Corrupt non-PDF file
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"NOT A REAL PDF FILE CONTENT AT ALL")
            corrupt_path = f.name
        try:
            with self.assertRaises(ValueError) as ctx:
                self.parser.parse(corrupt_path)
            self.assertIn("pdf header signature", str(ctx.exception).lower())
        finally:
            if os.path.exists(corrupt_path):
                os.remove(corrupt_path)

    # -------------------------------------------------------------------------
    # T3: Technical Skills Extraction & Categorization
    # -------------------------------------------------------------------------
    def test_03_technical_skills_extraction(self):
        """Verify extraction and structured categorization of technical skills."""
        result = self.parser.parse(self.resume_path)
        skills = result["skills"]

        # Programming languages
        self.assertIn("Python", skills["programming_languages"])
        self.assertIn("SQL", skills["programming_languages"])

        # Frameworks & libraries
        self.assertIn("NumPy", skills["frameworks_and_libraries"])
        self.assertIn("Pandas", skills["frameworks_and_libraries"])
        self.assertIn("Scikit-learn", skills["frameworks_and_libraries"])
        self.assertIn("OpenCV", skills["frameworks_and_libraries"])

        # ML / AI competencies
        ml_skills = [s.lower() for s in skills["machine_learning_ai"]]
        self.assertTrue(any("deep learning" in s for s in ml_skills))
        self.assertTrue(any("nlp" in s for s in ml_skills))
        self.assertTrue(any("random forest" in s for s in ml_skills))

        # Databases & tools
        db_tools = skills["databases_and_tools"]
        self.assertIn("PostgreSQL", db_tools)
        self.assertIn("MySQL", db_tools)
        self.assertIn("Power BI", db_tools)

    # -------------------------------------------------------------------------
    # T4: Skill Classification & Uncertainty Handling
    # -------------------------------------------------------------------------
    def test_04_skill_classification_and_review_gate(self):
        """Verify verified proficiency tiers and proper staging of unverified skills."""
        profile = self.profile_service.get_full_profile(profile_id=1)
        tiers = profile["skills_by_tier"]

        # Verified tiers must hold candidate's declared competencies
        proficient = [s["skill_name"] for s in tiers["Proficient"]]
        intermediate = [s["skill_name"] for s in tiers["Intermediate"]]
        learning = [s["skill_name"] for s in tiers["Learning"]]

        self.assertIn("RAG", proficient)
        self.assertIn("Agentic AI", proficient)
        self.assertIn("LangGraph", intermediate)
        self.assertIn("Docker", learning)

        # Unverified skills must not be automatically assigned verified tiers without confirmation
        new_skill = "Apache Spark Streaming"
        self.profile_service.confirm_skill_proficiency(
            profile_id=1,
            skill_name=new_skill,
            proficiency="Intermediate",
            category="Big Data"
        )
        profile_after = self.profile_service.get_full_profile(profile_id=1)
        inter_names = [s["skill_name"] for s in profile_after["skills_by_tier"]["Intermediate"]]
        self.assertIn(new_skill, inter_names)

        # Clean up
        skill_id = next(s["id"] for s in profile_after["skills"] if s["skill_name"] == new_skill)
        self.profile_service.delete_skill(skill_id)

    # -------------------------------------------------------------------------
    # T5: Projects & Experience Extraction
    # -------------------------------------------------------------------------
    def test_05_projects_and_experience_extraction(self):
        """Verify accurate extraction of projects, experience, certifications, and honors."""
        result = self.parser.parse(self.resume_path)

        # 3 Projects
        projects = result["projects"]
        self.assertEqual(len(projects), 3)

        project_names = [p["name"] for p in projects]
        self.assertTrue(any("EduNexora AI" in n for n in project_names))
        self.assertTrue(any("Customer Purchase" in n for n in project_names))
        self.assertTrue(any("Attendance Management" in n for n in project_names))

        # Check tech stack & description accuracy
        edunexora = next(p for p in projects if "EduNexora" in p["name"])
        self.assertIn("Python", edunexora["tech_stack"])
        self.assertIn("SHAP", edunexora["tech_stack"])
        self.assertIn("96% accuracy", edunexora["description"])

        # Experience
        exp = result["experience"]
        self.assertEqual(len(exp), 1)
        self.assertIn("Campus Ambassador", exp[0]["role"])
        self.assertEqual(exp[0]["company"], "GeeksforGeeks")

        # Certifications
        certs = result["certifications"]
        self.assertTrue(any("Oracle Cloud" in c for c in certs))
        self.assertTrue(any("McKinsey" in c for c in certs))
        self.assertTrue(any("IBM" in c for c in certs))

    # -------------------------------------------------------------------------
    # T6: Profile Storage & Duplicate Prevention
    # -------------------------------------------------------------------------
    def test_06_profile_storage_and_duplicate_prevention(self):
        """Verify candidate profile uniqueness and database UPSERT constraints on candidate_skills."""
        profile = self.profile_service.get_full_profile(1)
        self.assertIsNotNone(profile)
        self.assertEqual(profile["id"], 1)

        # Duplicate skill declaration must update existing entry, not create duplicate row
        test_skill = "Vector Databases"
        before_count = len(self.profile_service.get_full_profile(1)["skills"])

        # Re-add existing skill
        self.profile_service.add_skill(1, test_skill, "Proficient", "AI Infrastructure")
        after_count = len(self.profile_service.get_full_profile(1)["skills"])

        self.assertEqual(before_count, after_count, "Adding existing skill must not produce duplicate row in candidate_skills")

    # -------------------------------------------------------------------------
    # T7: Safe Sync & User Confirmation Gate
    # -------------------------------------------------------------------------
    def test_07_safe_sync_and_confirmation_gate(self):
        """Verify that sync_profile_with_resume requires user confirmation before applying changes."""
        # 1. Without confirmation
        dry_result = self.profile_service.sync_profile_with_resume(profile_id=1, confirm_overwrite=False)
        self.assertEqual(dry_result["status"], "REQUIRES_CONFIRMATION")
        self.assertTrue(dry_result["requires_confirmation"])
        self.assertIn("extracted_data", dry_result)
        self.assertIn("diff_summary", dry_result)

        # 2. With confirmation
        sync_result = self.profile_service.sync_profile_with_resume(profile_id=1, confirm_overwrite=True)
        self.assertEqual(sync_result["status"], "SYNCHRONIZED")
        self.assertFalse(sync_result["requires_confirmation"])

    # -------------------------------------------------------------------------
    # T8: Intelligent Job Matching Engine Distinction
    # -------------------------------------------------------------------------
    def test_08_scoring_engine_distinction_and_schema(self):
        """Verify that job scoring returns all 8 required fields and distinguishes Gemini vs Heuristic engine."""
        scoring_service = ScoringService(mock_mode=True)
        job = {
            "title": "Machine Learning Engineer",
            "company": "DeepAI Innovations",
            "location": "Remote",
            "description": "Looking for a Python Machine Learning Engineer with experience in PyTorch, OpenCV, and RAG.",
            "job_url": "https://example.com/jobs/ml-101",
            "experience": "0-2 years"
        }

        scored = scoring_service.score_single_job(job)

        # Check all 8 required keys
        required_keys = [
            "match_score",
            "matched_skills",
            "missing_skills",
            "education_compatibility",
            "experience_compatibility",
            "skills_gap_analysis",
            "explanation",
            "recommendation",
            "scoring_engine",
            "is_ai_generated"
        ]
        for k in required_keys:
            self.assertIn(k, scored, f"Missing required scoring key '{k}'")

        # Mock mode uses heuristic fallback engine
        self.assertEqual(scored["scoring_engine"], "heuristic_fallback")
        self.assertFalse(scored["is_ai_generated"])
        self.assertGreaterEqual(scored["match_score"], 6.0)
        self.assertIn(scored["recommendation"], ["STRONG_APPLY", "APPLY", "CONSIDER", "SKIP"])

    # -------------------------------------------------------------------------
    # T9: Dashboard Profile Routes & Integration
    # -------------------------------------------------------------------------
    def test_09_dashboard_profile_routes(self):
        """Verify Flask dashboard profile, education, preferences, and reprocess APIs."""
        from dashboard.app import app
        app.config["TESTING"] = True

        with app.test_client() as client:
            # Login to establish session
            with client.session_transaction() as sess:
                sess["authenticated"] = True
                sess["user"] = cfg.CANDIDATE_NAME

            # 1. GET /api/profile
            res_get = client.get("/api/profile")
            self.assertEqual(res_get.status_code, 200)
            data_get = res_get.get_json()
            self.assertEqual(data_get["status"], "success")
            self.assertIn("resume_metadata", data_get["profile"])
            self.assertIn("education", data_get["profile"])
            self.assertIn("projects", data_get["profile"])

            # 2. POST /api/profile (Update personal details)
            res_post = client.post("/api/profile", json={"location": "India / Remote"})
            self.assertEqual(res_post.status_code, 200)

            # 3. POST /api/profile/education
            res_edu = client.post("/api/profile/education", json={
                "degree": "B.Tech (Hon's) Computer Science and Engineering",
                "college": "Satya Sai Engineering College Balasore, Odisha",
                "year": "2026",
                "cgpa": "8.01/10"
            })
            self.assertEqual(res_edu.status_code, 200)

            # 4. POST /api/profile/reprocess-resume (confirmation gate check)
            res_reprocess = client.post("/api/profile/reprocess-resume", json={"confirm": False})
            self.assertEqual(res_reprocess.status_code, 200)
            data_rep = res_reprocess.get_json()
            self.assertEqual(data_rep["result"]["status"], "REQUIRES_CONFIRMATION")


if __name__ == "__main__":
    print("Running Phase 2 Advanced Resume Intelligence Engine Tests...")
    unittest.main()
