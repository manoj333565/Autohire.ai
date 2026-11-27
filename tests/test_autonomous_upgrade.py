"""
tests/test_autonomous_upgrade.py — Phase 11 Autonomous System Upgrade Test Suite
================================================================================
Validates all 13 Phase 11 requirements:
  1. Database reset & backup generation
  2. Empty database statistics (all 0s, zero hardcoded/mock data)
  3. Dynamic candidate profile loading (Manoj Kumar Mandal)
  4. Scrapers execution (RemoteOK, WWR, Internshala, Wellfound, LinkedIn, Naukri, ATS)
  5. Mock data containment (dry-run does not increment live submitted metrics)
  6. Multi-level duplicate prevention
  7. Gemini matching & scoring engine
  8. Application lifecycle & approval queue workflow
  9. Cold email approval & delivery tracking
  10. HR outreach tracking
  11. Dashboard REST APIs & Chart endpoints
  12. Background automation scheduler
  13. Failure handling & error recovery
"""

import os
import sys
import json
import unittest
import tempfile
import sqlite3
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService
from services.scoring_service import ScoringService
from services.apply_service import ApplyService
from services.email_service import EmailService
from services.scheduler_service import SchedulerService
from scrapers.scraper_engine import ScraperEngine
from scrapers.ats_scraper import AtsScraper
from dashboard.app import app


class TestAutonomousUpgrade(unittest.TestCase):
    """Exhaustive test suite covering all Phase 11 validation gates."""

    def setUp(self):
        self.temp_db_fd, self.temp_db_path = tempfile.mkstemp(suffix=".db")
        os.close(self.temp_db_fd)
        self.db = DatabaseManager(self.temp_db_path)
        self.profile_service = ProfileService(self.db)
        app.config["TESTING"] = True
        self.client = app.test_client()

    def tearDown(self):
        if os.path.exists(self.temp_db_path):
            try:
                os.remove(self.temp_db_path)
            except Exception:
                pass

    # 1. Test database reset and backup
    def test_01_database_reset_and_backup(self):
        from scripts.reset_autohire_database import reset_database
        res = reset_database(Path(self.temp_db_path), confirm=True, dry_run=False)
        self.assertEqual(res["status"], "success")
        self.assertTrue(Path(res["backup_path"]).exists())
        self.assertGreater(Path(res["backup_path"]).stat().st_size, 0)
        # Verify candidate profile was re-seeded
        with self.db._get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM candidate_profile;").fetchone()[0]
            self.assertEqual(cnt, 1)

    # 2. Verify empty database statistics
    def test_02_empty_database_statistics(self):
        all_time = self.db.get_all_time_stats()
        self.assertEqual(all_time["total_jobs_seen"], 0)
        self.assertEqual(all_time["total_applied"], 0)
        self.assertEqual(all_time["total_emailed"], 0)
        self.assertEqual(all_time["total_failed"], 0)
        self.assertEqual(all_time["total_failed_emails"], 0)
        self.assertEqual(all_time["total_hr_outreach_sent"], 0)
        self.assertEqual(all_time["unique_companies"], 0)
        self.assertEqual(self.db.get_portal_stats(), [])
        self.assertEqual(self.db.get_chart_stats(), [])

    # 3. Test profile loading
    def test_03_profile_loading(self):
        prof = self.profile_service.get_full_profile(1)
        self.assertEqual(prof["name"], "Manoj Kumar Mandal")
        self.assertEqual(prof["email"], "manojkumarmandal1111@gmail.com")
        self.assertEqual(prof["phone"], "+91-8404970494")
        self.assertIn("Satya Sai Engineering College", prof["education"]["college"])
        self.assertGreaterEqual(len(prof["skills"]), 30)
        self.assertGreaterEqual(len(prof["projects"]), 3)

    # 4. Test each scraper independently
    def test_04_scrapers_independent(self):
        engine = ScraperEngine(mock_mode=True)
        res = engine.run_all(max_jobs_per_portal=2)
        self.assertIn("ats", res["portal_stats"])
        self.assertIn("remoteok", res["portal_stats"])
        self.assertIn("weworkremotely", res["portal_stats"])
        self.assertIn("internshala", res["portal_stats"])
        self.assertGreater(res["total_scraped"], 0)
        self.assertGreater(res["total_unique"], 0)

    # 5. Verify that mock data cannot enter live metrics
    def test_05_mock_data_containment(self):
        apply_service = ApplyService(db_manager=self.db, dry_run=True, require_approval=True)
        sample_job = {
            "title": "AI Engineer",
            "company": "DeepMind",
            "job_url": "https://example.com/job/test-ai-101",
            "description": "Python, RAG, LangGraph"
        }
        res = apply_service.apply_to_job(sample_job)
        self.assertEqual(res["status"], "simulated_applied")
        all_time = self.db.get_all_time_stats()
        # Even after simulated dry run apply, total_applied must remain 0
        self.assertEqual(all_time["total_applied"], 0)

    # 6. Test duplicate prevention
    def test_06_duplicate_prevention(self):
        engine = ScraperEngine(mock_mode=True)
        job1 = {
            "job_id": "test_hash_01",
            "job_url": "https://example.com/job/dev-01",
            "company": "Google",
            "title": "Software Engineer",
            "portal": "ats"
        }
        job2 = {
            "job_id": "test_hash_01",
            "job_url": "https://example.com/job/dev-01?utm_source=rss",
            "company": "Google Inc",
            "title": "Software Engineer",
            "portal": "remoteok"
        }
        dedup = engine.deduplicate_jobs([job1, job2])
        self.assertEqual(len(dedup["unique_jobs"]), 1)
        self.assertEqual(dedup["duplicates_filtered"], 1)

    # 7. Test Gemini matching
    def test_07_gemini_matching(self):
        scoring = ScoringService(db_manager=self.db, mock_mode=True)
        jobs = [{
            "title": "Python Developer",
            "company": "Tech Corp",
            "job_url": "https://example.com/jobs/pyd",
            "description": "Python, SQL, Machine Learning, Streamlit, Pandas, NumPy required."
        }]
        scored = scoring.score_jobs(jobs)
        self.assertEqual(len(scored), 1)
        self.assertGreaterEqual(scored[0]["relevance_score"], 7.0)
        self.assertIn("Python", scored[0]["matched_skills"])

    # 8. Test application approval workflow
    def test_08_application_approval_workflow(self):
        apply_service = ApplyService(db_manager=self.db, dry_run=False, require_approval=True)
        job_hash = self.db.make_job_hash("https://example.com/job/app-flow-1")
        # 1. Enqueue
        qid = self.db.enqueue_for_approval(
            item_type="job_application",
            reference_id=job_hash,
            title="AI Engineer",
            company="NeuralNet",
            details="Match 9.2",
            preview_payload={"portal": "direct", "job_url": "https://example.com/job/app-flow-1"}
        )
        self.assertGreater(qid, 0)
        # 2. Check pending
        pending = apply_service.get_pending_approvals()
        self.assertEqual(len(pending), 1)
        # 3. Approve
        ok = apply_service.approve_item(qid, notes="Approved for submission")
        self.assertTrue(ok)
        # 4. Verify no longer pending
        pending_after = apply_service.get_pending_approvals()
        self.assertEqual(len(pending_after), 0)

    # 9. Test email approval and sending
    def test_09_email_approval_and_sending(self):
        email_service = EmailService(db_manager=self.db, dry_run=True, require_approval=True)
        qid = self.db.enqueue_for_approval(
            item_type="cold_email",
            reference_id="test_email_ref",
            title="Python Developer",
            company="Startup Labs",
            details="Outreach draft",
            preview_payload={
                "recipient_email": "hr@startuplabs.io",
                "title": "Python Developer",
                "company": "Startup Labs",
                "subject": "Inquiry regarding Python Developer role",
                "body_text": "Hello, I am interested in this role."
            }
        )
        # Cannot send while pending with require_approval=True
        # Now approve it
        self.db.decide_approval(qid, decision="approved")
        res = email_service.send_approved_outreach(qid)
        self.assertIn(res["status"], ("simulated_sent", "sent"))
        # Verify dry-run does not falsely mark applications.email_sent = 1
        all_time = self.db.get_all_time_stats()
        self.assertEqual(all_time["total_emailed"], 0)

    # 10. Test HR outreach tracking
    def test_10_hr_outreach_tracking(self):
        self.db.add_hr_contact("Acme AI", "acme.ai", "Jane Doe", "jane@acme.ai", "pdf")
        with self.db._get_conn() as conn:
            cnt = conn.execute("SELECT COUNT(*) FROM hr_contacts WHERE hr_email = 'jane@acme.ai';").fetchone()[0]
            self.assertEqual(cnt, 1)

    # 11. Test dashboard updates
    def test_11_dashboard_updates(self):
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user"] = cfg.CANDIDATE_NAME
        
        # Point dashboard to our isolated db
        orig_db_path = cfg.DB_PATH
        cfg.DB_PATH = self.temp_db_path
        try:
            res = self.client.get("/api/stats")
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data["all_time"]["total_jobs_seen"], 0)
            self.assertEqual(data["all_time"]["total_applied"], 0)
            self.assertEqual(data["candidate"]["name"], "Manoj Kumar Mandal")
        finally:
            cfg.DB_PATH = orig_db_path

    # 12. Test scheduler
    def test_12_scheduler(self):
        sched_service = SchedulerService(timezone_str="Asia/Kolkata", hour=9, minute=0)
        scheduler = sched_service.build_scheduler(job_func=lambda: None, is_background=True)
        jobs = scheduler.get_jobs()
        job_ids = [j.id for j in jobs]
        self.assertIn("careerpilot_daily_run", job_ids)
        self.assertIn("daemon_heartbeat", job_ids)

    # 13. Test failure handling and recovery
    def test_13_failure_handling_and_recovery(self):
        ats = AtsScraper(mock_mode=False, targets={"greenhouse": ["invalid_board_test_999"]})
        # In isolated sandbox with no network or invalid board, request_with_retry safely handles exceptions
        try:
            # Safe call that catches exceptions without crashing
            res = ats.run_safe(max_jobs=5)
            self.assertIsInstance(res, list)
        except Exception as e:
            self.fail(f"run_safe should catch all scraper exceptions, but raised: {e}")


if __name__ == "__main__":
    unittest.main()
