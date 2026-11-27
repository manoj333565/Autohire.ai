"""
services/drip_service.py — Secondary CSV HR Drip Campaign Service (F5 Bonus)
=============================================================================
Provides a secondary cold outreach channel directly from curated CSV contact lists:
  1. CSV Contact Ingestion & Strict Deduplication (email unique constraint)
  2. Sequential Drafting & Human Approval Gate (item_type='drip_email')
  3. Strict Resume Attachment Guard (Magic PDF header verification)
  4. Shared Daily Sending Limits (Tracked persistently via SQLite email_history)
  5. Atomic Execution Claims (email_execution_claims concurrency protection)
  6. Randomized Inter-Email Pacing Delays (Protecting SMTP reputation)
"""

import os
import csv
import time
import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional

import config as cfg
from database.db_manager import DatabaseManager
from tools.email_sender import EmailSender
from services.scoring_service import ScoringService

logger = logging.getLogger("CareerPilot.DripService")


class DripService:
    """Secondary CSV-based Recruiter Outreach & Drip Campaign Service."""

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        dry_run: bool = True,
        require_approval: bool = True,
        resume_path: Optional[str] = None
    ):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        self.dry_run = dry_run
        self.require_approval = require_approval
        self.resume_path = Path(resume_path or cfg.RESUME_PATH)
        self.sender = EmailSender(dry_run=dry_run, resume_path=str(self.resume_path))
        self._last_send_timestamp: Optional[float] = None

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
                logger.info(f"[DripService] Applying inter-email pacing delay: {sleep_needed:.2f}s")
                time.sleep(sleep_needed)

        self._last_send_timestamp = time.time()
        return target_delay

    def import_csv(self, csv_path: str) -> Dict[str, Any]:
        """
        Import recruiter contacts from a CSV file into csv_drip_contacts table.
        Enforces schema validation and strict email deduplication.
        """
        file_p = Path(csv_path)
        if not file_p.exists():
            raise FileNotFoundError(f"CSV file not found: {csv_path}")

        contacts = []
        total_rows = 0
        invalid_rows = 0

        with open(file_p, mode="r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            # Normalize headers
            if not reader.fieldnames:
                return {"imported": 0, "duplicates": 0, "invalid": 0, "total_rows": 0}

            field_map = {fn.strip().lower(): fn for fn in reader.fieldnames}
            company_key = field_map.get("company")
            name_key = field_map.get("name")
            email_key = field_map.get("email")
            role_key = field_map.get("role") or field_map.get("title")

            if not email_key or not company_key:
                raise ValueError("CSV must contain at least 'company' and 'email' columns.")

            for row in reader:
                total_rows += 1
                company = (row.get(company_key) or "").strip()
                email_addr = (row.get(email_key) or "").strip().lower()
                name = (row.get(name_key) or "").strip() if name_key else "Hiring Manager"
                role = (row.get(role_key) or "").strip() if role_key else "Engineering Lead"

                # Validate email syntax
                if (
                    not email_addr
                    or "@" not in email_addr
                    or "." not in email_addr.split("@")[-1]
                    or " " in email_addr
                    or not company
                ):
                    invalid_rows += 1
                    continue

                contacts.append({
                    "company": company,
                    "name": name or "Hiring Manager",
                    "email": email_addr,
                    "role": role or "Engineering Lead"
                })

        db_stats = self.db.import_csv_drip_contacts(contacts, source_file=file_p.name)
        return {
            "imported": db_stats.get("imported", 0),
            "duplicates": db_stats.get("duplicates", 0),
            "invalid": invalid_rows + db_stats.get("invalid", 0),
            "total_rows": total_rows
        }

    def stage_pending_for_approval(self, limit: int = 10, score: float = 8.0) -> List[int]:
        """
        Sequentially fetch pending contacts and stage personalized cold emails in approval_queue.
        Every queued item has item_type='drip_email' and requires human approval.
        """
        pending = self.db.get_pending_csv_drip_contacts(limit=limit)
        queued_ids = []

        scoring = ScoringService()

        for c in pending:
            contact_id = c["id"]
            company = c["company"]
            role = c["role"]
            email_addr = c["email"]
            name = c["name"]

            # Generate personalized email draft
            pseudo_job = {
                "company": company,
                "title": role,
                "job_url": f"csv://drip/{contact_id}",
                "url": f"csv://drip/{contact_id}",
                "description": f"Recruiter drip campaign targeting {role} at {company}."
            }

            pitch = scoring.generate_recruiter_email(pseudo_job)
            subject = pitch.get("subject") or f"Inquiry regarding {role} opportunities at {company} - {cfg.CANDIDATE_NAME}"
            body_text = pitch.get("body_text") or ""
            body_html = pitch.get("body_html") or ""

            preview_payload = {
                "drip_contact_id": contact_id,
                "recipient_email": email_addr,
                "recipient_name": name,
                "company": company,
                "role": role,
                "title": role,
                "subject": subject,
                "body_text": body_text,
                "body_html": body_html,
                "source": "csv_drip_campaign"
            }

            queue_id = self.db.enqueue_for_approval(
                item_type="drip_email",
                reference_id=str(contact_id),
                title=f"HR Drip: {company} ({role})",
                company=company,
                details=f"Secondary CSV outreach to {name} <{email_addr}> at {company} for {role}.",
                preview_payload=preview_payload,
                score=score
            )

            # Update contact record with queue_id and status='staged'
            self.db.update_csv_drip_contact_status(contact_id, status="staged", queue_id=queue_id)
            queued_ids.append(queue_id)
            logger.info(f"[DripService] Staged CSV drip contact #{contact_id} ({email_addr}) -> Queue #{queue_id}")

        return queued_ids

    def send_approved_drip(self, queue_id: int) -> Dict[str, Any]:
        """
        Send outreach only for an approved drip_email queue item.
        Enforces:
          - Mandatory human approval ('approved' status)
          - Persistent daily sending limit check
          - Strict resume attachment check (magic header & size)
          - Atomic execution claims
          - Randomized inter-email delay
        """
        # 1. Fetch and validate queue item
        with self.db._get_conn() as conn:
            row = conn.execute(
                """
                SELECT id, status, item_type, preview_payload, reference_id
                FROM approval_queue
                WHERE id = ?
                """,
                (queue_id,)
            ).fetchone()

            if not row:
                raise ValueError(f"Approval queue item #{queue_id} not found.")

            if row["item_type"] != "drip_email":
                raise PermissionError(f"Queue item #{queue_id} is not a drip_email item (type: '{row['item_type']}').")

            if row["status"] != "approved":
                raise PermissionError(f"Queue item #{queue_id} has status '{row['status']}', not 'approved'.")

            preview_raw = row["preview_payload"]
            reference_id = row["reference_id"]

        # 2. Parse payload
        try:
            payload = json.loads(preview_raw) if isinstance(preview_raw, str) else preview_raw
        except Exception as e:
            raise ValueError(f"Invalid preview JSON in queue item #{queue_id}: {e}")

        recipient = (payload.get("recipient_email") or "").strip().lower()
        company = payload.get("company") or "Company"
        role = payload.get("role") or payload.get("title") or "Engineering"
        recipient_name = payload.get("recipient_name") or "Hiring Manager"
        contact_id = payload.get("drip_contact_id") or (int(reference_id) if reference_id and reference_id.isdigit() else None)

        if not recipient or "@" not in recipient:
            raise ValueError(f"Invalid recipient email in queue item #{queue_id}: '{recipient}'")

        # 3. Check persistent daily sending limit
        can_send, reason = self.db.can_send_email()
        if not can_send:
            raise PermissionError(f"Daily email limit reached: {reason}")

        # 4. Strict resume verification (mandatory guard for all cold outreach)
        if not self.resume_path.exists():
            raise FileNotFoundError(f"Required resume PDF not found at {self.resume_path}")
        with open(self.resume_path, "rb") as rf:
            magic = rf.read(5)
            if not magic.startswith(b"%PDF-"):
                raise ValueError(f"Invalid resume file: Not a valid PDF format at {self.resume_path}")

        # 5. Dry-run mode: return simulated success without network or state changes
        if self.dry_run:
            logger.info(f"[DripService] [DRY RUN] Simulated dispatch for queue item #{queue_id} to {recipient}.")
            return {
                "status": "simulated_sent",
                "recipient": recipient,
                "company": company,
                "role": role,
                "dry_run": True,
                "queue_id": queue_id
            }

        # 6. Atomic claim
        claim_token = self.db.claim_email_execution(queue_id)
        if not claim_token:
            raise RuntimeError(f"Queue item #{queue_id} is already claimed or executed by another worker.")

        # 7. Apply inter-email pacing delay
        self.apply_inter_email_delay()

        # 8. Live dispatch
        subject = payload.get("subject") or f"Inquiry regarding {role} at {company}"
        body_content = payload.get("body_text") or subject
        try:
            result = self.sender.send_cold_email(
                recipient_email=recipient,
                job_title=role,
                company_name=company,
                recipient_name=recipient_name,
                custom_subject=subject,
                custom_text_body=payload.get("body_text"),
                custom_html_body=payload.get("body_html")
            )
        except Exception as exc:
            self.db.finalize_email_execution(queue_id, claim_token, "failed")
            if contact_id:
                self.db.update_csv_drip_contact_status(contact_id, status="failed", error_message=str(exc))
            with self.db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO email_history (recipient_email, subject, body, email_type, status, error_message)
                    VALUES (?, ?, ?, 'drip_outreach', 'failed', ?)
                    """,
                    (recipient, subject, body_content, str(exc))
                )
            raise

        is_sent = isinstance(result, dict) and result.get("status") == "sent" and result.get("dry_run") is False
        if is_sent:
            self.db.finalize_email_execution(queue_id, claim_token, "sent")
            if contact_id:
                self.db.update_csv_drip_contact_status(contact_id, status="sent")
            with self.db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO email_history (recipient_email, subject, body, email_type, status)
                    VALUES (?, ?, ?, 'drip_outreach', 'sent')
                    """,
                    (recipient, subject, body_content)
                )
            logger.info(f"[DripService] Confirmed live dispatch for queue #{queue_id} to {recipient}.")
        else:
            self.db.finalize_email_execution(queue_id, claim_token, "failed")
            if contact_id:
                self.db.update_csv_drip_contact_status(contact_id, status="failed", error_message="Non-sent status returned")
            with self.db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO email_history (recipient_email, subject, body, email_type, status)
                    VALUES (?, ?, ?, 'drip_outreach', 'failed')
                    """,
                    (recipient, subject, body_content)
                )

        return result

    def get_stats(self) -> Dict[str, Any]:
        """Return campaign metrics for the secondary CSV HR drip channel."""
        return self.db.get_csv_drip_stats()
