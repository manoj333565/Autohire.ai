"""
tests/test_report_service.py — Test Suite for Milestone 13 Daily HTML Report Engine (F9)
========================================================================================
Validates:
1. Daily metrics & pipeline funnel conversion rate calculations
2. 7-day metric comparison & Day-over-Day (DoD) trend computations
3. Inbox intelligence & 3-layer recruiter response categorization
4. Responsive HTML email template structure, branding & styling
5. Report artifact disk persistence (HTML & JSON files in logs/reports/)
6. Relational database audit logging in `email_history` table
7. Safe dry-run email dispatch via EmailSender
8. LangGraph `generate_report_node` integration
"""

import os
import sys
import json
import unittest
from datetime import datetime
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.report_service import ReportService
from agent.nodes.report_node import generate_report_node


class TestReportService(unittest.TestCase):
    """Test suite for ReportService and Daily HTML Reporting Engine."""

    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager(cfg.DB_PATH)
        cls.report_service = ReportService(db_manager=cls.db, dry_run=True)

    # ── 1. Daily Metrics & Pipeline Funnel ─────────────────────────────────────
    def test_daily_metrics_and_funnel_calculations(self):
        """Test daily pipeline metrics, conversion rates, and drip progress."""
        mock_state = {
            "run_id": "test_report_run_01",
            "raw_jobs": [{"url": f"https://job.test/{i}"} for i in range(20)],
            "filtered_jobs": [{"url": f"https://job.test/{i}"} for i in range(10)],
            "scored_jobs": [{"url": f"https://job.test/{i}"} for i in range(8)],
            "approval_items": [{"id": i} for i in range(6)],
            "total_applied": 4,
            "total_emailed": 5,
            "pdf_drip_sent": [{"email": f"hr{i}@test.com"} for i in range(12)],
            "errors": [],
            "duration_seconds": 14.5
        }

        metrics = self.report_service.compile_daily_metrics(state=mock_state, date_str="2026-11-17")

        self.assertEqual(metrics["date"], "2026-11-17")
        self.assertEqual(metrics["today"]["scraped"], 20)
        self.assertEqual(metrics["today"]["filtered"], 10)
        self.assertEqual(metrics["today"]["scored"], 8)
        self.assertEqual(metrics["today"]["staged"], 6)
        self.assertEqual(metrics["today"]["applied"], 4)
        self.assertEqual(metrics["today"]["emailed"], 5)
        self.assertEqual(metrics["today"]["pdf_drip_sent"], 12)

        # Conversion rate math verification
        # Filter pass rate: 10 / 20 = 50.0%
        self.assertEqual(metrics["conversion_rates"]["filter_pass_pct"], 50.0)
        # Scoring pass rate: 8 / 10 = 80.0%
        self.assertEqual(metrics["conversion_rates"]["scoring_pass_pct"], 80.0)
        # Approval stage rate: 6 / 8 = 75.0%
        self.assertEqual(metrics["conversion_rates"]["approval_stage_pct"], 75.0)

        # Drip metrics
        self.assertGreaterEqual(metrics["drip_campaign"]["total_contacts"], 0)
        self.assertEqual(metrics["drip_campaign"]["daily_limit"], cfg.DAILY_DRIP_LIMIT)

    # ── 2. 7-Day Performance & Trend Comparison ───────────────────────────────
    def test_7day_comparison_computation(self):
        """Test 7-day trend history and Day-over-Day growth rates."""
        trends = self.report_service.compile_7day_comparison(days=7)

        self.assertIn("daily_records", trends)
        self.assertIn("totals", trends)
        self.assertIn("averages", trends)
        self.assertIn("dod_applied_growth_pct", trends)

        self.assertGreaterEqual(trends["period_days"], 1)
        self.assertIsInstance(trends["daily_records"], list)
        self.assertIn("applied_per_day", trends["averages"])
        self.assertIn("emailed_per_day", trends["averages"])

    # ── 3. Inbox Intelligence & Recruiter Categorization ───────────────────────
    def test_inbox_intelligence_categorization(self):
        """Test scanning career inbox and classifying recruiter responses."""
        inbox = self.report_service.compile_inbox_intelligence(days_back=3)

        self.assertIn("total_inspected", inbox)
        self.assertIn("bounce_count", inbox)
        self.assertIn("auto_replies_count", inbox)
        self.assertIn("rejections_count", inbox)
        self.assertIn("positive_replies", inbox)

        # Verify positive replies structure
        self.assertIsInstance(inbox["positive_replies"], list)
        if inbox["positive_replies"]:
            r = inbox["positive_replies"][0]
            self.assertIn("sender", r)
            self.assertIn("subject", r)
            self.assertIn("snippet", r)

    # ── 4. Responsive HTML Email Rendering & Branding ─────────────────────────
    def test_render_html_report_branding_and_structure(self):
        """Test HTML rendering contains candidate identity, royal blue styling, and sections."""
        mock_payload = {
            "date": "2026-11-17",
            "dry_run": True,
            "metrics": {
                "date": "2026-11-17",
                "today": {"applied": 5, "emailed": 7, "scraped": 25, "filtered": 15, "scored": 12, "staged": 8},
                "conversion_rates": {"filter_pass_pct": 60.0, "scoring_pass_pct": 80.0, "approval_stage_pct": 66.7, "apply_conversion_pct": 62.5},
                "all_time": {"total_applied": 150, "total_emailed": 120, "unique_companies": 85},
                "drip_campaign": {"total_contacts": 1831, "sent_total": 450, "sent_today": 35, "remaining": 1381, "percentage_complete": 24.6},
                "approval_queue": {"pending": 3}
            },
            "trend_7d": {
                "daily_records": [
                    {"date": "2026-11-16", "applied": 4, "emailed": 5, "scraped": 20},
                    {"date": "2026-11-17", "applied": 5, "emailed": 7, "scraped": 25}
                ],
                "averages": {"applied_per_day": 4.5}
            },
            "inbox": {
                "bounce_count": 1,
                "positive_replies": [
                    {
                        "sender": "tech-recruiting@deepmind.com",
                        "subject": "Interview for AI Engineer - Next Steps",
                        "date": "Tue, 17 Nov 2026 10:15:00 +0530",
                        "snippet": "We reviewed Manoj's profile and would love to schedule a screening interview."
                    }
                ]
            }
        }

        html = self.report_service.render_html_report(mock_payload)

        # 1. Candidate branding & identity check
        self.assertIn(cfg.CANDIDATE_NAME, html)
        self.assertIn(cfg.PERSONAL_EMAIL, html)
        self.assertNotIn("Debashis", html)

        # 2. Design & Royal Blue palette check
        self.assertIn("#1A56DB", html)
        self.assertIn("SAFE DRY-RUN MODE", html)

        # 3. Structural section checks
        self.assertIn("Daily Executive Run Report", html)
        self.assertIn("Applied Today", html)
        self.assertIn("Daily Multi-Stage Pipeline Funnel", html)
        self.assertIn("Recruiter Interview Responses", html)
        self.assertIn("tech-recruiting@deepmind.com", html)
        self.assertIn("7-Day Performance & Activity Trends", html)
        self.assertIn("1,831 Verified Recruiter Drip Pipeline", html)

        self.assertIn("Human Approval Required", html)

    # ── 5. Report Artifact Disk Persistence ───────────────────────────────────
    def test_report_artifact_disk_persistence(self):
        """Test HTML and JSON report files are written to logs/reports/."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        result = self.report_service.generate_daily_report(
            date_str=today_str,
            send_email=False,
            dry_run=True
        )

        self.assertEqual(result["status"], "success")
        self.assertTrue(Path(result["html_path"]).exists())
        self.assertTrue(Path(result["json_path"]).exists())

        # Verify HTML file is non-empty
        with open(result["html_path"], "r", encoding="utf-8") as f:
            content = f.read()
            self.assertIn(cfg.CANDIDATE_NAME, content)
            self.assertIn("<!DOCTYPE html>", content)

        # Verify JSON file structure
        with open(result["json_path"], "r", encoding="utf-8") as f:
            data = json.load(f)
            self.assertEqual(data["candidate"]["name"], cfg.CANDIDATE_NAME)
            self.assertIn("metrics", data)

    # ── 6. Database Audit Logging in email_history ────────────────────────────
    def test_email_history_audit_logging(self):
        """Test daily report record is logged in SQLite email_history table."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        self.report_service.generate_daily_report(
            date_str=today_str,
            send_email=False,
            dry_run=True
        )

        with self.db._get_conn() as conn:
            row = conn.execute("""
                SELECT * FROM email_history 
                WHERE email_type = 'daily_report' AND job_hash = ?
                ORDER BY id DESC LIMIT 1
            """, (f"report_{today_str}",)).fetchone()

            self.assertIsNotNone(row)
            self.assertEqual(row["recipient_email"], cfg.PERSONAL_EMAIL)
            self.assertEqual(row["recipient_name"], cfg.CANDIDATE_NAME)
            self.assertEqual(row["email_type"], "daily_report")
            self.assertEqual(row["status"], "dry_run")

    # ── 7. Safe Dry-Run Email Dispatch ────────────────────────────────────────
    def test_dry_run_email_dispatch(self):
        """Test safe dry-run email dispatch simulates successfully without error."""
        today_str = datetime.now().strftime("%Y-%m-%d")
        result = self.report_service.generate_daily_report(
            date_str=today_str,
            send_email=True,
            dry_run=True
        )
        self.assertEqual(result["status"], "success")
        self.assertTrue(result["email_dispatched"])

    # ── 8. LangGraph Node Integration ─────────────────────────────────────────
    def test_langgraph_report_node_integration(self):
        """Test LangGraph generate_report_node invokes ReportService and records output."""
        mock_state = {
            "run_id": "test_m13_node_run",
            "run_date": datetime.now().strftime("%Y-%m-%d"),
            "dry_run": True,
            "require_human_approval": True,
            "start_time": 1000.0,
            "session_memory": {},
            "raw_jobs": [{"url": "https://job.test/1"}, {"url": "https://job.test/2"}],
            "filtered_jobs": [{"url": "https://job.test/1"}],
            "rejected_jobs": [{"url": "https://job.test/2"}],
            "scored_jobs": [{"url": "https://job.test/1", "relevance_score": 8.5}],
            "staged_approvals": [],
            "approved_jobs": [],
            "applied_jobs": [],
            "emailed_jobs": [],
            "pdf_drip_contacts": [],
            "pdf_drip_sent": [],
            "stats": {},
            "logs": ["Initial log entry"],
            "errors": [],
            "report_summary": {},
            "report_html": None
        }

        output = generate_report_node(mock_state)

        self.assertIn("report_summary", output)
        self.assertIn("report_html", output)
        self.assertIn("logs", output)

        self.assertEqual(output["report_summary"]["run_id"], "test_m13_node_run")
        self.assertEqual(output["report_summary"]["candidate"], cfg.CANDIDATE_NAME)
        self.assertTrue(len(output["report_html"]) > 100)
        self.assertIn(cfg.CANDIDATE_NAME, output["report_html"])


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  🧪 EXECUTING CAREERPILOT AI MILESTONE 13 DAILY REPORT TESTS")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestReportService)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n" + "=" * 70)
        print("  🎉 ALL 8 DAILY HTML REPORT ENGINE TESTS PASSED SUCCESSFULLY (100%)!")
        print("=" * 70 + "\n")
        sys.exit(0)
    else:
        print("\n❌ SOME REPORT SERVICE TESTS FAILED.")
        sys.exit(1)
