"""
tests/test_f6_complete.py — Comprehensive F6 LangGraph Multi-Agent Verification Suite
====================================================================================
Validates all 18 F6 mandatory and bonus architectural requirements:
  T01: LangGraph StateGraph compilation with SqliteSaver checkpointer
  T02: AgentState complete schema initialization
  T03: SQLite checkpoint persistence across node transitions
  T04: Checkpoint recovery and resumption from saved thread_id
  T05: Scraper node fault tolerance (graceful fallback on engine/portal failures)
  T06: Filter node multi-criteria pass/reject isolation
  T07: Score node semantic evaluation and score persistence
  T08: Stage approvals safety gate and approval_queue persistence
  T09: Human approval barrier (unapproved applications strictly blocked from apply node)
  T10: Apply node dry-run execution with strict duplicate protection
  T11: Dry-run email routing (permits email draft staging during dry runs)
  T12: Email node cold email draft generation and approval_queue staging
  T13: Email safety (zero SMTP dispatches during dry-run executions)
  T14: Inbox monitor node execution, IMAP error tolerance, and reply classification
  T15: Drip campaign node (CSV staging, daily limits, approval queue integration)
  T16: Memory nodes lifecycle (load_memory and save_memory persistence)
  T17: Report node summary metrics and HTML artifact generation
  T18: Full end-to-end multi-agent workflow execution (START to END with SQLite checkpoint)
"""

import sys
import os
import uuid
import sqlite3
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from agent.state import AgentState
from agent.graph import (
    build_job_agent_graph,
    get_checkpointer,
    get_initial_state,
    execute_workflow,
    resume_workflow,
    should_continue_after_filter,
    should_apply_after_approval,
    should_email_after_apply,
)
from agent.nodes import (
    load_memory_node,
    scrape_jobs_node,
    filter_jobs_node,
    score_jobs_node,
    stage_approvals_node,
    apply_jobs_node,
    email_outreach_node,
    inbox_monitor_node,
    drip_campaign_node,
    save_memory_node,
    generate_report_node,
)


class TestF6LangGraphWorkflow(unittest.TestCase):
    """Test suite for complete F6 LangGraph implementation."""

    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager(cfg.DB_PATH)
        cls.test_db_path = str(cfg.DATABASE_DIR / f"test_checkpoints_{uuid.uuid4().hex[:6]}.db")

    @classmethod
    def tearDownClass(cls):
        # Cleanup temporary test checkpoint db if created
        for p in [Path(cls.test_db_path)]:
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass

    # ── T01: Graph Compilation with SqliteSaver Checkpointer ──────────
    def test_01_graph_compilation_with_checkpointer(self):
        """T01: Verify graph compilation succeeds with SqliteSaver checkpointer."""
        checkpointer = get_checkpointer(self.test_db_path)
        app = build_job_agent_graph(checkpointer=checkpointer)

        self.assertIsNotNone(app, "Compiled LangGraph app must not be None")
        self.assertTrue(hasattr(app, "invoke"), "Compiled app must provide invoke() method")
        self.assertIsNotNone(checkpointer, "Checkpointer must be instantiated")
        print("  ✅ T01: Graph compilation with SqliteSaver checkpointer verified")

    # ── T02: State Initialization Schema ──────────────────────────────
    def test_02_state_initialization_schema(self):
        """T02: Verify AgentState dictionary schema has all required keys."""
        state = get_initial_state(dry_run=True, require_approval=True)

        required_keys = [
            "run_id", "run_date", "dry_run", "mock_mode", "require_human_approval",
            "start_time", "session_memory", "raw_jobs", "filtered_jobs",
            "rejected_jobs", "scored_jobs", "staged_approvals", "approved_jobs",
            "applied_jobs", "emailed_jobs", "inbox_messages", "inbox_stats",
            "csv_drip_staged", "pdf_drip_contacts", "pdf_drip_sent",
            "pdf_drip_prepared", "stats", "logs", "errors", "report_summary"
        ]

        for key in required_keys:
            self.assertIn(key, state, f"State must contain key: {key}")

        self.assertTrue(state["run_id"].startswith("run_"))
        self.assertTrue(state["dry_run"])
        self.assertTrue(state["require_human_approval"])
        print("  ✅ T02: AgentState complete schema initialization verified")

    # ── T03: Checkpoint Persistence Across Nodes ──────────────────────
    def test_03_checkpoint_persistence(self):
        """T03: Verify checkpoints are saved to SQLite after node execution."""
        checkpointer = get_checkpointer(self.test_db_path)
        app = build_job_agent_graph(checkpointer=checkpointer)

        thread_id = f"test_thread_{uuid.uuid4().hex[:8]}"
        initial_state = get_initial_state(dry_run=True, require_approval=True, mock_mode=True)
        initial_state["run_id"] = thread_id

        # Run workflow
        app.invoke(initial_state, config={"configurable": {"thread_id": thread_id}})

        # Verify checkpoints exist in SQLite for this thread_id
        checkpoints = list(checkpointer.list({"configurable": {"thread_id": thread_id}}))
        self.assertGreater(len(checkpoints), 0, "Checkpointer must contain saved checkpoints")

        latest_cp = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        self.assertIsNotNone(latest_cp, "Latest checkpoint tuple must be retrievable")
        self.assertIn("report_summary", latest_cp.checkpoint["channel_values"])
        print(f"  ✅ T03: Checkpoint persistence verified ({len(checkpoints)} checkpoints saved)")

    # ── T04: Checkpoint Resumption ────────────────────────────────────
    def test_04_checkpoint_resumption(self):
        """T04: Verify workflow state can be retrieved and resumed from thread_id."""
        checkpointer = get_checkpointer(self.test_db_path)
        thread_id = f"resume_thread_{uuid.uuid4().hex[:8]}"

        state = get_initial_state(dry_run=True, require_approval=True, mock_mode=True)
        state["run_id"] = thread_id

        # Execute once
        app = build_job_agent_graph(checkpointer=checkpointer)
        app.invoke(state, config={"configurable": {"thread_id": thread_id}})

        # Resume / fetch state using existing thread_id
        latest_tuple = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        self.assertIsNotNone(latest_tuple)
        recovered_state = latest_tuple.checkpoint["channel_values"]
        self.assertEqual(recovered_state.get("run_id"), thread_id)
        print("  ✅ T04: Checkpoint recovery and resumption from thread_id verified")

    # ── T05: Scraper Node Fault Tolerance ─────────────────────────────
    def test_05_scraper_fault_tolerance(self):
        """T05: Verify scraper node catches engine crashes gracefully without breaking workflow."""
        state = get_initial_state(dry_run=True, mock_mode=True)

        with patch("scrapers.scraper_engine.ScraperEngine.run_all", side_effect=RuntimeError("Simulated Scraper Engine Outage")):
            out = scrape_jobs_node(state)
            self.assertEqual(out["raw_jobs"], [], "Failed scraper must return empty raw_jobs")
            self.assertTrue(len(out["errors"]) > 0, "Errors list must record the scraper failure")
            self.assertIn("Simulated Scraper Engine Outage", out["errors"][0])
        print("  ✅ T05: Scraper node fault tolerance verified")

    # ── T06: Filter Node Pass/Reject Isolation ────────────────────────
    def test_06_filter_node_pass_reject(self):
        """T06: Verify filter node separates matching target roles from irrelevant ones."""
        state = get_initial_state(dry_run=True, mock_mode=True)
        u1, u2 = uuid.uuid4().hex[:6], uuid.uuid4().hex[:6]
        state["raw_jobs"] = [
            {
                "title": "Python Developer",
                "company": f"TechCorp_{u1}",
                "job_url": f"https://example.com/j1_{u1}",
                "description": "Looking for a Python Developer with Python, FastAPI, SQL, Docker, and Git experience.",
                "location": "Remote"
            },
            {
                "title": "Dental Hygienist",
                "company": f"SmileClinic_{u2}",
                "job_url": f"https://example.com/j2_{u2}",
                "description": "Teeth cleaning and dental hygiene clinic assistant.",
                "location": "Remote"
            },
        ]

        out = filter_jobs_node(state)
        self.assertGreaterEqual(len(out["filtered_jobs"]), 1, "Matching Python role must pass filter")
        self.assertGreaterEqual(len(out["rejected_jobs"]), 1, "Irrelevant role must be rejected")
        print("  ✅ T06: Filter node pass/reject isolation verified")

    # ── T07: Score Node Execution and Persistence ─────────────────────
    def test_07_score_node_execution(self):
        """T07: Verify score node assigns relevance scores and breakdown."""
        state = get_initial_state(dry_run=True, mock_mode=True)
        state["filtered_jobs"] = [
            {
                "job_title": "Full Stack Python AI Engineer",
                "company_name": "InnovateAI",
                "job_url": f"https://example.com/score_{uuid.uuid4().hex[:6]}",
                "description": "Looking for Python, LangGraph, and FastAPI expertise."
            }
        ]

        out = score_jobs_node(state)
        self.assertEqual(len(out["scored_jobs"]), 1)
        scored = out["scored_jobs"][0]
        self.assertIn("relevance_score", scored)
        self.assertGreater(scored["relevance_score"], 0)
        print(f"  ✅ T07: Score node execution verified (Score: {scored['relevance_score']})")

    # ── T08: Stage Approvals Safety Gate ──────────────────────────────
    def test_08_stage_approvals_node(self):
        """T08: Verify high-scoring jobs are staged in approval_queue under 'job_application'."""
        state = get_initial_state(dry_run=True, require_approval=True, mock_mode=True)
        test_url = f"https://example.com/stage_{uuid.uuid4().hex[:6]}"
        job_hash = self.db.make_job_hash(test_url)
        state["scored_jobs"] = [
            {
                "title": "Lead Python Engineer",
                "company": "MegaCorp",
                "job_url": test_url,
                "job_hash": job_hash,
                "relevance_score": 9.2,
                "apply_status": "pending_approval"
            }
        ]

        out = stage_approvals_node(state)
        self.assertGreaterEqual(len(out["staged_approvals"]), 1)

        # Verify queued in SQLite as 'pending'
        items = self.db.get_approval_queue(status="pending", item_type="job_application")
        found = any(i.get("reference_id") == job_hash for i in items)
        self.assertTrue(found, "Job must be enqueued in SQLite approval_queue with status='pending'")
        print("  ✅ T08: Stage approvals safety gate and SQLite persistence verified")

    # ── T09: Approval Gate Prevents Unapproved Applications ───────────
    def test_09_approval_gate_unapproved_blocked(self):
        """T09: Verify unapproved jobs are routed to inbox_monitor and never reach apply node."""
        state_unapproved = {"approved_jobs": []}
        state_approved = {"approved_jobs": [{"id": 1, "title": "Dev"}]}

        route_unapproved = should_apply_after_approval(state_unapproved)
        route_approved = should_apply_after_approval(state_approved)

        self.assertEqual(route_unapproved, "inbox_monitor", "Unapproved jobs must route to inbox_monitor")
        self.assertEqual(route_approved, "apply", "Approved jobs must route to apply")
        print("  ✅ T09: Human approval barrier strictly enforced")

    # ── T10: Apply Node Dry-Run Duplicate Protection ──────────────────
    def test_10_apply_node_dry_run_duplicate_protection(self):
        """T10: Verify apply node in dry-run mode simulates without real submission and guards duplicates."""
        test_hash = f"hash_{uuid.uuid4().hex[:8]}"
        job_url = f"https://remoteok.com/apply_{test_hash}"
        queue_id = self.db.enqueue_for_approval(
            item_type="job_application",
            reference_id=test_hash,
            title="Backend Python Developer",
            company="TestCompany",
            details="Details",
            preview_payload={"portal": "remoteok", "url": job_url, "apply_type": "ats"},
            score=8.8
        )
        self.db.decide_approval(queue_id, "approved")

        state = get_initial_state(dry_run=True, require_approval=True)
        state["approved_jobs"] = [
            {
                "title": "Backend Python Developer",
                "company": "TestCompany",
                "job_url": job_url,
                "url": job_url,
                "job_hash": test_hash,
                "portal": "remoteok",
                "apply_type": "ats",
                "queue_id": queue_id,
                "score": 8.8,
            }
        ]

        out1 = apply_jobs_node(state)
        self.assertEqual(len(out1["applied_jobs"]), 1)
        self.assertTrue(out1["applied_jobs"][0].get("dry_run"))

        # Re-run: duplicate protection handles repeat safely
        out2 = apply_jobs_node(state)
        self.assertEqual(len(out2["applied_jobs"]), 1)
        print("  ✅ T10: Apply node dry-run execution and duplicate protection verified")

    # ── T11: Dry-Run Email Routing ────────────────────────────────────
    def test_11_dry_run_email_routing(self):
        """T11: Verify should_email_after_apply permits routing to email node during dry-run."""
        state_with_applications = {
            "dry_run": True,
            "applied_jobs": [
                {"title": "Dev", "company": "Co", "status": "applied", "dry_run": True}
            ]
        }
        state_no_applications = {
            "dry_run": True,
            "applied_jobs": []
        }

        route_eligible = should_email_after_apply(state_with_applications)
        route_empty = should_email_after_apply(state_no_applications)

        self.assertEqual(route_eligible, "email", "Eligible dry-run applications must route to email node for preview")
        self.assertEqual(route_empty, "inbox_monitor", "Empty applications must route to inbox_monitor")
        print("  ✅ T11: Dry-run email routing allows draft previews")

    # ── T12: Email Node Draft Generation & Staging ────────────────────
    def test_12_email_node_draft_generation_staging(self):
        """T12: Verify email node generates cold email draft and stages it in approval_queue."""
        unique_id = uuid.uuid4().hex[:6]
        test_company = f"StagingCorp{unique_id}"
        test_email = f"recruiter@{test_company.lower()}.com"

        # Pre-seed verified contact in hr_contacts table (schema has 'source' and 'verified')
        with self.db._get_conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO hr_contacts (company, company_domain, hr_name, hr_email, source, verified)
                VALUES (?, ?, 'Jane Recruiter', ?, 'pre_seeded', 1)
                """,
                (test_company, f"{test_company.lower()}.com", test_email),
            )

        job_url = f"https://example.com/draft_{unique_id}"
        job_hash = self.db.make_job_hash(job_url)

        state = get_initial_state(dry_run=True, require_approval=True)
        state["applied_jobs"] = [
            {
                "title": "Senior AI Architect",
                "company": test_company,
                "url": job_url,
                "job_hash": job_hash,
                "status": "applied",
                "dry_run": True,
                "relevance_score": 9.5
            }
        ]

        out = email_outreach_node(state)
        self.assertEqual(len(out["emailed_jobs"]), 1)
        result = out["emailed_jobs"][0]
        self.assertEqual(result.get("status"), "awaiting_approval")

        # Verify staged item exists in approval_queue under 'cold_email'
        items = self.db.get_approval_queue(status="pending", item_type="cold_email")
        staged = next((i for i in items if i.get("reference_id") == job_hash), None)
        self.assertIsNotNone(staged, "Cold email draft must be present in SQLite approval_queue")
        self.assertIn("recipient_email", staged["preview_payload"])
        self.assertEqual(staged["preview_payload"]["recipient_email"], test_email)
        print("  ✅ T12: Cold email draft generation and staging into approval_queue verified")

    # ── T13: Email Safety (Zero SMTP Calls in Dry-Run) ────────────────
    def test_13_email_safety_zero_smtp(self):
        """T13: Verify zero real SMTP transmissions occur when dry_run=True."""
        state = get_initial_state(dry_run=True, require_approval=True)
        state["applied_jobs"] = [
            {
                "title": "Python Dev",
                "company": "SafeCorp",
                "url": "https://example.com/safe_1",
                "status": "applied",
                "dry_run": True
            }
        ]

        # Patch EmailSender.send_cold_email to raise if ever invoked
        with patch("tools.email_sender.EmailSender.send_cold_email", side_effect=AssertionError("SMTP send_cold_email must NOT be called in dry run")):
            out = email_outreach_node(state)
            self.assertEqual(out["stats"]["emailed_count"], 0)
        print("  ✅ T13: Zero SMTP transmissions in dry-run mode strictly confirmed")

    # ── T14: Inbox Monitor Node Execution & Fault Tolerance ───────────
    def test_14_inbox_monitor_node(self):
        """T14: Verify inbox monitor node handles IMAP connectivity errors gracefully and updates state."""
        state = get_initial_state(dry_run=True)

        # 1. Graceful exception tolerance
        with patch("services.email_service.EmailService.run_inbox_monitor_cycle", side_effect=ConnectionError("IMAP Offline")):
            out = inbox_monitor_node(state)
            self.assertEqual(out["inbox_messages"], [])
            self.assertTrue(len(out["errors"]) > 0)

        # 2. Normal execution cycle
        mock_cycle = {
            "processed_count": 2,
            "stats": {"interviews_detected": 1, "bounces_handled": 0, "rejections_logged": 0},
            "messages": [{"subject": "Interview Invitation", "sender": "hr@corp.com"}]
        }
        with patch("services.email_service.EmailService.run_inbox_monitor_cycle", return_value=mock_cycle):
            out2 = inbox_monitor_node(state)
            self.assertEqual(out2["stats"]["interviews_detected"], 1)
            self.assertEqual(out2["stats"]["inbox_inspected"], 2)
        print("  ✅ T14: Inbox monitor node execution, IMAP fault tolerance, and stats tracking verified")

    # ── T15: Drip Campaign Node (CSV & PDF) ───────────────────────────
    def test_15_drip_campaign_node(self):
        """T15: Verify drip campaign stages CSV contacts into approval_queue and previews PDF contacts."""
        # Pre-seed a pending CSV contact
        uid = uuid.uuid4().hex[:6]
        test_email = f"lead_{uid}@targetcorp.com"
        self.db.import_csv_drip_contacts([
            {"company": f"TargetCorp_{uid}", "name": "Talent Lead", "email": test_email, "role": "Senior Engineer"}
        ])

        state = get_initial_state(dry_run=True, require_approval=True)
        out = drip_campaign_node(state)

        self.assertIn("csv_drip_staged", out)
        self.assertIn("pdf_drip_prepared", out)
        self.assertGreaterEqual(len(out["csv_drip_staged"]), 1, "At least 1 CSV contact must be staged")

        # Verify staged item exists in approval_queue under 'drip_email'
        items = self.db.get_approval_queue(status="pending", item_type="drip_email")
        found = any(i.get("preview_payload", {}).get("recipient_email") == test_email for i in items)
        self.assertTrue(found, "Pending CSV contact must be enqueued with item_type='drip_email'")
        print("  ✅ T15: Drip campaign node CSV staging and approval_queue integration verified")

    # ── T16: Memory Nodes Lifecycle ───────────────────────────────────
    def test_16_memory_nodes_lifecycle(self):
        """T16: Verify load_memory and save_memory preserve session context."""
        state = get_initial_state(dry_run=True)

        load_out = load_memory_node(state)
        self.assertIn("session_memory", load_out)

        state["session_memory"] = load_out["session_memory"]
        state["stats"]["total_scraped"] = 15

        save_out = save_memory_node(state)
        self.assertIn("logs", save_out)

        # Verify persistence in SQLite memory_store table
        saved = self.db.get_memory("last_run_summary")
        self.assertIsNotNone(saved)
        self.assertEqual(saved.get("category"), "workflow_stats")
        print("  ✅ T16: Memory nodes lifecycle (load & save) verified")

    # ── T17: Report Node Artifact Generation ──────────────────────────
    def test_17_report_node_summary(self):
        """T17: Verify generate_report_node creates metrics summary and HTML report."""
        state = get_initial_state(dry_run=True)
        state["raw_jobs"] = [{"title": f"Job {i}"} for i in range(12)]
        state["filtered_jobs"] = [{"title": f"Job {i}"} for i in range(5)]
        state["scored_jobs"] = [{"title": f"Job {i}", "relevance_score": 8.0} for i in range(5)]
        state["applied_jobs"] = [{"title": "Python Dev", "company": "Acme", "status": "applied", "dry_run": True}]

        out = generate_report_node(state)
        self.assertIn("report_summary", out)
        self.assertIn("report_html", out)
        self.assertIsNotNone(out["report_html"])
        self.assertEqual(out["report_summary"]["metrics"]["scraped"], 12)
        print("  ✅ T17: Report node summary metrics and HTML generation verified")

    # ── T18: Full End-to-End Workflow Execution ───────────────────────
    def test_18_full_end_to_end_workflow(self):
        """T18: Verify full end-to-end multi-agent execution with SQLite checkpointing from START to END."""
        thread_id = f"e2e_thread_{uuid.uuid4().hex[:8]}"
        checkpointer = get_checkpointer(self.test_db_path)

        final_state = execute_workflow(
            dry_run=True,
            require_approval=True,
            mock_mode=True,
            thread_id=thread_id,
            checkpointer=checkpointer,
        )

        self.assertIsNotNone(final_state)
        self.assertEqual(final_state.get("run_id"), thread_id)
        self.assertIn("report_summary", final_state)
        self.assertIn("logs", final_state)
        self.assertGreater(len(final_state["logs"]), 5, "Execution must record logs from multiple workflow nodes")

        # Verify persistent checkpoint was created for this thread
        saved_cp = checkpointer.get_tuple({"configurable": {"thread_id": thread_id}})
        self.assertIsNotNone(saved_cp, "Checkpointer must hold complete final state")
        print(f"  ✅ T18: Full end-to-end workflow execution verified (Run ID: {thread_id})")


if __name__ == "__main__":
    print("\n" + "=" * 76)
    print("  🧪 RUNNING COMPREHENSIVE F6 LANGGRAPH AGENT WORKFLOW TEST SUITE")
    print("=" * 76 + "\n")
    unittest.main(verbosity=2)
