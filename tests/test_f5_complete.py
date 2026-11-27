"""
tests/test_f5_complete.py — 20 Comprehensive Regression Tests for F5 Milestone
=============================================================================
Validates all Mandatory and Bonus Requirements:
  M1 (T1-T6): Waterfall HR email discovery (SQLite cache, Apollo, Snov, Heuristic Patterns, MX validation, Safety guard)
  M2 (T7): LLM-based personalized cold email generation
  M3 (T8-T9): Resume attachment guard (magic PDF header validation & missing file rejection)
  M4 (T10): Mandatory human approval safety gate
  M5 (T11-T12): Persistent daily sending limit tracking in SQLite (MAX_EMAILS_PER_DAY)
  M6 (T13): Inter-email pacing delays (EMAIL_DELAY_SECONDS)
  M7 (T14): Atomic execution claims (concurrency duplicate protection)
  B1 (T15): IMAP message UID deduplication in SQLite
  B2 (T16): DSN hard bounce recipient extraction (bypassing mailer-daemon)
  B3 (T17): Hard bounce multi-table contact invalidation
  B4 (T18-T20): CSV secondary HR drip campaign (Import & dedup, staging & approval gate, execution & stats)
"""

import os
import sys
import uuid
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from tools.email_finder import EmailFinder, clean_company_name, guess_company_domains, verify_mx_record
from tools.email_sender import EmailSender
from tools.inbox_monitor import (
    InboxMonitor,
    extract_bounced_recipient,
    CATEGORY_HARD_BOUNCE,
    CATEGORY_AUTO_REPLY,
    CATEGORY_POSITIVE_REPLY,
    CATEGORY_REJECTION
)
from services.email_service import EmailService
from services.drip_service import DripService
from services.scoring_service import ScoringService


# ─── T1: Waterfall Stage 1 - SQLite Verified Contact Cache ───────────────────
def test_waterfall_stage1_sqlite_cache():
    """T1: Verify that existing verified recruiter contacts in SQLite take immediate precedence."""
    db = DatabaseManager(cfg.DB_PATH)
    finder = EmailFinder(db_manager=db)

    test_company = f"AcmeCorp{uuid.uuid4().hex[:6]}"
    test_email = f"lead.recruiter@{test_company.lower()}.com"

    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO hr_contacts (company, company_domain, hr_name, hr_email, source, verified)
            VALUES (?, ?, 'Alice Walker', ?, 'apollo_verified', 1)
            """,
            (test_company, f"{test_company.lower()}.com", test_email)
        )

    result = finder.find_hr_email(test_company, "")
    assert result["email"] == test_email
    assert result["verified"] is True
    assert "verified_db" in result["source"] or result["source"] == "sqlite_contacts"
    print("  ✅ T1: test_waterfall_stage1_sqlite_cache passed")


# ─── T2: Waterfall Stage 2 - Apollo API Discovery ─────────────────────────────
def test_waterfall_stage2_apollo_configured():
    """T2: Verify Apollo discovery is invoked when configured and SQLite cache misses."""
    db = DatabaseManager(cfg.DB_PATH)
    finder = EmailFinder(db_manager=db)
    test_company = f"ApolloTest{uuid.uuid4().hex[:6]}"

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "person": {"name": "John Doe", "email": f"john@{test_company.lower()}.com"}
    }

    with patch("requests.post", return_value=mock_resp), \
         patch.object(cfg, "USE_APOLLO", True), \
         patch.object(cfg, "APOLLO_API_KEY", "test_apollo_key"):
        result = finder.find_hr_email(test_company, f"https://{test_company.lower()}.com")
        assert result["email"] == f"john@{test_company.lower()}.com"
        assert result["source"] == "apollo"
        assert result["verified"] is True
    print("  ✅ T2: test_waterfall_stage2_apollo_configured passed")


# ─── T3: Waterfall Stage 3 - Snov API Fallback ────────────────────────────────
def test_waterfall_stage3_snov_fallback():
    """T3: Verify Snov fallback occurs when Apollo discovery returns no contact."""
    db = DatabaseManager(cfg.DB_PATH)
    finder = EmailFinder(db_manager=db)
    test_company = f"SnovTest{uuid.uuid4().hex[:6]}"

    mock_snov_token_resp = MagicMock()
    mock_snov_token_resp.status_code = 200
    mock_snov_token_resp.json.return_value = {"access_token": "mock_token"}

    mock_snov_search_resp = MagicMock()
    mock_snov_search_resp.status_code = 200
    mock_snov_search_resp.json.return_value = {
        "emails": [{"email": f"hiring@{test_company.lower()}.com", "firstName": "Bob"}]
    }

    def fake_post_or_get(*args, **kwargs):
        url = args[0] if args else kwargs.get("url", "")
        if "oauth/access_token" in url:
            return mock_snov_token_resp
        return mock_snov_search_resp

    with patch.object(cfg, "USE_APOLLO", False), \
         patch.object(cfg, "USE_SNOV", True), \
         patch.object(cfg, "SNOV_CLIENT_ID", "mock_id"), \
         patch.object(cfg, "SNOV_CLIENT_SECRET", "mock_secret"), \
         patch("requests.post", side_effect=fake_post_or_get), \
         patch("requests.get", return_value=mock_snov_search_resp):
        result = finder.find_hr_email(test_company, f"https://{test_company.lower()}.com")
        assert result["email"] == f"hiring@{test_company.lower()}.com"
        assert result["source"] == "snov"
        assert result["verified"] is True
    print("  ✅ T3: test_waterfall_stage3_snov_fallback passed")


# ─── T4: Waterfall Stage 4 - Heuristic Pattern Generation ─────────────────────
def test_waterfall_stage4_heuristic_pattern_generation():
    """T4: Verify heuristic candidate generation generates standard recruiter patterns."""
    finder = EmailFinder(db_manager=None)
    with patch.object(cfg, "APOLLO_API_KEY", ""), \
         patch.object(cfg, "SNOV_CLIENT_ID", ""):
        result = finder.find_hr_email("DeepMind Technologies", "https://deepmind.com/careers")
        assert result["domain"] in ("deepmind.com", "deepmindtechnologies.com")
        assert result["source"] == "pattern_generator"
        assert "@" in result["email"]
        assert len(result.get("alternates", [])) >= 2
    print("  ✅ T4: test_waterfall_stage4_heuristic_pattern_generation passed")


# ─── T5: Waterfall Stage 5 - MX Record Deliverability Validation ──────────────
def test_waterfall_stage5_mx_record_validation():
    """T5: Verify MX DNS record check confirms deliverable domains and rejects non-existent ones."""
    # Known valid domain
    is_valid_gmail = verify_mx_record("gmail.com")
    assert is_valid_gmail is True

    # Known invalid domain
    is_valid_fake = verify_mx_record("this-domain-does-not-exist-xyz123456789.org")
    assert is_valid_fake is False
    print("  ✅ T5: test_waterfall_stage5_mx_record_validation passed")


# ─── T6: Waterfall Safety - Unverified Patterns Cannot Be Staged/Sent ─────────
def test_waterfall_safety_unverified_not_staged():
    """T6: Verify that pattern-generated candidate emails have verified=False and cannot be staged."""
    db = DatabaseManager(cfg.DB_PATH)
    service = EmailService(db_manager=db, dry_run=True, require_approval=True)

    unverified_company = f"PatternOnlyCorp_{uuid.uuid4().hex[:6]}"
    test_url = f"https://{unverified_company.lower()}.com/jobs/1"

    # Stage outreach for a company without pre-verified contact
    qid = service.stage_outreach_for_approval({
        "company": unverified_company,
        "title": "AI Engineer",
        "job_url": test_url
    }, score=9.0)

    # Must be None because contact.verified is False
    assert qid is None, "Safety violation: Unverified pattern email was staged in approval queue!"
    print("  ✅ T6: test_waterfall_safety_unverified_not_staged passed")


# ─── T7: LLM Cold Email Personalization ───────────────────────────────────────
def test_llm_email_personalization():
    """T7: Verify that generated cold emails contain candidate name, skills, and job context."""
    scoring = ScoringService()
    job = {
        "title": "Senior AI/ML Engineer",
        "company": "ScaleAI Lab",
        "description": "Looking for PyTorch, LangChain, and Agentic AI expertise."
    }
    pitch = scoring.generate_recruiter_email(job)
    assert "subject" in pitch
    assert "body_text" in pitch
    assert cfg.CANDIDATE_NAME in pitch["subject"] or cfg.CANDIDATE_NAME in pitch["body_text"]
    assert "ScaleAI Lab" in pitch["subject"] or "ScaleAI Lab" in pitch["body_text"]
    assert len(pitch["body_text"]) > 100
    print("  ✅ T7: test_llm_email_personalization passed")


# ─── T8: Strict Resume Attachment Guard - Valid PDF ───────────────────────────
def test_resume_attachment_guard_valid_pdf():
    """T8: Verify EmailSender validates %PDF- magic header and attaches valid PDF."""
    sender = EmailSender(dry_run=True, resume_path=cfg.RESUME_PATH)
    msg = sender.build_message(
        recipient_email="recruiter@example.com",
        job_title="Python Developer",
        company_name="TechCorp"
    )
    assert msg["To"] == "recruiter@example.com"
    pdf_parts = [p for p in msg.get_payload() if p.get_content_type() == "application/pdf"]
    assert len(pdf_parts) == 1
    assert "attachment" in pdf_parts[0].get("Content-Disposition", "")
    print("  ✅ T8: test_resume_attachment_guard_valid_pdf passed")


# ─── T9: Strict Resume Attachment Guard - Missing / Corrupt PDF Rejection ────
def test_resume_attachment_guard_missing_pdf():
    """T9: Verify missing or corrupt resume PDF strictly raises FileNotFoundError or ValueError."""
    # 1. Non-existent file
    sender_missing = EmailSender(dry_run=True, resume_path="d:/does_not_exist/resume.pdf")
    try:
        sender_missing.build_message("recruiter@example.com", "Dev", "Co")
        assert False, "Should have raised FileNotFoundError for missing resume"
    except FileNotFoundError:
        pass

    # 2. Corrupt / Non-PDF file
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tf:
        tf.write(b"NOT A REAL PDF FILE HEADER")
        fake_pdf_path = tf.name

    try:
        sender_corrupt = EmailSender(dry_run=True, resume_path=fake_pdf_path)
        sender_corrupt.build_message("recruiter@example.com", "Dev", "Co")
        assert False, "Should have raised ValueError for non-PDF magic header"
    except ValueError as e:
        assert "not a valid pdf" in str(e).lower()
    finally:
        if os.path.exists(fake_pdf_path):
            os.remove(fake_pdf_path)

    print("  ✅ T9: test_resume_attachment_guard_missing_pdf passed")


# ─── T10: Mandatory Human Approval Safety Gate ────────────────────────────────
def test_human_approval_gate_strictly_enforced():
    """T10: Verify unapproved queue items (status='pending') raise PermissionError."""
    db = DatabaseManager(cfg.DB_PATH)
    service = EmailService(db_manager=db, dry_run=False, require_approval=True)

    qid = db.enqueue_for_approval(
        item_type="cold_email",
        reference_id=uuid.uuid4().hex[:8],
        title="Test Gate",
        company="GateCorp",
        details="Awaiting approval",
        preview_payload={"recipient_email": "careers@gatecorp.com", "title": "Dev", "company": "GateCorp"}
    )

    try:
        service.send_approved_outreach(qid)
        assert False, "Safety gate failed: unapproved email must raise PermissionError"
    except PermissionError as e:
        assert "not 'approved'" in str(e) or "has status 'pending'" in str(e)
    print("  ✅ T10: test_human_approval_gate_strictly_enforced passed")


# ─── T11: Persistent Daily Sending Limit Tracking ─────────────────────────────
def test_daily_email_limit_persistent_tracking():
    """T11: Verify daily email count tracks only 'sent' emails in SQLite email_history."""
    db = DatabaseManager(cfg.DB_PATH)
    initial_count = db.get_daily_sent_email_count()

    # Insert simulated sent email today
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO email_history (recipient_email, subject, body, email_type, status, sent_at)
            VALUES ('test_limit@example.com', 'Subject', 'Body content', 'job_outreach', 'sent', CURRENT_TIMESTAMP)
            """
        )

    updated_count = db.get_daily_sent_email_count()
    assert updated_count == initial_count + 1

    # Insert failed or dry-run email (should NOT count toward daily limit)
    with db._get_conn() as conn:
        conn.execute(
            """
            INSERT INTO email_history (recipient_email, subject, body, email_type, status, sent_at)
            VALUES ('failed@example.com', 'Subject', 'Body content', 'job_outreach', 'failed', CURRENT_TIMESTAMP)
            """
        )

    assert db.get_daily_sent_email_count() == updated_count
    print("  ✅ T11: test_daily_email_limit_persistent_tracking passed")


# ─── T12: Daily Sending Limit Blocks at Max (25/day) ──────────────────────────
def test_daily_email_limit_blocks_at_max():
    """T12: Verify can_send_email() returns False when MAX_EMAILS_PER_DAY is reached."""
    db = DatabaseManager(cfg.DB_PATH)

    with patch.object(db, "get_daily_sent_email_count", return_value=cfg.MAX_EMAILS_PER_DAY):
        can_send, reason = db.can_send_email()
        assert can_send is False
        assert f"{cfg.MAX_EMAILS_PER_DAY}/{cfg.MAX_EMAILS_PER_DAY}" in reason

    with patch.object(db, "get_daily_sent_email_count", return_value=cfg.MAX_EMAILS_PER_DAY - 1):
        can_send, reason = db.can_send_email()
        assert can_send is True
    print("  ✅ T12: test_daily_email_limit_blocks_at_max passed")


# ─── T13: Inter-Email Pacing Delay ────────────────────────────────────────────
def test_inter_email_pacing_delay():
    """T13: Verify apply_inter_email_delay calculates sleep times based on last send."""
    service = EmailService(dry_run=True)
    import time

    service._last_send_timestamp = time.time() - 2.0
    # Request 3.0s delay -> sleep needed is ~1.0s
    with patch("time.sleep") as mock_sleep:
        service.apply_inter_email_delay(delay_seconds=3.0)
        assert mock_sleep.called
        sleep_arg = mock_sleep.call_args[0][0]
        assert 0.5 <= sleep_arg <= 1.5
    print("  ✅ T13: test_inter_email_pacing_delay passed")


# ─── T14: Atomic Execution Claims Concurrency Protection ──────────────────────
def test_atomic_execution_claims_concurrency():
    """T14: Verify claim_email_execution allows only one worker to claim an approved queue item."""
    db = DatabaseManager(cfg.DB_PATH)
    qid = db.enqueue_for_approval(
        item_type="cold_email",
        reference_id=uuid.uuid4().hex[:8],
        title="Concurrent Test",
        company="SyncCorp",
        details="Testing concurrency",
        preview_payload={"recipient_email": "sync@synccorp.com"}
    )
    db.decide_approval(qid, decision="approved")

    # Worker 1 claims
    token1 = db.claim_email_execution(qid)
    assert token1 is not None

    # Worker 2 attempts to claim simultaneously
    token2 = db.claim_email_execution(qid)
    assert token2 is None, "Concurrency error: Second worker claimed the same email execution!"

    # Finalize worker 1 claim
    finalized = db.finalize_email_execution(qid, token1, "sent")
    assert finalized is True
    print("  ✅ T14: test_atomic_execution_claims_concurrency passed")


# ─── T15: IMAP Message UID Deduplication in SQLite ────────────────────────────
def test_imap_uid_deduplication():
    """T15: Verify is_inbox_message_processed and record_inbox_processed_message prevent duplicate scans."""
    db = DatabaseManager(cfg.DB_PATH)
    test_uid = f"uid_{uuid.uuid4().hex[:8]}"

    # Initially unprocessed
    assert db.is_inbox_message_processed("INBOX", test_uid) is False

    # Record processing
    recorded = db.record_inbox_processed_message(
        mailbox="INBOX",
        imap_uid=test_uid,
        message_id="<test@msg>",
        sender="hr@company.com",
        subject="Interview Invitation",
        classification="positive_reply"
    )
    assert recorded is True

    # Now marked processed
    assert db.is_inbox_message_processed("INBOX", test_uid) is True

    # Re-recording same UID does nothing (conflict ignored)
    re_recorded = db.record_inbox_processed_message(
        mailbox="INBOX",
        imap_uid=test_uid,
        subject="Duplicate Attempt"
    )
    assert re_recorded is False
    print("  ✅ T15: test_imap_uid_deduplication passed")


# ─── T16: DSN Hard Bounce Recipient Extraction ────────────────────────────────
def test_hard_bounce_recipient_extraction():
    """T16: Verify extract_bounced_recipient extracts actual recipient from DSN body (not mailer-daemon)."""
    dsn_body_1 = (
        "Delivery to the following recipient failed permanently:\n\n"
        "    careers@failingstartup.io\n\n"
        "Technical details of permanent failure:\n"
        "Google tried to deliver your message, but it was rejected by the server for the recipient domain."
    )
    recipient1 = extract_bounced_recipient(dsn_body_1, "mailer-daemon@googlemail.com")
    assert recipient1 == "careers@failingstartup.io"

    dsn_body_2 = (
        "Action: failed\n"
        "Final-Recipient: rfc822; talent.lead@deadcompany.com\n"
        "Status: 5.1.1\n"
    )
    recipient2 = extract_bounced_recipient(dsn_body_2, "postmaster@deadcompany.com")
    assert recipient2 == "talent.lead@deadcompany.com"
    print("  ✅ T16: test_hard_bounce_recipient_extraction passed")


# ─── T17: Hard Bounce Multi-Table Contact Invalidation ────────────────────────
def test_hard_bounce_invalidation():
    """T17: Verify run_inbox_monitor_cycle invalidates hr_contacts, pdf_hr_outreach, and csv_drip_contacts."""
    db = DatabaseManager(cfg.DB_PATH)
    service = EmailService(db_manager=db, dry_run=True)

    bounced_email = f"bounced_{uuid.uuid4().hex[:6]}@badstartup.xyz"

    with db._get_conn() as conn:
        # Seed hr_contacts
        conn.execute(
            """
            INSERT OR REPLACE INTO hr_contacts (company, company_domain, hr_name, hr_email, verified)
            VALUES ('BadStartup', 'badstartup.xyz', 'Bad Recruiter', ?, 1)
            """,
            (bounced_email,)
        )
        # Seed pdf_hr_outreach
        conn.execute(
            """
            INSERT INTO pdf_hr_outreach (company, email, status)
            VALUES ('BadStartup', ?, 'sent')
            """,
            (bounced_email,)
        )
        # Seed csv_drip_contacts
        conn.execute(
            """
            INSERT INTO csv_drip_contacts (company, name, email, role, status)
            VALUES ('BadStartup', 'Bad Recruiter', ?, 'Lead', 'pending')
            """,
            (bounced_email,)
        )

    # Mock fetch_inbox_activity returning a bounce for this email
    mock_item = {
        "uid": f"bounce_uid_{uuid.uuid4().hex[:6]}",
        "sender": "mailer-daemon@googlemail.com",
        "subject": "Delivery Status Notification (Failure)",
        "snippet": f"Delivery to {bounced_email} failed permanently: 550 User unknown",
        "bounced_recipient": bounced_email,
        "classification": {"category": CATEGORY_HARD_BOUNCE}
    }

    with patch.object(service.monitor, "fetch_inbox_activity", return_value=[mock_item]):
        cycle_res = service.run_inbox_monitor_cycle(days_back=1)
        assert cycle_res["stats"]["bounces_handled"] >= 1

    # Verify invalidation across all three tables
    with db._get_conn() as conn:
        hr_row = conn.execute("SELECT verified FROM hr_contacts WHERE hr_email = ?", (bounced_email,)).fetchone()
        assert hr_row["verified"] == 0

        pdf_row = conn.execute("SELECT status FROM pdf_hr_outreach WHERE email = ?", (bounced_email,)).fetchone()
        assert pdf_row["status"] == "invalid"

        drip_row = conn.execute("SELECT status FROM csv_drip_contacts WHERE email = ?", (bounced_email,)).fetchone()
        assert drip_row["status"] == "bounced"

    print("  ✅ T17: test_hard_bounce_invalidation passed")


# ─── T18: CSV Secondary HR Drip Campaign Import & Deduplication ───────────────
def test_csv_drip_import_and_deduplication():
    """T18: Verify DripService.import_csv parses contacts and prevents duplicate email insertion."""
    db = DatabaseManager(cfg.DB_PATH)
    drip = DripService(db_manager=db, dry_run=True)

    unique_email = f"lead_{uuid.uuid4().hex[:6]}@scaleai.com"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as tf:
        tf.write("company,name,email,role\n")
        tf.write(f"ScaleAI,Sarah,{unique_email},Talent Partner\n")
        tf.write(f"ScaleAI,Sarah Duplicate,{unique_email},Talent Partner\n")  # Duplicate
        tf.write("BrokenCorp,NoEmail,,Recruiter\n")  # Invalid
        csv_path = tf.name

    try:
        res = drip.import_csv(csv_path)
        assert res["imported"] == 1
        assert res["duplicates"] == 1
        assert res["invalid"] == 1
        assert res["total_rows"] == 3
    finally:
        if os.path.exists(csv_path):
            os.remove(csv_path)
    print("  ✅ T18: test_csv_drip_import_and_deduplication passed")


# ─── T19: CSV Secondary HR Drip Campaign Staging & Human Approval ─────────────
def test_csv_drip_staging_for_approval():
    """T19: Verify stage_pending_for_approval enqueues contacts with item_type='drip_email'."""
    db = DatabaseManager(cfg.DB_PATH)
    drip = DripService(db_manager=db, dry_run=True)

    fast_draft = {
        "subject": "Inquiry regarding VP Engineering at InnovateIO",
        "body_text": "Hi Mark, I am interested in opportunities at your team.",
        "body_html": "<p>Hi Mark, I am interested in opportunities at your team.</p>"
    }

    unique_email = f"drip_test_{uuid.uuid4().hex[:6]}@innovate.io"
    with db._get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO csv_drip_contacts (company, name, email, role, status)
            VALUES ('InnovateIO', 'Mark Lead', ?, 'VP Engineering', 'pending')
            """,
            (unique_email,)
        )
        contact_id = cursor.lastrowid

    with patch.object(ScoringService, "generate_recruiter_email", return_value=fast_draft):
        queued_ids = drip.stage_pending_for_approval(limit=5)
    assert len(queued_ids) >= 1

    # Verify queue entry
    with db._get_conn() as conn:
        q_row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (queued_ids[-1],)).fetchone()
        assert q_row["item_type"] == "drip_email"
        assert q_row["status"] == "pending"
        assert "InnovateIO" in q_row["title"]

        c_row = conn.execute("SELECT status, queue_id FROM csv_drip_contacts WHERE id = ?", (contact_id,)).fetchone()
        assert c_row["status"] == "staged"
        assert c_row["queue_id"] == queued_ids[-1]

    print("  ✅ T19: test_csv_drip_staging_for_approval passed")


# ─── T20: CSV Secondary HR Drip Campaign Execution & Stats ────────────────────
def test_csv_drip_approval_and_execution():
    """T20: Verify approval, dry-run dispatch, and stats tracking for CSV drip outreach."""
    db = DatabaseManager(cfg.DB_PATH)
    drip = DripService(db_manager=db, dry_run=True)

    fast_draft = {
        "subject": "Inquiry regarding Head of AI at TechLab",
        "body_text": "Hi Elena, I am interested in opportunities at your team.",
        "body_html": "<p>Hi Elena, I am interested in opportunities at your team.</p>"
    }

    unique_email = f"exec_{uuid.uuid4().hex[:6]}@techlab.ai"
    with db._get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO csv_drip_contacts (company, name, email, role, status)
            VALUES ('TechLab', 'Elena', ?, 'Head of AI', 'pending')
            """,
            (unique_email,)
        )
        contact_id = cursor.lastrowid

    with patch.object(ScoringService, "generate_recruiter_email", return_value=fast_draft):
        queued_ids = drip.stage_pending_for_approval(limit=1)
    qid = queued_ids[-1]

    # 1. Unapproved send must fail
    try:
        drip.send_approved_drip(qid)
        assert False, "Should raise PermissionError for unapproved drip email"
    except PermissionError as e:
        assert "not 'approved'" in str(e)

    # 2. Candidate approves
    db.decide_approval(qid, decision="approved", notes="Looks great!")

    # 3. Dry-run dispatch
    send_res = drip.send_approved_drip(qid)
    assert send_res["status"] == "simulated_sent"
    assert send_res["dry_run"] is True
    assert send_res["recipient"] == unique_email

    # 4. Check stats
    stats = drip.get_stats()
    assert stats["total"] >= 1

    print("  ✅ T20: test_csv_drip_approval_and_execution passed")


# ─── Master Test Suite Runner ────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"\n{'='*75}")
    print("🚀 RUNNING AUTOHIRE.AI F5 COMPLETE COMPREHENSIVE REGRESSION SUITE (20 SCENARIOS)")
    print(f"{'='*75}\n")

    test_suite = [
        test_waterfall_stage1_sqlite_cache,
        test_waterfall_stage2_apollo_configured,
        test_waterfall_stage3_snov_fallback,
        test_waterfall_stage4_heuristic_pattern_generation,
        test_waterfall_stage5_mx_record_validation,
        test_waterfall_safety_unverified_not_staged,
        test_llm_email_personalization,
        test_resume_attachment_guard_valid_pdf,
        test_resume_attachment_guard_missing_pdf,
        test_human_approval_gate_strictly_enforced,
        test_daily_email_limit_persistent_tracking,
        test_daily_email_limit_blocks_at_max,
        test_inter_email_pacing_delay,
        test_atomic_execution_claims_concurrency,
        test_imap_uid_deduplication,
        test_hard_bounce_recipient_extraction,
        test_hard_bounce_invalidation,
        test_csv_drip_import_and_deduplication,
        test_csv_drip_staging_for_approval,
        test_csv_drip_approval_and_execution,
    ]

    passed = 0
    failed = 0

    for test_fn in test_suite:
        try:
            test_fn()
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {test_fn.__name__} FAILED: {e}")
            failed += 1
        except Exception as e:
            print(f"  💥 {test_fn.__name__} ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*75}")
    if failed == 0:
        print(f"🎉 ALL {passed}/20 F5 TEST SCENARIOS PASSED PERFECTLY WITH ZERO FAILURES!")
    else:
        print(f"❌ {failed}/{len(test_suite)} F5 tests FAILED. {passed} passed.")
    print(f"{'='*75}\n")

    sys.exit(0 if failed == 0 else 1)
