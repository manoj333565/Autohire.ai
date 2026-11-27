"""
tests/test_email_service.py — Unit & Integration Tests for Recruiter Discovery & Cold Email Engine (Milestone 9)
================================================================================================================
Validates:
  T1: Waterfall recruiter contact discovery & MX deliverability checks
  T2: Responsive HTML & MIME email message building with resume attachment
  T3: Safe dry-run email simulation (no external socket connection)
  T4: 3-Layer IMAP message classification (hard bounce, auto-reply, positive response)
  T5: Mandatory human approval safety gate on cold outreach
  T6: End-to-end outreach staging, candidate approval, and dispatch lifecycle
  T7: Inbox monitoring updates to SQLite applications (interview status & bounce invalidation)
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
from tools.email_finder import EmailFinder, clean_company_name, guess_company_domains
from tools.email_sender import EmailSender
from tools.inbox_monitor import (
    InboxMonitor,
    CATEGORY_HARD_BOUNCE,
    CATEGORY_AUTO_REPLY,
    CATEGORY_POSITIVE_REPLY,
    CATEGORY_REJECTION
)
from services.email_service import EmailService


def test_waterfall_email_discovery():
    """T1: Verify company name cleanup, domain deduction, and pattern discovery."""
    assert clean_company_name("ScaleAI Technologies Inc.") == "ScaleAI"
    domains = guess_company_domains("ScaleAI Technologies")
    assert "scaleai.com" in domains

    finder = EmailFinder(db_manager=None)
    res = finder.find_hr_email("DeepMind Innovations", job_url="https://deepmind.com/careers/101")

    assert res["email"] is not None
    assert "@" in res["email"]
    assert res["domain"] in ["deepmind.com", "deepmindinnovations.com"]
    assert res["source"] in ["pattern_generator", "apollo", "snov", "tavily"]
    assert "alternates" in res
    print(f"  ✅ test_waterfall_email_discovery passed ({res['email']} via {res['source']})")


def test_email_message_building():
    """T2: Verify MIME multipart email construction and resume PDF attachment."""
    sender = EmailSender(dry_run=True)
    msg = sender.build_message(
        recipient_email="careers@scaleai.com",
        job_title="AI/ML Engineer",
        company_name="ScaleAI",
        recipient_name="Sarah"
    )

    assert "Application for AI/ML Engineer" in msg["Subject"]
    assert cfg.CANDIDATE_NAME in msg["Subject"]
    assert msg["To"] == "careers@scaleai.com"

    # Verify message payload contains HTML and attachment
    payloads = msg.get_payload()
    assert len(payloads) >= 2  # alternative text part + PDF attachment part

    # Check resume PDF attachment
    pdf_parts = [p for p in payloads if p.get_content_type() == "application/pdf"]
    assert len(pdf_parts) == 1, "Resume PDF attachment missing in MIME message"
    disp = pdf_parts[0].get("Content-Disposition", "")
    assert "attachment" in disp
    assert ".pdf" in disp

    print("  ✅ test_email_message_building passed")


def test_dry_run_email_dispatch():
    """T3: Verify that dry-run mode returns simulated sent status without network dispatch."""
    sender = EmailSender(dry_run=True)
    res = sender.send_cold_email(
        recipient_email="talent@innovate.ai",
        job_title="Generative AI Specialist",
        company_name="InnovateAI"
    )

    assert res["status"] == "simulated_sent"
    assert res["dry_run"] is True
    assert res["has_attachment"] is True
    assert "talent@innovate.ai" == res["recipient"]

    print("  ✅ test_dry_run_email_dispatch passed")


def test_inbox_monitor_classification():
    """T4: Verify 3-layer message classification logic."""
    monitor = InboxMonitor(dry_run=True)

    # 1. Hard Bounce
    bounce_res = monitor.classify_message(
        subject="Delivery Status Notification (Failure)",
        body="550 5.1.1 User unknown. Mailbox unavailable.",
        sender="mailer-daemon@google.com"
    )
    assert bounce_res["category"] == CATEGORY_HARD_BOUNCE
    assert bounce_res["action_required"] == "mark_email_invalid"

    # 2. Auto-Reply
    auto_res = monitor.classify_message(
        subject="Automatic reply: Out of office",
        body="I am away from the office on annual leave until next week.",
        sender="hr@company.com"
    )
    assert auto_res["category"] == CATEGORY_AUTO_REPLY

    # 3. Positive Recruiter Reply (Interview Request)
    positive_res = monitor.classify_message(
        subject="Invitation to Interview: AI/ML Engineer",
        body="Hi Manoj, We were impressed by your resume and would like to schedule a phone screen.",
        sender="recruiter@innovators.com"
    )
    assert positive_res["category"] == CATEGORY_POSITIVE_REPLY
    assert positive_res["action_required"] == "alert_candidate_interview"

    # 4. Rejection
    rejection_res = monitor.classify_message(
        subject="Update on your application",
        body="Unfortunately, we have decided to pursue other candidates whose experience more closely matches our needs.",
        sender="careers@company.com"
    )
    assert rejection_res["category"] == CATEGORY_REJECTION

    print("  ✅ test_inbox_monitor_classification passed")


def test_human_approval_safety_gate():
    """T5: Verify that sending unapproved outreach in live mode raises PermissionError."""
    db = DatabaseManager(cfg.DB_PATH)
    service = EmailService(db_manager=db, dry_run=False, require_approval=True)

    unique_hash = uuid.uuid4().hex[:10]
    # Enqueue unapproved item (status='pending')
    qid = db.enqueue_for_approval(
        item_type="cold_email",
        reference_id=unique_hash,
        title="Cold Outreach Test",
        company="BlockCo",
        details="Score: 8.5",
        preview_payload={"recipient_email": "careers@blockco.com", "title": "AI Engineer", "company": "BlockCo"},
        score=8.5
    )

    try:
        service.send_approved_outreach(qid)
        assert False, "Safety gate failed: unapproved live email must raise PermissionError"
    except PermissionError as e:
        assert "has status 'pending'" in str(e) or "not 'approved'" in str(e)
        print("  ✅ test_human_approval_safety_gate passed")


def test_outreach_staging_and_dispatch_lifecycle():
    """T6: End-to-end lifecycle: discovery -> queue staging -> candidate approval -> dispatch."""
    db = DatabaseManager(cfg.DB_PATH)
    service = EmailService(db_manager=db, dry_run=True, require_approval=True)

    test_url = f"https://remoteok.com/l/{uuid.uuid4().hex[:10]}"
    job_hash = db.make_job_hash(test_url)
    job = {
        "title": "Agentic AI Developer",
        "company": "ScaleAI Lab",
        "job_url": test_url,
        "url": test_url,
        "job_hash": job_hash
    }
    db.add_job(job)

    # Pre-seed verified contact in SQLite (Stage 1 Waterfall discovery)
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO hr_contacts (company, company_domain, hr_name, hr_email, source, verified)
            VALUES ('ScaleAI Lab', 'scaleai.com', 'Sarah Recruiter', 'careers@scaleai.com', 'apollo', 1)
            """
        )

    # 1. Stage in approval queue
    qid = service.stage_outreach_for_approval(job, score=9.2)
    assert qid is not None

    # 2. Verify pending status
    with db._get_conn() as conn:
        row = conn.execute("SELECT status FROM approval_queue WHERE id = ?", (qid,)).fetchone()
        assert row["status"] == "pending"

    # 3. Candidate approves
    db.decide_approval(qid, decision="approved", notes="Looks great, approve outreach.")

    # 4. Dispatch outreach
    res = service.send_approved_outreach(qid)
    assert res["status"] in ["simulated_sent", "sent"]

    # 5. Verify database updates
    with db._get_conn() as conn:
        app_row = conn.execute("SELECT email_sent, hr_email FROM applications WHERE job_hash = ?", (job_hash,)).fetchone()
        assert app_row is not None
        assert app_row["email_sent"] == (1 if not service.dry_run else 0)
        assert app_row["hr_email"] is not None

    print("  ✅ test_outreach_staging_and_dispatch_lifecycle passed")


def test_inbox_scan_database_updates():
    """T7: Verify inbox scan updates application status to 'interview' upon recruiter response."""
    db = DatabaseManager(cfg.DB_PATH)
    service = EmailService(db_manager=db, dry_run=True)

    # Insert test application with known HR email
    test_hr_email = "recruiter@scaleai.com"
    test_url = f"https://example.com/jobs/{uuid.uuid4().hex[:10]}"
    job_hash = db.make_job_hash(test_url)
    db.add_job({
        "title": "Python ML Engineer",
        "company": "ScaleAI",
        "job_url": test_url,
        "url": test_url,
        "job_hash": job_hash
    })
    with db._get_conn() as conn:
        conn.execute("UPDATE applications SET hr_email = ?, status = 'applied' WHERE job_hash = ?", (test_hr_email, job_hash))
        conn.execute("DELETE FROM inbox_processed_messages WHERE imap_uid LIKE 'mock_uid_%'")

    # Run inbox monitor scan cycle
    scan_res = service.run_inbox_monitor_cycle(days_back=3)

    assert scan_res["processed_count"] > 0
    assert scan_res["stats"]["interviews_detected"] >= 1
    assert scan_res["stats"]["bounces_handled"] >= 1

    # Verify that the application was updated to 'interview'
    with db._get_conn() as conn:
        row = conn.execute("SELECT status, notes FROM applications WHERE job_hash = ?", (job_hash,)).fetchone()
        assert row is not None
        assert row["status"] == "interview", f"Expected status 'interview', got '{row['status']}'"
        assert "Positive reply" in row["notes"]

    print("  ✅ test_inbox_scan_database_updates passed")


if __name__ == "__main__":
    print("\nRunning Milestone 9 Recruiter Discovery & Cold Outreach Tests...\n")

    tests = [
        test_waterfall_email_discovery,
        test_email_message_building,
        test_dry_run_email_dispatch,
        test_inbox_monitor_classification,
        test_human_approval_safety_gate,
        test_outreach_staging_and_dispatch_lifecycle,
        test_inbox_scan_database_updates,
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
        print(f"🎉 ALL {passed_count} RECRUITER OUTREACH & INBOX TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
