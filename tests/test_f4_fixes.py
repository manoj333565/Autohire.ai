"""
tests/test_f4_fixes.py — Regression Test Suite for F4 Automated Application Engine Fixes
========================================================================================
Validates the two critical fixes and security guarantees for F4:
  1. Unsupported portal creates a cold_email approval queue item when verified HR contact exists in hr_contacts.
  2. Repeated workflow runs do not create duplicate cold_email queue items.
  3. Missing recruiter email does NOT create a sendable outreach queue item.
  4. No email is sent without explicit human approval.
  5. Application status remains manual_required until confirmed action changes it.
  6. Randomized delays stay within configured bounds (cfg.BROWSER_MIN_DELAY to cfg.BROWSER_MAX_DELAY).
  7. Duplicate application protection remains intact across repeated runs.
  8. Human approval safety gate remains intact (blocks live unapproved applications).
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
from services.email_service import EmailService


def make_test_job(title: str, company: str = None, portal: str = "internshala") -> dict:
    unique_id = uuid.uuid4().hex[:10]
    comp = company or f"TestCorp-{unique_id}"
    url = f"https://{portal}.com/jobs/{unique_id}"
    db = DatabaseManager(cfg.DB_PATH)
    job_hash = db.make_job_hash(url)
    return {
        "title": title,
        "company": comp,
        "location": "Remote",
        "description": "Senior Python and AI Engineer building LangGraph workflows.",
        "job_url": url,
        "url": url,
        "job_hash": job_hash,
        "portal": portal,
        "experience": "0-2 years",
        "salary": "INR 6-10 LPA",
        "posted_date": "2026-11-27",
        "relevance_score": 8.8,
    }


def test_1_unsupported_portal_creates_cold_email_item():
    """1. Unsupported portal creates a cold_email approval queue item when verified HR contact exists."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    company = f"AcmeVerified-{uuid.uuid4().hex[:6]}"
    hr_email = f"talent@{company.lower()}.com"

    # Seed verified HR contact in hr_contacts table
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO hr_contacts (company, hr_name, hr_email, verified, source)
            VALUES (?, ?, ?, 1, 'f4_test')
            """,
            (company, "Sarah Talent", hr_email)
        )

    job = make_test_job("AI Workflow Engineer", company=company, portal="internshala")
    db.add_job(job)

    # Pre-approve the job application in approval queue so safety gate permits execution
    qid = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=job["job_hash"],
        title=job["title"],
        company=job["company"],
        details="Pre-approved for unsupported portal test",
        preview_payload=job,
        score=8.8
    )
    service.approve_item(qid, notes="Pre-approved application")

    # Apply to job: unsupported portal handler returns manual_required and triggers cold email staging
    res = service.apply_to_job(job, queue_id=qid)

    assert res["status"] == "manual_required"
    assert res.get("cold_email_queue_id") is not None

    # Verify cold_email item in approval_queue
    with db._get_conn() as conn:
        q_item = conn.execute(
            """
            SELECT id, item_type, status, reference_id, preview_payload
            FROM approval_queue
            WHERE reference_id = ? AND item_type = 'cold_email'
            """,
            (job["job_hash"],)
        ).fetchone()

    assert q_item is not None, "cold_email approval queue item must exist"
    assert q_item["item_type"] == "cold_email"
    assert q_item["status"] == "pending", "New cold email must be pending human review"

    print("  ✅ Test 1 Passed: Unsupported portal staged to cold_email approval queue with verified HR contact")


def test_2_repeated_runs_prevent_duplicate_cold_email():
    """2. Repeated workflow runs do not create duplicate cold_email queue items."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    company = f"DupTestCorp-{uuid.uuid4().hex[:6]}"
    hr_email = f"careers@{company.lower()}.com"

    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO hr_contacts (company, hr_name, hr_email, verified, source)
            VALUES (?, ?, ?, 1, 'f4_test')
            """,
            (company, "Alex Recruiter", hr_email)
        )

    job = make_test_job("Python Platform Engineer", company=company, portal="linkedin")
    db.add_job(job)

    # First routing call
    qid1 = service.route_unsupported_to_cold_email(job, score=9.0)
    assert qid1 is not None

    # Second routing call (simulating repeat run)
    qid2 = service.route_unsupported_to_cold_email(job, score=9.0)
    assert qid2 == qid1, "Repeated routing must return existing queue ID"

    # Verify only ONE entry exists in approval_queue
    with db._get_conn() as conn:
        items = conn.execute(
            """
            SELECT id FROM approval_queue
            WHERE reference_id = ? AND item_type = 'cold_email'
            """,
            (job["job_hash"],)
        ).fetchall()

    assert len(items) == 1, f"Expected exactly 1 queue item, found {len(items)}"
    print("  ✅ Test 2 Passed: Duplicate cold_email queue items strictly prevented on repeated runs")


def test_3_missing_recruiter_email_blocks_staging():
    """3. Missing recruiter email does NOT create a sendable outreach queue item."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    # Company with NO verified contact in hr_contacts
    company = f"UnverifiedCorp-{uuid.uuid4().hex[:8]}"
    job = make_test_job("Unknown Recruiter Role", company=company, portal="weworkremotely")
    db.add_job(job)

    qid = service.route_unsupported_to_cold_email(job, score=8.0)
    assert qid is None, "Missing or unverified contact must return None"

    with db._get_conn() as conn:
        items = conn.execute(
            """
            SELECT id FROM approval_queue
            WHERE reference_id = ? AND item_type = 'cold_email'
            """,
            (job["job_hash"],)
        ).fetchall()

    assert len(items) == 0, "No approval queue items should be created for unverified contacts"
    print("  ✅ Test 3 Passed: Missing/unverified recruiter email correctly blocks cold email queue creation")


def test_4_no_email_sent_without_human_approval():
    """4. No email is sent without explicit human approval (pending remains blocked)."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)
    email_service = EmailService(db_manager=db, dry_run=False, require_approval=True)

    company = f"ApprovalGateCorp-{uuid.uuid4().hex[:6]}"
    hr_email = f"lead@{company.lower()}.com"

    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO hr_contacts (company, hr_name, hr_email, verified, source)
            VALUES (?, ?, ?, 1, 'f4_test')
            """,
            (company, "Dave Lead", hr_email)
        )

    job = make_test_job("Lead Agentic Engineer", company=company, portal="wellfound")
    db.add_job(job)

    qid = service.route_unsupported_to_cold_email(job, score=9.2)
    assert qid is not None

    # Attempt to send email directly while status='pending' (unapproved)
    # email_service.send_approved_outreach must raise PermissionError
    blocked = False
    try:
        email_service.send_approved_outreach(qid)
    except PermissionError:
        blocked = True
    except Exception as e:
        blocked = "not approved" in str(e).lower()

    assert blocked is True, "Sending unapproved cold email must be strictly blocked"

    # Verify no email was dispatched
    with db._get_conn() as conn:
        app_row = conn.execute(
            "SELECT email_sent FROM applications WHERE job_hash = ?",
            (job["job_hash"],)
        ).fetchone()
        assert app_row["email_sent"] == 0 or app_row["email_sent"] is None

        hist = conn.execute(
            "SELECT COUNT(*) FROM email_history WHERE recipient_email = ?",
            (hr_email,)
        ).fetchone()[0]
        assert hist == 0, "No email history record should exist for blocked unapproved email"

    print("  ✅ Test 4 Passed: Unapproved cold email strictly blocked by human approval safety gate")


def test_5_application_status_remains_manual_required():
    """5. Application status remains manual_required until confirmed action changes it."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    company = f"StatusPreserveCorp-{uuid.uuid4().hex[:6]}"
    job = make_test_job("Full Stack Engineer", company=company, portal="naukri")
    db.add_job(job)

    # Pre-approve and apply
    qid = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=job["job_hash"],
        title=job["title"],
        company=job["company"],
        details="Pre-approved for status test",
        preview_payload=job,
        score=7.9
    )
    service.approve_item(qid, notes="Pre-approved")

    res = service.apply_to_job(job, queue_id=qid)
    assert res["status"] == "manual_required"

    # Verify applications table
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT status, email_sent FROM applications WHERE job_hash = ?",
            (job["job_hash"],)
        ).fetchone()

    assert row is not None
    assert row["status"] == "manual_required", f"Expected manual_required, got {row['status']}"
    assert row["status"] != "applied", "Unsupported portal must NOT be marked as applied"
    assert row["email_sent"] in (0, None), "Email must not be marked sent"

    print("  ✅ Test 5 Passed: Application status preserved as 'manual_required' in SQLite")


def test_6_randomized_delays_stay_within_bounds():
    """6. Randomized delays stay within configured bounds."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db)

    min_cfg = cfg.BROWSER_MIN_DELAY
    max_cfg = cfg.BROWSER_MAX_DELAY
    assert min_cfg > 0
    assert max_cfg >= min_cfg

    # 100 sample iterations of default bounds
    delays = [service.get_random_delay() for _ in range(100)]
    for d in delays:
        assert min_cfg <= d <= max_cfg, f"Delay {d} out of bounds [{min_cfg}, {max_cfg}]"

    # Custom bounds
    custom_delays = [service.get_random_delay(min_sec=0.2, max_sec=0.6) for _ in range(50)]
    for cd in custom_delays:
        assert 0.2 <= cd <= 0.6, f"Custom delay {cd} out of bounds [0.2, 0.6]"

    # Inverted bounds handling
    inv_delay = service.get_random_delay(min_sec=1.5, max_sec=0.5)
    assert 0.5 <= inv_delay <= 1.5

    # Check variation (not fixed constant)
    assert len(set(round(d, 4) for d in delays)) > 10, "Delays must be genuinely randomized"

    print(f"  ✅ Test 6 Passed: Randomized delays respect bounds [{min_cfg}s, {max_cfg}s] with true variation")


def test_7_duplicate_application_protection():
    """7. Duplicate application protection remains intact."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=False)

    job = make_test_job("Duplicate Protection Test Role")
    db.add_job(job)

    # Initial check should allow
    allowed1, reason1 = service.can_apply(job)
    assert allowed1 is True, f"Initial check should allow, got: {reason1}"

    # Mark as applied
    db.update_application_status(job["job_hash"], status="applied", notes="Applied in F4 test")

    # Second check must strictly block
    allowed2, reason2 = service.can_apply(job)
    assert allowed2 is False, "Double apply must be blocked"
    assert "REJECT_ALREADY_APPLIED" in reason2

    print("  ✅ Test 7 Passed: Double-apply protection strictly prevents repeat applications")


def test_8_human_approval_safety_gate():
    """8. Human approval safety gate remains intact."""
    db = DatabaseManager(cfg.DB_PATH)
    service = ApplyService(db_manager=db, dry_run=False, require_approval=True)

    job = make_test_job("Unapproved Safety Gate Role")
    db.add_job(job)

    # Attempt to apply without human approval in approval_queue
    res = service.apply_to_job(job)

    assert res["status"] == "awaiting_approval"
    assert "Explicit human approval is required" in res["reason"] or "SAFETY_GATE_ACTIVE" in res["reason"]

    # Verify applications status in DB remains 'new' / not 'applied'
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM applications WHERE job_hash = ?",
            (job["job_hash"],)
        ).fetchone()

    assert row is not None
    assert row["status"] != "applied", "Status must NOT change to applied without approval"

    print("  ✅ Test 8 Passed: Safety gate blocks unapproved applications without status leakage")


if __name__ == "__main__":
    print("\nRunning F4 Automated Application Engine Fixes Regression Test Suite...\n")

    tests = [
        test_1_unsupported_portal_creates_cold_email_item,
        test_2_repeated_runs_prevent_duplicate_cold_email,
        test_3_missing_recruiter_email_blocks_staging,
        test_4_no_email_sent_without_human_approval,
        test_5_application_status_remains_manual_required,
        test_6_randomized_delays_stay_within_bounds,
        test_7_duplicate_application_protection,
        test_8_human_approval_safety_gate,
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

    print(f"\n{'='*70}")
    if failed_count == 0:
        print(f"🎉 ALL {passed_count} F4 REGRESSION TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*70}\n")
    sys.exit(0 if failed_count == 0 else 1)
