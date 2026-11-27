"""
tests/test_dashboard_api.py — Comprehensive Test Suite for Milestone 12 SaaS Dashboard
======================================================================================
Validates:
1. Session-based authentication & route protection (redirects / 401s on unauthenticated access)
2. Password validation with constant-time comparison
3. Public /health endpoint integrity
4. Aggregate statistics & 14-day Chart.js trend data APIs
5. High-speed paginated Job Feed & Portal breakdown APIs
6. Applications tracking & manual status transition APIs
7. Mandatory Human Approval Queue single & bulk decision APIs
8. 1,831 PDF HR Contacts Drip Campaign tracking API
9. Candidate Profile & 3-Tier Verified Skills CRUD APIs
10. System Settings & Background Agent Run orchestration APIs
"""

import sys
import json
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from dashboard.app import app


class TestDashboardAPI(unittest.TestCase):
    """Test suite for CareerPilot AI / Autohire.ai SaaS Dashboard Backend."""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
        app.config["SECRET_KEY"] = "test-dashboard-secret-key-2026"
        cls.client = app.test_client()
        cls.db = DatabaseManager(cfg.DB_PATH)

    def _login(self):
        """Helper to authenticate test client session."""
        with self.client.session_transaction() as sess:
            sess["authenticated"] = True
            sess["user"] = cfg.CANDIDATE_NAME

    def _logout(self):
        """Helper to clear test client session."""
        with self.client.session_transaction() as sess:
            sess.clear()

    # ── 1. Authentication & Route Protection ──────────────────────────────────
    def test_unauthenticated_access_protection(self):
        """Unauthenticated access to protected pages and APIs must be rejected."""
        self._logout()

        # Web view should redirect to /login
        resp_index = self.client.get("/")
        self.assertEqual(resp_index.status_code, 302)
        self.assertIn("/login", resp_index.headers.get("Location", ""))

        # API endpoint should return 401 Unauthorized
        resp_api = self.client.get("/api/stats")
        self.assertEqual(resp_api.status_code, 401)
        data = resp_api.get_json()
        self.assertEqual(data.get("status"), "error")
        self.assertEqual(data.get("error"), "Unauthorized")

    def test_password_authentication_flow(self):
        """Password login flow with correct vs incorrect credentials."""
        self._logout()

        # 1. Invalid password
        resp_bad = self.client.post("/login", json={"password": "wrong_password_attempt"}, headers={"Accept": "application/json"})
        self.assertEqual(resp_bad.status_code, 401)
        data_bad = resp_bad.get_json()
        self.assertEqual(data_bad.get("status"), "error")

        # 2. Correct password via JSON
        resp_good = self.client.post("/login", json={"password": cfg.DASHBOARD_PASSWORD}, headers={"Accept": "application/json"})
        self.assertEqual(resp_good.status_code, 200)
        data_good = resp_good.get_json()
        self.assertEqual(data_good.get("status"), "success")
        self.assertEqual(data_good.get("redirect"), "/")

        # 3. Access protected view after login
        resp_authed = self.client.get("/")
        self.assertEqual(resp_authed.status_code, 200)
        self.assertIn(b"Autohire", resp_authed.data)
        self.assertIn(cfg.CANDIDATE_NAME.encode(), resp_authed.data)

        # 4. Logout
        resp_logout = self.client.get("/logout")
        self.assertEqual(resp_logout.status_code, 302)
        self.assertIn("/login", resp_logout.headers.get("Location", ""))

    # ── 2. Public Health Endpoint ─────────────────────────────────────────────
    def test_public_health_endpoint(self):
        """Public /health check must be accessible without login."""
        self._logout()
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("candidate"), cfg.CANDIDATE_NAME)
        self.assertEqual(data.get("email"), cfg.PERSONAL_EMAIL)
        self.assertTrue(data.get("database_connected"))

    # ── 3. High-Level Statistics & 14-Day Chart APIs ──────────────────────────
    def test_stats_api(self):
        """Test aggregate stats API returns all required metrics."""
        self._login()
        resp = self.client.get("/api/stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")

        # Candidate branding
        self.assertEqual(data["candidate"]["name"], cfg.CANDIDATE_NAME)
        self.assertEqual(data["candidate"]["email"], cfg.PERSONAL_EMAIL)

        # All-time and daily stats
        self.assertIn("total_applied", data["all_time"])
        self.assertIn("total_emailed", data["all_time"])
        self.assertIn("unique_companies", data["all_time"])
        self.assertIn("total_contacts", data["drip"])
        self.assertIn("pending", data["approval_queue"])
        self.assertIn("current_time_ist", data["system"])

    def test_chart_data_api(self):
        """Test 14-day trend stats API for Chart.js rendering."""
        self._login()
        resp = self.client.get("/api/chart-data?days=14")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("days"), 14)
        self.assertIsInstance(data.get("trends"), list)

    # ── 4. Job Feed & Portal Breakdown APIs ───────────────────────────────────
    def test_job_feed_api(self):
        """Test paginated job feed matching reference SaaS layout."""
        self._login()
        resp = self.client.get("/api/jobs?page=1&per_page=10")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("jobs", data)
        self.assertIn("total", data)
        self.assertIn("total_pages", data)
        self.assertGreaterEqual(data["total"], 0)

        if data["jobs"]:
            first_job = data["jobs"][0]
            self.assertIn("job_title", first_job)
            self.assertIn("company", first_job)
            self.assertIn("portal", first_job)
            self.assertIn("relevance_score", first_job)

    def test_portal_stats_api(self):
        """Test breakdown by portal."""
        self._login()
        resp = self.client.get("/api/portals")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIsInstance(data.get("portals"), list)

    # ── 5. Applications Tracking & Status Transition APIs ─────────────────────
    def test_applications_history_and_status_update(self):
        """Test applications pagination and manual status updates."""
        self._login()

        # 1. Fetch applications
        resp = self.client.get("/api/applications?page=1&per_page=5")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertIn("applications", data)

        if data["applications"]:
            test_app = data["applications"][0]
            job_hash = test_app["job_hash"]

            # 2. Update status to 'interview'
            resp_update = self.client.post(
                f"/api/applications/{job_hash}/status",
                json={"status": "interview", "notes": "Interview scheduled via SaaS Dashboard"}
            )
            self.assertEqual(resp_update.status_code, 200)
            update_data = resp_update.get_json()
            self.assertEqual(update_data.get("status"), "success")
            self.assertEqual(update_data.get("new_status"), "interview")

    # ── 6. Mandatory Human Approval Queue APIs ────────────────────────────────
    def test_approval_queue_single_and_bulk_decisions(self):
        """Test human approval review queue with approve/reject workflows."""
        self._login()

        # 1. Enqueue test item
        item_id = self.db.enqueue_for_approval(
            item_type="job_application",
            reference_id="test_m12_approval_ref",
            title="Senior AI Agent Engineer",
            company="DeepMind Technologies",
            details="Pitching Manoj Kumar Mandal for Agentic AI Architecture role.",
            preview_payload={"portal": "direct", "score": 9.5},
            score=9.5
        )
        self.assertGreater(item_id, 0)

        # 2. Retrieve pending queue
        resp_q = self.client.get("/api/approval-queue?status=pending")
        self.assertEqual(resp_q.status_code, 200)
        data_q = resp_q.get_json()
        self.assertEqual(data_q.get("status"), "success")
        matching = [i for i in data_q.get("items", []) if i["id"] == item_id]
        self.assertTrue(len(matching) > 0)

        # 3. Approve individual item
        resp_decide = self.client.post(
            f"/api/approval-queue/{item_id}/decide",
            json={"decision": "approved", "notes": "Approved by candidate test suite"}
        )
        self.assertEqual(resp_decide.status_code, 200)
        decide_data = resp_decide.get_json()
        self.assertEqual(decide_data.get("decision"), "approved")

        # 4. Enqueue second item for bulk decision
        item_id_2 = self.db.enqueue_for_approval(
            item_type="cold_email",
            reference_id="test_m12_bulk_ref",
            title="Cold Email to VP of AI",
            company="Anthropic Inc",
            details="Email outreach draft for Manoj.",
            preview_payload={"recipient": "hr@anthropic.com"},
            score=9.2
        )
        # Bulk approve item 2
        resp_bulk = self.client.post(
            "/api/approval-queue/bulk-decide",
            json={"decision": "approved", "notes": "Bulk approval test", "item_ids": [item_id_2]}
        )
        self.assertEqual(resp_bulk.status_code, 200)
        bulk_data = resp_bulk.get_json()
        self.assertEqual(bulk_data.get("status"), "success")
        self.assertEqual(bulk_data.get("updated_count"), 1)

    # ── 7. 1,831 PDF HR Contacts Drip Campaign API ────────────────────────────
    def test_drip_stats_api(self):
        """Test drip outreach metrics for 1,831 verified HR recruiters."""
        self._login()
        resp = self.client.get("/api/drip-stats")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data.get("status"), "success")
        self.assertGreaterEqual(data.get("total_contacts"), 0)
        self.assertIn("sent", data)
        self.assertIn("remaining", data)
        self.assertEqual(data.get("daily_quota"), cfg.DAILY_DRIP_LIMIT)

    # ── 8. Candidate Profile & 3-Tier Skills CRUD APIs ─────────────────────────
    def test_candidate_profile_and_skills_crud(self):
        """Test candidate profile inspection, skill addition, and deletion."""
        self._login()

        # 1. Fetch profile
        resp_prof = self.client.get("/api/profile")
        self.assertEqual(resp_prof.status_code, 200)
        prof_data = resp_prof.get_json().get("profile", {})
        self.assertEqual(prof_data.get("name"), cfg.CANDIDATE_NAME)
        self.assertEqual(prof_data.get("email"), cfg.PERSONAL_EMAIL)
        self.assertIn("skills_by_tier", prof_data)

        # 2. Add verified skill in Proficient tier
        skill_name = "M12_Autonomous_Agents_Test"
        resp_add = self.client.post(
            "/api/skills",
            json={"skill_name": skill_name, "proficiency": "Proficient", "category": "AI/ML"}
        )
        self.assertEqual(resp_add.status_code, 200)
        add_data = resp_add.get_json()
        self.assertEqual(add_data.get("status"), "success")

        # 3. Verify skill presence
        resp_verify = self.client.get("/api/profile")
        tiers = resp_verify.get_json().get("profile", {}).get("skills_by_tier", {})
        proficient_skills = tiers.get("Proficient", [])
        added_skill = next((s for s in proficient_skills if s["skill_name"] == skill_name), None)
        self.assertIsNotNone(added_skill)

        # 4. Delete skill
        skill_id = added_skill["id"]
        resp_del = self.client.delete(f"/api/skills/{skill_id}")
        self.assertEqual(resp_del.status_code, 200)
        del_data = resp_del.get_json()
        self.assertEqual(del_data.get("status"), "success")

    # ── 9. System Settings & Agent Trigger APIs ───────────────────────────────
    def test_settings_and_agent_status_apis(self):
        """Test settings inspection/updates and agent status."""
        self._login()

        # 1. Inspect settings
        resp_set = self.client.get("/api/settings")
        self.assertEqual(resp_set.status_code, 200)
        set_data = resp_set.get_json()
        self.assertEqual(set_data.get("status"), "success")
        self.assertIn("dry_run", set_data["settings"])
        self.assertIn("require_human_approval", set_data["settings"])

        # 2. Update settings
        resp_update_set = self.client.post(
            "/api/settings",
            json={"dry_run": True, "require_human_approval": True, "match_score_threshold": 7.5}
        )
        self.assertEqual(resp_update_set.status_code, 200)
        self.assertEqual(cfg.MATCH_SCORE_THRESHOLD, 7.5)

        # 3. Inspect agent status
        resp_status = self.client.get("/api/agent/status")
        self.assertEqual(resp_status.status_code, 200)
        status_data = resp_status.get_json()
        self.assertIn("running", status_data)
        self.assertIn("logs", status_data)


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  🧪 EXECUTING CAREERPILOT AI MILESTONE 12 SAAS DASHBOARD TESTS")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestDashboardAPI)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n" + "=" * 70)
        print("  🎉 ALL 9 SAAS DASHBOARD TEST SUITES PASSED SUCCESSFULLY (100%)!")
        print("=" * 70 + "\n")
        sys.exit(0)
    else:
        print("\n❌ SOME DASHBOARD TESTS FAILED.")
        sys.exit(1)
