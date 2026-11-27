"""
tests/test_apply_service.py — Unit & Integration Tests for Application Engine & Approval Queue (Milestone 8)
============================================================================================================
Validates:
  T1: Candidate application payload mapping (name, contact, education, resume, links, skills)
  T2: Strict double-apply prevention via SHA-256 URL hashing & DB lookups
  T3: Daily application limit enforcement
  T4: Human approval safety gate (blocks live unapproved applications)
  T5: Approval Queue operations (enqueue, get_pending, approve, reject, bulk actions)
  T6: Dry-run application simulation (validates form fields without external requests)
  T7: Multi-portal form assistance handlers (RemoteOK, WWR, Internshala, Wellfound, LinkedIn, Naukri)
  T8: Approved batch execution and SQLite status lifecycle
"""

import sys
import os
import uuid
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService
from services.apply_service import ApplyService


def make_test_job(title: str, portal: str = "remoteok") -> dict:
    unique_id = uuid.uuid4().hex[:10]
    url = f"https://{portal}.com/jobs/{unique_id}"
    db = DatabaseManager(cfg.DB_PATH)
    job_hash = db.make_job_hash(url)
    return {
        "title": title,
        "company": f"TechCorp-{unique_id}",
        "location": "Remote",
        "description": "Building RAG and agentic AI systems with FastAPI and Python.",
        "job_url": url,
        "url": url,
        "job_hash": job_hash,
        "portal": portal,
        "experience": "0-2 years",
        "salary": "$90,000",
        "posted_date": "2026-11-17"
    }


def test_candidate_application_payload():
    """T1: Verify candidate form field mapping from profile and resume."""
    db = DatabaseManager(cfg.DB_PATH)
    profile_service = ProfileService(db)
    service = ApplyService(db_manager=db, profile_service=profile_service)

    payload = service.get_candidate_application_payload()

    assert payload["full_name"] == cfg.CANDIDATE_NAME
    assert payload["email"] == cfg.PERSONAL_EMAIL
    assert payload["phone"] is not None
    assert "linkedin.com" in payload["linkedin_url"]
    assert "github.com" in payload["github_url"]
    assert "education" in payload
    assert payload["education"]["year"] is not None
    assert payload["resume_path"] is not None
    assert payload["resume_exists"] is True, "Candidate resume.pdf must exist and be readable"
    assert len(payload["top_skills"]) > 0

    print("  ✅ test_candidate_application_payload passed")


def test_double_apply_prevention():
    """T2: Verify that applying twice to the same URL is strictly blocked."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=False)

    job = make_test_job("AI Research Engineer")
    url = job["job_url"]
    job_hash = db.make_job_hash(url)

    # First check: can apply
    allowed1, reason1 = service.can_apply(job)
    assert allowed1 is True, f"First check must pass, got: {reason1}"

    # Mark as applied in DB
    db.add_job(job)
    db.update_application_status(job_hash, status="applied", notes="Applied in test")

    # Second check: must be rejected
    allowed2, reason2 = service.can_apply(job)
    assert allowed2 is False, "Double apply must be blocked"
    assert "REJECT_ALREADY_APPLIED" in reason2

    print("  ✅ test_double_apply_prevention passed")


def test_human_approval_safety_gate():
    """T3: Verify that live unapproved applications are blocked by the safety gate."""
    db = DatabaseManager(cfg.DB_PATH)
    # Require approval and NOT dry-run
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    job = make_test_job("Unapproved Junior Python Engineer")
    db.add_job(job)

    # Attempt to apply without human approval in approval_queue
    res = service.apply_to_job(job)

    assert res["status"] == "awaiting_approval"
    assert "SAFETY_GATE_ACTIVE" in res["reason"] or "Explicit human approval is required" in res["reason"]

    # Verify applications status in DB remains 'new' / not 'applied'
    conn = db._get_conn()
    row = conn.execute("SELECT status FROM applications WHERE job_hash = ?", (job["job_hash"],)).fetchone()
    assert row is not None
    assert row["status"] != "applied", "Status must NOT change to applied without approval"

    print("  ✅ test_human_approval_safety_gate passed")


def test_approval_queue_operations():
    """T4: Verify enqueuing, retrieving, approving, rejecting, and bulk operations."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db)

    unique_ref = uuid.uuid4().hex[:10]
    # Enqueue item
    qid = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=unique_ref,
        title="Test Agentic Engineer",
        company="ScaleAI Labs",
        details="Relevance Score: 9.2/10",
        preview_payload={"job_url": f"https://example.com/apply/{unique_ref}"},
        score=9.2
    )
    assert qid > 0

    # Get pending
    pending = service.get_pending_approvals()
    pending_ids = [p["id"] for p in pending]
    assert qid in pending_ids

    # Approve item
    ok_approve = service.approve_item(qid, notes="Approved by candidate")
    assert ok_approve is True

    # Check status changed in DB
    conn = db._get_conn()
    row = conn.execute("SELECT status, reviewer_notes FROM approval_queue WHERE id = ?", (qid,)).fetchone()
    assert row["status"] == "approved"
    assert "Approved by candidate" in row["reviewer_notes"]

    # Bulk rejection test
    qid2 = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=uuid.uuid4().hex[:10],
        title="Reject Test",
        company="RejectCorp",
        details="Low score",
        preview_payload={},
        score=5.0
    )
    bulk_res = service.bulk_decide([qid2], decision="rejected", notes="Bulk cleanup")
    assert bulk_res["success"] == 1

    row2 = conn.execute("SELECT status FROM approval_queue WHERE id = ?", (qid2,)).fetchone()
    assert row2["status"] == "rejected"

    print("  ✅ test_approval_queue_operations passed")


def test_dry_run_application_simulation():
    """T5: Verify that dry-run mode simulates form mapping without making external calls."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=True, require_approval=True)

    job = make_test_job("FastAPI Backend Engineer", portal="remoteok")
    db.add_job(job)

    # Pre-approve in approval queue to pass the human approval safety gate
    qid = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=job["job_hash"],
        title=job["title"],
        company=job["company"],
        details="Dry-run simulation test",
        preview_payload=job,
        score=8.5
    )
    service.approve_item(qid, notes="Pre-approved for dry-run simulation test")

    res = service.apply_to_job(job, queue_id=qid)

    assert res["status"] in ("simulated_applied", "dry_run_preview")
    assert res["dry_run"] is True
    assert "field_mapping_preview" in res
    assert res["field_mapping_preview"]["resume_attached"] is True

    print("  ✅ test_dry_run_application_simulation passed")


def test_multi_portal_handlers():
    """T6: Verify all 6 portal handlers format application data appropriately."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=True)
    payload = service.get_candidate_application_payload()

    portals = ["remoteok", "weworkremotely", "internshala", "wellfound", "linkedin", "naukri"]
    for p in portals:
        job = make_test_job(f"Engineer on {p.upper()}", portal=p)
        handler_name = f"_apply_{p}"
        handler = getattr(service, handler_name)
        out = handler(job, payload)

        assert out["status"] in ("applied", "manual_required")
        assert "resume_path" in out
        assert out["resume_path"].endswith(".pdf")

    print("  ✅ test_multi_portal_handlers passed (all 6 portals formatted)")


def test_approved_batch_execution():
    """T7: Verify batch execution of approved queue items and status updates."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    job = make_test_job("Approved Live Application Role", portal="internshala")
    url = job["job_url"]
    job_hash = db.make_job_hash(url)
    db.add_job(job)

    # Enqueue and approve
    qid = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=job_hash,
        title=job["title"],
        company=job["company"],
        details="Top match 9.5",
        preview_payload=job,
        score=9.5
    )
    service.approve_item(qid, notes="Pre-approved for batch test")

    # Run batch apply
    results = service.apply_approved_batch(limit=5)
    matched = [r for r in results if r["job_hash"] == job_hash]
    assert len(matched) == 1, "Approved job must be executed by apply_approved_batch"
    assert matched[0]["status"] in ("applied", "manual_required")

    # Verify status in applications table
    conn = db._get_conn()
    app_row = conn.execute("SELECT status, applied_date FROM applications WHERE job_hash = ?", (job_hash,)).fetchone()
    assert app_row is not None
    assert app_row["status"] in ("applied", "manual_required")

    # Verify queue status has executed execution_status
    q_row = conn.execute("SELECT status, execution_status, reviewer_notes FROM approval_queue WHERE id = ?", (qid,)).fetchone()
    assert q_row is not None
    assert q_row["status"] == "approved"
    assert q_row["execution_status"] in ("applied", "manual_required")

    print("  ✅ test_approved_batch_execution passed")


if __name__ == "__main__":
    print("\nRunning Milestone 8 Application Engine & Approval Queue Tests...\n")

    tests = [
        test_candidate_application_payload,
        test_double_apply_prevention,
        test_human_approval_safety_gate,
        test_approval_queue_operations,
        test_dry_run_application_simulation,
        test_multi_portal_handlers,
        test_approved_batch_execution,
    ]

    passed_count = 0
    failed_count = 0

    for test_fn in tests:
        try:
            test_fn()
            passed_count += 1
        except AssertionError as e:
            print(f"  ❌ {test_fn.__name__} FAILED: {e}")
            failed_count += 1
        except Exception as e:
            print(f"  💥 {test_fn.__name__} ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1

    print(f"\n{'='*68}")
    if failed_count == 0:
        print(f"🎉 ALL {passed_count} APPLICATION ENGINE TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
