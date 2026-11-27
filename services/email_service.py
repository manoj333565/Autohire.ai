"""
services/email_service.py — Cold Email Outreach & Inbox Coordinator (F5)
========================================================================
Coordinates end-to-end recruiter communication:
  - Discovers recruiter contacts via waterfall finder
  - Enforces mandatory human approval safety gate for all email outreach
  - Dispatches personalized HTML cold emails with resume attachment
  - Scans career inbox via IMAP and classifies hard bounces, auto-replies, and interview requests
  - Updates SQLite `email_history`, `hr_contacts`, and `approval_queue`
"""

import time
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import config as cfg
from database.db_manager import DatabaseManager
from tools.email_finder import EmailFinder
from tools.email_sender import EmailSender
from tools.inbox_monitor import (
    InboxMonitor,
    CATEGORY_HARD_BOUNCE,
    CATEGORY_POSITIVE_REPLY,
    CATEGORY_REJECTION,
    extract_bounced_recipient
)

logger = logging.getLogger("CareerPilot.EmailService")


class EmailService:
    """Master recruiter email outreach and inbox monitoring service."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        dry_run: bool = True,
        require_approval: bool = True
    ):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        self.dry_run = dry_run
        self.require_approval = require_approval
        self._last_send_timestamp: Optional[float] = None

        self.finder = EmailFinder(db_manager=self.db)
        self.sender = EmailSender(dry_run=dry_run)
        self.monitor = InboxMonitor(dry_run=dry_run, db_manager=self.db)

    def apply_inter_email_delay(self, delay_seconds: Optional[float] = None) -> float:
        """
        Enforce randomized/configurable pacing delay between consecutive outbound emails.
        Protects SMTP reputation and adheres to provider rate limits.
        """
        target_delay = (
            delay_seconds
            if delay_seconds is not None
            else getattr(cfg, "EMAIL_DELAY_SECONDS", 5.0)
        )
        if self._last_send_timestamp is not None:
            elapsed = time.time() - self._last_send_timestamp
            sleep_needed = max(0.0, target_delay - elapsed)
            if sleep_needed > 0:
                logger.info(f"[EmailService] Applying inter-email pacing delay: {sleep_needed:.2f}s")
                time.sleep(sleep_needed)

        self._last_send_timestamp = time.time()
        return target_delay

    def discover_recruiter(self, company_name: str, job_url: str = "") -> Dict[str, Any]:
        """Discover hiring team contact for an opportunity."""
        return self.finder.find_hr_email(company_name, job_url)

    
    def stage_outreach_for_approval(
        self,
        job: Dict[str, Any],
        score: float = 0.0
    ) -> Optional[int]:
        """
        Stage a cold email only when the recipient is independently
        verified in the HR contacts database.
        """
        company = job.get("company", "Company")
        title = job.get("title", "Role")
        url = job.get("job_url") or job.get("url") or ""
        job_hash = job.get("job_hash") or self.db.make_job_hash(url)

        # 1. Discover recruiter contact.
        contact = self.discover_recruiter(company, url)

        if not isinstance(contact, dict):
            logger.warning(
                "[EmailService] No valid recruiter contact returned for '%s'.",
                company,
            )
            return None

        to_email = contact.get("email")

        # 2. Require explicit verification from the finder.
        if contact.get("verified") is not True:
            logger.info(
                "[EmailService] Outreach staging blocked for '%s': "
                "contact is not verified.",
                company,
            )
            return None

        if not isinstance(to_email, str) or not to_email.strip():
            logger.info(
                "[EmailService] Outreach staging blocked for '%s': "
                "recipient email is missing.",
                company,
            )
            return None

        to_email = to_email.strip().lower()

        # 3. Independently verify the recipient in SQLite.
        with self.db._get_conn() as conn:
            verified_contact = conn.execute(
                """
                SELECT hr_email
                FROM hr_contacts
                WHERE LOWER(TRIM(hr_email)) = ?
                  AND verified = 1
                LIMIT 1
                """,
                (to_email,),
            ).fetchone()

        if not verified_contact:
            logger.warning(
                "[EmailService] Outreach staging blocked: "
                "recipient is not verified in the database."
            )
            return None

        # 4. Generate the email draft only after verification.
        from services.scoring_service import ScoringService

        pitch = ScoringService().generate_recruiter_email(job)

        if not isinstance(pitch, dict):
            logger.warning(
                "[EmailService] Email draft generation failed for '%s'.",
                company,
            )
            return None

        details = (
            f"Cold email to {to_email} "
            f"({contact.get('source', 'verified contact')}) "
            f"for {title} at {company}."
        )

        preview_payload = {
            "job_hash": job_hash,
            "title": title,
            "company": company,
            "recipient_email": to_email,
            "recipient_name": contact.get("name"),
            "contact_meta": contact,
            "subject": pitch.get("subject"),
            "body_text": pitch.get("body_text"),
            "body_html": pitch.get("body_html"),
            "is_ai_generated": pitch.get("is_ai_generated", False),
        }

        # 5. Add the verified draft to the human approval queue.
        try:
            queue_id = self.db.enqueue_for_approval(
                item_type="cold_email",
                reference_id=job_hash,
                title=f"Cold Email: {company} ({title})",
                company=company,
                details=details,
                preview_payload=preview_payload,
                score=score,
            )

            if job_hash:
                with self.db._get_conn() as conn:
                    conn.execute(
                        "UPDATE applications SET hr_email = ? WHERE job_hash = ?",
                        (to_email, job_hash)
                    )

            logger.info(
                "[EmailService] Verified cold email staged for '%s'. "
                "Queue ID: #%s",
                company,
                queue_id,
            )

            return queue_id

        except Exception:
            logger.exception(
                "[EmailService] Could not stage outreach for '%s'.",
                company,
            )
            return None


    
    def send_approved_outreach(self, queue_id: int) -> Dict[str, Any]:
        """
        Send outreach only for an approved cold_email queue item
        with a verified HR recipient.

        Dry-run mode creates a preview only. It never calls the sender
        or modifies database records.
        """
        import json

        # 1. Load and validate the approval queue item.
        with self.db._get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, status, item_type, preview_payload
                FROM approval_queue
                WHERE id = ?
                """,
                (queue_id,),
            ).fetchone()

            if not row:
                raise ValueError(
                    f"Approval queue item #{queue_id} not found."
                )

            if row["item_type"] != "cold_email":
                raise PermissionError(
                    f"Queue item #{queue_id} is not a cold_email item."
                )

            if row["status"] != "approved":
                raise PermissionError(
                    f"Queue item #{queue_id} has status '{row['status']}', not 'approved'."
                )

            preview = row["preview_payload"]

        # Check daily sending limit
        can_send, reason = self.db.can_send_email()
        if not can_send:
            raise PermissionError(
                f"Daily email sending limit reached: {reason}"
            )

        # 2. Parse the saved email preview.
        try:
            payload = (
                json.loads(preview)
                if isinstance(preview, str)
                else preview
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(
                "The saved email preview contains invalid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise ValueError("The email preview must be a valid object.")

        recipient = payload.get("recipient_email")

        if not isinstance(recipient, str) or not recipient.strip():
            raise ValueError(
                "No recipient email is available. Outreach is blocked."
            )

        recipient = recipient.strip().lower()

        # Basic email syntax check.
        if (
            recipient.count("@") != 1
            or " " in recipient
            or recipient.startswith("@")
            or recipient.endswith("@")
            or "." not in recipient.split("@")[-1]
        ):
            raise ValueError(
                "The recipient email address has invalid syntax."
            )

        # 3. Require an independently verified HR contact in SQLite.
        with self.db._get_conn() as conn:
            verified_contact = conn.execute(
                """
                SELECT hr_email
                FROM hr_contacts
                WHERE LOWER(TRIM(hr_email)) = ?
                  AND verified = 1
                LIMIT 1
                """,
                (recipient,),
            ).fetchone()

        if not verified_contact:
            raise PermissionError(
                "Recipient is not present as a verified HR contact. "
                "Outreach is blocked."
            )

        title = payload.get("title") or "Software Engineer"
        company = payload.get("company") or "Company"
        recipient_name = payload.get("recipient_name")

        # 4. Dry-run: return a preview without sending or writing to SQLite.
        if self.dry_run:
            logger.info(
                "[EmailService] Dry-run preview prepared for queue item #%s. "
                "No email sent and no database records changed.",
                queue_id,
            )

            return {
                "status": "simulated_sent",
                "recipient": recipient,
                "job_title": title,
                "company": company,
                "dry_run": True,
                "submitted": False,
                "database_updated": False,
            }

        # 5. Atomic execution claim
        claim_token = self.db.claim_email_execution(queue_id)
        if not claim_token:
            raise RuntimeError(
                f"Queue item #{queue_id} is already claimed or executed."
            )

        # 6. Apply inter-email pacing delay
        self.apply_inter_email_delay()

        # 7. Live sending
        job_hash = payload.get("job_hash")
        subject = payload.get("subject") or f"Application for {title}"
        body_content = payload.get("body_text") or subject
        try:
            result = self.sender.send_cold_email(
                recipient_email=recipient,
                job_title=title,
                company_name=company,
                recipient_name=recipient_name,
                custom_subject=subject,
                custom_text_body=payload.get("body_text"),
                custom_html_body=payload.get("body_html"),
            )
        except Exception as exc:
            self.db.finalize_email_execution(queue_id, claim_token, "failed")
            with self.db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO email_history
                    (recipient_email, subject, body, email_type, status, error_message)
                    VALUES (?, ?, ?, 'job_outreach', 'failed', ?)
                    """,
                    (recipient, subject, body_content, str(exc)),
                )
            raise

        is_sent = (
            isinstance(result, dict)
            and result.get("status") == "sent"
            and result.get("dry_run") is False
        )

        if is_sent:
            self.db.finalize_email_execution(queue_id, claim_token, "sent")
            with self.db._get_conn() as conn:
                if job_hash:
                    conn.execute(
                        """
                        UPDATE applications
                        SET email_sent = 1,
                            email_sent_date = CURRENT_TIMESTAMP,
                            hr_email = ?
                        WHERE job_hash = ?
                        """,
                        (recipient, job_hash),
                    )

                conn.execute(
                    """
                    INSERT INTO email_history
                    (recipient_email, subject, body, email_type, status)
                    VALUES (?, ?, ?, 'job_outreach', 'sent')
                    """,
                    (recipient, subject, body_content),
                )

            logger.info(
                "[EmailService] Confirmed live email dispatch for queue item #%s.",
                queue_id,
            )
        else:
            self.db.finalize_email_execution(queue_id, claim_token, "failed")
            with self.db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO email_history
                    (recipient_email, subject, body, email_type, status)
                    VALUES (?, ?, ?, 'job_outreach', 'failed')
                    """,
                    (recipient, subject, body_content),
                )

        return result

    def run_inbox_monitor_cycle(self, days_back: int = 3) -> Dict[str, Any]:
        """
        Scan career inbox, classify messages, and update application & contact statuses.
        """
        messages = self.monitor.fetch_inbox_activity(days_back=days_back, db_manager=self.db)
        stats = {
            "total_inspected": len(messages),
            "bounces_handled": 0,
            "interviews_detected": 0,
            "rejections_logged": 0
        }

        with self.db._get_conn() as conn:
            for msg in messages:
                cls = msg.get("classification", {})
                category = cls.get("category")
                sender = msg.get("sender", "")

                if category == CATEGORY_HARD_BOUNCE:
                    stats["bounces_handled"] += 1
                    bounced_email = msg.get("bounced_recipient") or extract_bounced_recipient(msg.get("snippet", ""), sender)
                    logger.warning(f"[EmailService] Handled hard bounce for: {bounced_email or sender}")
                    targets = [e for e in (bounced_email, sender) if e and "@" in e]
                    for target in targets:
                        t = target.strip().lower()
                        conn.execute("UPDATE hr_contacts SET verified = 0 WHERE LOWER(TRIM(hr_email)) = ?", (t,))
                        conn.execute("UPDATE pdf_hr_outreach SET status = 'invalid' WHERE LOWER(TRIM(email)) = ?", (t,))
                        conn.execute("UPDATE csv_drip_contacts SET status = 'bounced' WHERE LOWER(TRIM(email)) = ?", (t,))

                elif category == CATEGORY_POSITIVE_REPLY:
                    stats["interviews_detected"] += 1
                    logger.info(f"🎉 [INTERVIEW DETECTED] Recruiter response from {sender}: '{msg.get('subject')}'")
                    # Update matching application status
                    conn.execute("""
                        UPDATE applications 
                        SET status = 'interview', notes = ? 
                        WHERE hr_email = ?
                    """, (f"Positive reply: {msg.get('snippet')[:100]}", sender))

                elif category == CATEGORY_REJECTION:
                    stats["rejections_logged"] += 1
                    conn.execute("""
                        UPDATE applications 
                        SET status = 'rejected_by_company' 
                        WHERE hr_email = ?
                    """, (sender,))

        return {
            "processed_count": len(messages),
            "stats": stats,
            "messages": messages
        }
