"""
tests/test_system_hardening.py — Test Suite for Milestone 14 System Hardening & Performance Optimization
========================================================================================================
Validates:
1. SQLite index optimization verified via `EXPLAIN QUERY PLAN` (zero unintended full table scans)
2. WAL checkpointing (`TRUNCATE` mode) & database optimization (`PRAGMA optimize`)
3. HTTP security headers (X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, Referrer-Policy)
4. Brute-force & flood rate limiting defense (HTTP 429 Too Many Requests)
5. Defensive input validation & boundaries (page clamping, status whitelist, score range checks)
6. Concurrency & lock resistance under simultaneous multithreaded readers/writers
7. Admin maintenance API endpoint integrity
"""

import sys
import time
import threading
import unittest
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from dashboard.app import app, login_limiter, agent_run_limiter


class TestSystemHardening(unittest.TestCase):
    """Test suite for system hardening, concurrency, and performance optimization."""

    @classmethod
    def setUpClass(cls):
        app.config["TESTING"] = True
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

    # ── 1. EXPLAIN QUERY PLAN & Index Verification ───────────────────────────
    def test_explain_query_plan_index_utilization(self):
        """Verify queries utilize indexes rather than table scans."""
        # 1. Lookup by job_hash in applications
        plan_hash = self.db.explain_query_plan(
            "SELECT * FROM applications WHERE job_hash = ?", ("test_hash",)
        )
        plan_str = " ".join([r.get("detail", "") for r in plan_hash])
        self.assertIn("INDEX", plan_str.upper())
        self.assertTrue("IDX_APPS_JOB_HASH" in plan_str.upper() or "INDEX" in plan_str.upper())

        # 2. Lookup by status in applications
        plan_status = self.db.explain_query_plan(
            "SELECT * FROM applications WHERE status = ?", ("applied",)
        )
        plan_status_str = " ".join([r.get("detail", "") for r in plan_status])
        self.assertIn("INDEX", plan_status_str.upper())

        # 3. Lookup in approval_queue by status
        plan_queue = self.db.explain_query_plan(
            "SELECT * FROM approval_queue WHERE status = ?", ("pending",)
        )
        plan_queue_str = " ".join([r.get("detail", "") for r in plan_queue])
        self.assertIn("INDEX", plan_queue_str.upper())

        # 4. Lookup in job_scores by job_hash
        plan_scores = self.db.explain_query_plan(
            "SELECT * FROM job_scores WHERE job_hash = ?", ("test_hash",)
        )
        plan_scores_str = " ".join([r.get("detail", "") for r in plan_scores])
        self.assertIn("INDEX", plan_scores_str.upper())

    # ── 2. WAL Checkpoint & Database Optimization ────────────────────────────
    def test_wal_checkpoint_and_database_optimization(self):
        """Verify WAL checkpoint flushes log pages and optimize runs without error."""
        # 1. WAL Checkpoint TRUNCATE
        checkpoint_res = self.db.checkpoint_wal(mode="TRUNCATE")
        self.assertIn("status", checkpoint_res)
        self.assertIn("checkpointed_pages", checkpoint_res)
        self.assertEqual(checkpoint_res["mode"], "TRUNCATE")

        # 2. Database optimization
        optimize_res = self.db.optimize_database()
        self.assertEqual(optimize_res["status"], "optimized")
        self.assertIn("timestamp", optimize_res)

    # ── 3. HTTP Security Headers ──────────────────────────────────────────────
    def test_http_security_headers_present(self):
        """Verify security headers are injected into all HTTP responses."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "SAMEORIGIN")
        self.assertEqual(resp.headers.get("X-XSS-Protection"), "1; mode=block")
        self.assertEqual(resp.headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")
        self.assertEqual(resp.headers.get("Server"), "CareerPilot-Secure-Engine")

    # ── 4. Brute-Force & Flood Rate Limiter ────────────────────────────────────
    def test_rate_limiter_brute_force_protection(self):
        """Verify rapid repeated requests trigger HTTP 429 Too Many Requests."""
        self._logout()
        login_limiter.reset()

        # Send 10 rapid failed login requests (up to limit)
        for _ in range(10):
            resp = self.client.post("/login", json={"password": "wrong_password"})
            self.assertEqual(resp.status_code, 401)

        # 11th request must be rate-limited (HTTP 429)
        resp_blocked = self.client.post("/login", json={"password": "wrong_password"})
        self.assertEqual(resp_blocked.status_code, 429)
        data = resp_blocked.get_json()
        self.assertEqual(data.get("status"), "error")
        self.assertEqual(data.get("error"), "Too Many Requests")

        # Reset limiter and verify normal access restored
        login_limiter.reset()
        resp_restored = self.client.post("/login", json={"password": cfg.DASHBOARD_PASSWORD})
        self.assertEqual(resp_restored.status_code, 200)

    # ── 5. Defensive Input Validation Boundaries ──────────────────────────────
    def test_input_validation_boundaries(self):
        """Verify boundaries are enforced on pagination, filters, and update payloads."""
        self._login()

        # 1. Negative page and oversized per_page clamped safely
        resp_jobs = self.client.get("/api/jobs?page=-5&per_page=999")
        self.assertEqual(resp_jobs.status_code, 200)
        jobs_data = resp_jobs.get_json()
        self.assertEqual(jobs_data["page"], 1)
        self.assertEqual(jobs_data["per_page"], 100)

        # 2. Invalid portal string safely handled
        resp_portal = self.client.get("/api/jobs?portal=malicious_injection_portal")
        self.assertEqual(resp_portal.status_code, 200)

        # 3. Invalid application status rejected with HTTP 400
        resp_status = self.client.post(
            "/api/applications/non_existent_hash/status",
            json={"status": "invalid_status_type"}
        )
        self.assertEqual(resp_status.status_code, 400)
        status_data = resp_status.get_json()
        self.assertEqual(status_data["status"], "error")
        self.assertIn("Invalid status", status_data["message"])

        # 4. Out-of-bounds score threshold rejected with HTTP 400
        resp_thresh = self.client.post(
            "/api/settings",
            json={"match_score_threshold": 99.9}
        )
        self.assertEqual(resp_thresh.status_code, 400)

    # ── 6. Multithreaded Database Concurrency ─────────────────────────────────
    def test_multithreaded_concurrency_without_locks(self):
        """Simulate concurrent reading and writing threads to verify zero lock failures."""
        errors = []

        def worker_writer(thread_id: int):
            try:
                for i in range(10):
                    job_url = f"https://concurrency.test/{thread_id}_{i}_{time.time()}"
                    self.db.add_job({
                        "url": job_url,
                        "title": f"Concurrent Job {thread_id}-{i}",
                        "company": "Concurrency Corp",
                        "portal": "direct",
                        "relevance_score": 8.0
                    })
            except Exception as e:
                errors.append(f"Writer {thread_id} error: {e}")

        def worker_reader(thread_id: int):
            try:
                for _ in range(10):
                    _ = self.db.get_all_time_stats()
                    _ = self.db.get_applications(page=1, per_page=10)
            except Exception as e:
                errors.append(f"Reader {thread_id} error: {e}")

        threads = []
        for t_idx in range(4):
            threads.append(threading.Thread(target=worker_writer, args=(t_idx,)))
            threads.append(threading.Thread(target=worker_reader, args=(t_idx,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        self.assertEqual(len(errors), 0, f"Concurrency errors encountered: {errors}")

    # ── 7. Admin Maintenance API ──────────────────────────────────────────────
    def test_admin_maintenance_api(self):
        """Verify POST /api/admin/maintenance triggers WAL checkpoint and returns success."""
        self._login()
        resp = self.client.post("/api/admin/maintenance", json={"mode": "TRUNCATE"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["status"], "success")
        self.assertIn("wal_checkpoint", data)
        self.assertIn("database_optimization", data)


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  🧪 EXECUTING CAREERPILOT AI MILESTONE 14 SYSTEM HARDENING TESTS")
    print("=" * 70)
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSystemHardening)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    if result.wasSuccessful():
        print("\n" + "=" * 70)
        print("  🎉 ALL 7 SYSTEM HARDENING & CONCURRENCY TESTS PASSED (100%)!")
        print("=" * 70 + "\n")
        sys.exit(0)
    else:
        print("\n❌ SOME SYSTEM HARDENING TESTS FAILED.")
        sys.exit(1)
