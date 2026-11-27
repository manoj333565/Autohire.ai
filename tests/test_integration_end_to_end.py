"""
tests/test_integration_end_to_end.py — Master End-to-End Autonomous Lifecycle Verification
========================================================================================
Comprehensive system test validating all 15 milestones of CareerPilot AI / Autohire.ai:
  1. Candidate Profile & 3-Tier Verified Skills (Manoj Kumar Mandal)
  2. Multi-Portal Scrapers (RemoteOK, WeWorkRemotely, Internshala, Wellfound, LinkedIn, Naukri)
  3. 7-Stage Filter, Deduplication & NLP Skills-Gap Analysis
  4. LLM Job Scoring Engine (Structured Gemini Flash JSON / Heuristic Fallback)
  5. Mandatory Human Approval Safety Gate Staging & Deciding
  6. Multi-Portal Application Submission Engine (Dry-Run Double-Apply Prevention)
  7. Recruiter Discovery & Cold Outreach Engine
  8. 1,831 PDF HR Contacts Drip Campaign (50/day quota enforcement)
  9. Cross-Session Memory Store (SQLite + Mem0 disk cache)
  10. Executive Daily HTML Report Generation & Disk Persistence
  11. Real-Time SaaS Monitoring Dashboard REST API & Security Headers
  12. Master LangGraph Multi-Agent Workflow State Machine Execution
"""

import os
import sys
import json
import uuid
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService
from scrapers.scraper_engine import ScraperEngine
from services.filter_service import FilterService
from services.scoring_service import ScoringService
from services.apply_service import ApplyService
from services.email_service import EmailService
from services.memory_service import MemoryService
from services.report_service import ReportService
from dashboard.app import app
from agent.graph import build_job_agent_graph, get_initial_state


class TestIntegrationEndToEnd(unittest.TestCase):
    """Master Integration Test Suite covering the complete autonomous lifecycle."""

    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager(cfg.DB_PATH)
        cls.profile_service = ProfileService(cls.db)
        cls.filter_service = FilterService(cls.db)
        cls.scoring_service = ScoringService(cls.db, mock_mode=True)
        cls.apply_service = ApplyService(cls.db, dry_run=True, require_approval=True)
        cls.email_service = EmailService(cls.db, dry_run=True, require_approval=True)
        cls.memory_service = MemoryService(cls.db)
        cls.report_service = ReportService(cls.db, dry_run=True)

        app.config["TESTING"] = True
        cls.client = app.test_client()

    # ── 1. Candidate Profile & 3-Tier Verified Skills ─────────────────────────
    def test_01_candidate_profile_and_skills(self):
        """Verify candidate identity is strictly Manoj Kumar Mandal with 3 verified skill tiers."""
        profile = self.profile_service.get_full_profile(profile_id=1)
        self.assertEqual(profile.get("name"), "Manoj Kumar Mandal")
        self.assertEqual(profile.get("email"), "manojkumarmandal1111@gmail.com")
        self.assertIn("B.Tech", profile.get("education_degree", ""))
        self.assertIn("Computer Science", profile.get("education_degree", ""))

        tiers = profile.get("skills_by_tier", {})
        self.assertIn("Proficient", tiers)
        self.assertIn("Intermediate", tiers)
        self.assertIn("Learning", tiers)

        proficient_names = [s["skill_name"] for s in tiers["Proficient"]]
        self.assertGreaterEqual(len(proficient_names), 3)

        context = self.profile_service.get_candidate_context_for_scoring()
        context_str = json.dumps(context)
        self.assertIn("Manoj Kumar Mandal", context_str)
        self.assertIn("Proficient", context_str)

    # ── 2. Multi-Portal Scrapers Aggregation ──────────────────────────────────
    def test_02_multi_portal_scrapers(self):
        """Verify scraper engine aggregates normalized jobs across all 6 portals."""
        engine = ScraperEngine(mock_mode=True)
        results = engine.run_all(max_jobs_per_portal=3)

        self.assertIn("total_scraped", results)
        self.assertGreaterEqual(results["total_scraped"], 12)
        jobs = results.get("raw_jobs", [])
        self.assertGreaterEqual(len(jobs), 12)

        portals_observed = {j["portal"] for j in jobs}
        expected_portals = {"remoteok", "weworkremotely", "internshala", "wellfound", "linkedin", "naukri"}
        self.assertTrue(expected_portals.issubset(portals_observed))

        # Check normalization
        first_job = jobs[0]
        for field in ("title", "company", "url", "portal", "location", "description"):
            self.assertIn(field, first_job)
            self.assertTrue(bool(first_job[field]))

    # ── 3. 7-Stage Filtering, Deduplication & NLP Skills-Gap ──────────────────
    def test_03_filter_deduplication_and_skills_gap(self):
        """Verify 7-stage filter eliminates unwanted tech, deduplicates, and generates skills-gap report."""
        test_jobs = [
            {"title": "AI Engineer", "company": "Tech Innovations", "url": "https://filter.e2e/job1", "job_url": "https://filter.e2e/job1", "portal": "direct", "description": "Python, PyTorch, RAG architectures required."},
            {"title": "Java Spring Backend Developer", "company": "Legacy Corp", "url": "https://filter.e2e/job2", "job_url": "https://filter.e2e/job2", "portal": "direct", "description": "Enterprise Java, Spring Boot, Hibernate."},
            {"title": "Principal Architect 12+ years", "company": "Mega Corp", "url": "https://filter.e2e/job3", "job_url": "https://filter.e2e/job3", "portal": "direct", "description": "15+ years experience required."},
            {"title": "Machine Learning Engineer", "company": "AI Labs", "url": "https://filter.e2e/job4", "job_url": "https://filter.e2e/job4", "portal": "direct", "description": "Python, FastAPI, Agentic AI pipelines."}
        ]

        passed, rejected = self.filter_service.run_pipeline(test_jobs)

        passed_urls = [j.get("url") or j.get("job_url") for j in passed]
        self.assertIn("https://filter.e2e/job1", passed_urls)
        self.assertIn("https://filter.e2e/job4", passed_urls)
        self.assertNotIn("https://filter.e2e/job2", passed_urls)
        self.assertNotIn("https://filter.e2e/job3", passed_urls)

        # NLP Skills-Gap report attached
        if passed:
            self.assertIn("skills_gap_report", passed[0])

    # ── 4. LLM Job Scoring Engine (Structured Schema) ─────────────────────────
    def test_04_scoring_engine_and_threshold(self):
        """Verify LLM scoring computes structured relevance scores with proficiency weighting."""
        candidate_context = self.profile_service.get_candidate_context_for_scoring()
        test_job = {
            "title": "Agentic AI & LLM Systems Engineer",
            "company": "DeepMind Partner",
            "url": "https://scoring.e2e/test_job",
            "portal": "direct",
            "description": "Looking for Python AI engineer proficient in PyTorch, LangGraph, RAG, and Agentic AI."
        }

        score_res = self.scoring_service.score_job(test_job, candidate_context)
        self.assertIn("relevance_score", score_res)
        self.assertGreaterEqual(score_res["relevance_score"], 6.5)
        self.assertIn("matched_skills", score_res)
        self.assertIn("reasoning", score_res)
        self.assertTrue(score_res["relevance_score"] >= cfg.MATCH_SCORE_THRESHOLD)

    # ── 5. Mandatory Human Approval Safety Gate ───────────────────────────────
    def test_05_approval_safety_gate(self):
        """Verify candidate submissions and emails stage into approval_queue for human gate."""
        queue_id = self.db.enqueue_for_approval(
            item_type="job_application",
            reference_id="e2e_approval_ref_01",
            title="Lead AI Engineer",
            company="Anthropic Ecosystem",
            details="Pitch for Manoj Kumar Mandal.",
            preview_payload={"portal": "direct", "score": 9.2},
            score=9.2
        )
        self.assertGreater(queue_id, 0)

        # Verify queued item is pending
        pending = self.db.get_approval_queue(status="pending")
        matched = [i for i in pending if i["id"] == queue_id]
        self.assertEqual(len(matched), 1)

        # Decide approval
        ok = self.db.decide_approval(queue_id=queue_id, decision="approved", notes="E2E test approval")
        self.assertTrue(ok)

    # ── 6. Automated Application Engine (Multi-Portal & Dry-Run) ──────────────
    def test_06_automated_apply_service(self):
        """Verify form mapping, portal dispatchers, and double-apply prevention."""
        unique_url = f"https://apply.e2e/test_job_{uuid.uuid4().hex[:8]}"
        test_job = {
            "title": "Machine Learning Engineer",
            "company": "Vision AI",
            "url": unique_url,
            "job_url": unique_url,
            "portal": "remoteok",
            "location": "Remote",
            "relevance_score": 8.5
        }

        res = self.apply_service.apply_to_job(test_job)
        self.assertIn(res["status"], ("applied", "simulated_applied"))
        self.assertTrue(res["dry_run"])

        # Double apply prevention check
        job_hash = self.db.make_job_hash(test_job["url"])
        self.db.add_job(test_job)
        self.db.update_application_status(job_hash, status="applied", notes="Applied in test")

        can_apply, reason = self.apply_service.can_apply(test_job)
        self.assertFalse(can_apply)
        self.assertIn("REJECT_ALREADY_APPLIED", reason)

        second_res = self.apply_service.apply_to_job(test_job)
        self.assertEqual(second_res["status"], "blocked")
        self.assertIn("REJECT_ALREADY_APPLIED", second_res["reason"])

    # ── 7. Recruiter Discovery & Cold Outreach Engine ─────────────────────────
    def test_07_recruiter_cold_outreach(self):
        """Verify waterfall HR email discovery and safe cold email dispatch."""
        job_info = {
            "title": "AI/ML Engineer",
            "company": "ScaleAI Inc",
            "url": "https://outreach.e2e/job",
            "portal": "direct"
        }

        # Discover recruiter
        recruiter = self.email_service.discover_recruiter("ScaleAI Inc", job_info["url"])
        self.assertIn("email", recruiter)

        # Stage outreach for approval
        queue_id = self.email_service.stage_outreach_for_approval(job_info, score=8.5)
        self.assertIsNotNone(queue_id)
        self.assertGreater(queue_id, 0)

    # ── 8. HR Contacts Drip Campaign ──────────────────────────────────────────
    def test_08_pdf_hr_drip_pipeline(self):
        """Verify daily 50-contact batch extraction from HR contacts."""
        batch = self.db.get_pdf_hr_batch(limit=cfg.DAILY_DRIP_LIMIT)
        if not batch:
            with self.db._get_conn() as conn:
                conn.execute(
                    "INSERT OR IGNORE INTO pdf_hr_outreach (email, name, company, designation, status) VALUES (?, ?, ?, ?, 'pending')",
                    ("test_recruiter@tech.corp", "Test Recruiter", "Tech Corp", "Talent Acquisition")
                )
            batch = self.db.get_pdf_hr_batch(limit=cfg.DAILY_DRIP_LIMIT)

        self.assertGreater(len(batch), 0)
        self.assertLessEqual(len(batch), 50)

        first_hr = batch[0]
        self.assertIn("email", first_hr)
        self.assertIn("company", first_hr)

        # Mark sent and verify stats
        self.db.mark_pdf_hr_sent(first_hr["email"], status="sent")
        stats = self.db.get_pdf_hr_stats()
        self.assertGreater(stats["total"], 0)
        self.assertGreaterEqual(stats["sent"], 1)

    # ── 9. Cross-Session Memory Store ─────────────────────────────────────────
    def test_09_cross_session_memory(self):
        """Verify persistent memory store and contextual keyword retrieval."""
        self.memory_service.remember(
            key="candidate_identity",
            content="Manoj Kumar Mandal - AI/ML & Agentic AI Engineer",
            category="candidate"
        )
        item = self.memory_service.recall("candidate_identity")
        self.assertIsNotNone(item)
        self.assertIn("Manoj Kumar Mandal", item["content"])

        self.memory_service.record_company_interaction(
            company="DeepMind Technologies",
            action="application_submitted",
            outcome="success",
            notes="Applied for AI Research Engineer role."
        )

        history = self.memory_service.get_company_interaction("DeepMind Technologies")
        self.assertGreaterEqual(len(history), 1)

        mem_context = self.memory_service.get_system_context()
        self.assertIn("all_time_stats", mem_context)
        self.assertIn("daily_quota_remaining", mem_context)

    # ── 10. Executive Daily HTML Report Generation ────────────────────────────
    def test_10_daily_html_report_engine(self):
        """Verify daily HTML report is generated, formatted with royal blue theme, and saved to disk."""
        today_str = "2026-11-18"
        report_res = self.report_service.generate_daily_report(
            date_str=today_str,
            send_email=False,
            dry_run=True
        )

        self.assertEqual(report_res["status"], "success")
        self.assertTrue(Path(report_res["html_path"]).exists())
        self.assertTrue(Path(report_res["json_path"]).exists())

        html = report_res["html_content"]
        self.assertIn(cfg.CANDIDATE_NAME, html)
        self.assertIn("#1A56DB", html)
        self.assertIn("Daily Multi-Stage Pipeline Funnel", html)

    # ── 11. SaaS Dashboard API & Security Headers ─────────────────────────────
    def test_11_dashboard_api_and_security(self):
        """Verify dashboard authentication, stats API, and HTTP security headers."""
        # 1. Health check
        resp_health = self.client.get("/health")
        self.assertEqual(resp_health.status_code, 200)
        self.assertEqual(resp_health.headers.get("X-Content-Type-Options"), "nosniff")

        # 2. Login
        resp_login = self.client.post("/login", json={"password": cfg.DASHBOARD_PASSWORD})
        self.assertEqual(resp_login.status_code, 200)

        # 3. Authenticated stats API
        resp_stats = self.client.get("/api/stats")
        self.assertEqual(resp_stats.status_code, 200)
        data = resp_stats.get_json()
        self.assertEqual(data["candidate"]["name"], cfg.CANDIDATE_NAME)
        self.assertIn("all_time", data)

    # ── 12. Full LangGraph Multi-Agent Workflow State Machine ─────────────────
    def test_12_langgraph_workflow_orchestration(self):
        """Verify complete LangGraph StateGraph executes from START to END cleanly."""
        agent = build_job_agent_graph()
        initial_state = get_initial_state(dry_run=True)

        final_state = agent.invoke(initial_state)

        self.assertIn("report_summary", final_state)
        self.assertIn("logs", final_state)
        self.assertEqual(final_state.get("run_date"), initial_state.get("run_date"))

        # Verify summary metrics
        metrics = final_state.get("report_summary", {}).get("metrics", {})
        self.assertIn("scraped", metrics)
        self.assertIn("applied", metrics)


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  🚀 EXECUTING CAREERPILOT AI MASTER END-TO-END VERIFICATION")
    print("=" * 72)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestIntegrationEndToEnd)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n" + "=" * 72)
        print("  🎉 MASTER END-TO-END INTEGRATION TEST PASSED SUCCESSFULLY (12/12)!")
        print("=" * 72 + "\n")
        sys.exit(0)
    else:
        print("\n❌ MASTER END-TO-END TEST ENCOUNTERED FAILURES.")
        sys.exit(1)
