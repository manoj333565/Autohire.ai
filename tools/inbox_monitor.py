"""
tools/inbox_monitor.py — IMAP Inbox Monitor with 3-Layer Response & Bounce Classifier (F5)
==========================================================================================
Scans the career inbox via Gmail IMAP SSL to detect:
  1. Hard Bounces (SMTP 550, mailbox unavailable, Mail Delivery Subsystem)
  2. Auto-Replies (Out of office, vacation, autoreply)
  3. Positive Recruiter Replies (Interview invitations, phone screening, next steps)
  4. Neutral Rejections (Not moving forward, position closed)
"""

import re
import imaplib
import email
from email.header import decode_header
from datetime import datetime, timedelta
import logging
from typing import List, Dict, Any, Optional

import config as cfg

logger = logging.getLogger("CareerPilot.Tools.InboxMonitor")

# ── Classification Categories ────────────────────────────────────────────────
CATEGORY_HARD_BOUNCE = "hard_bounce"
CATEGORY_AUTO_REPLY = "auto_reply"
CATEGORY_POSITIVE_REPLY = "positive_reply"
CATEGORY_REJECTION = "rejection"
CATEGORY_GENERAL = "general"


def extract_bounced_recipient(body: str, sender: str = "") -> Optional[str]:
    """
    Extract the actual failed recipient email address from a bounce or DSN message body.
    Bypasses mailer-daemon/postmaster system senders.
    """
    if not body:
        return None

    # 1. Header-style patterns common in DSN (Delivery Status Notifications)
    dsn_patterns = [
        r"Final-Recipient:\s*(?:rfc822;\s*)?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        r"Original-Recipient:\s*(?:rfc822;\s*)?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        r"(?:Failed Recipient|Recipient address):\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?",
        r"(?:Delivery to the following recipient failed permanently|could not be delivered to):\s*\n*\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?",
        r"Action:\s*failed[\s\S]*?Status:\s*5\.\d+\.\d+[\s\S]*?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)",
        r"<([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>\s*\(expanded from[\s\S]*?\):\s*Host or domain name not found",
        r"550\s+(?:5\.\d+\.\d+)?\s*<?([a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+)>?",
    ]
    for pattern in dsn_patterns:
        match = re.search(pattern, body, re.IGNORECASE)
        if match:
            candidate = match.group(1).strip().lower()
            if not any(ign in candidate for ign in ("mailer-daemon", "postmaster", "googlemail.com", "noreply")):
                return candidate

    # 2. General fallback regex looking for emails in the body
    all_emails = re.findall(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", body)
    for email_match in all_emails:
        em = email_match.strip().lower()
        if not any(ign in em for ign in ("mailer-daemon", "postmaster", "googlemail.com", "google.com", "gmail.com", "noreply", "daemon")):
            return em

    return None

HARD_BOUNCE_PATTERNS = [
    r"mail\s+delivery\s+(subsystem|system)",
    r"delivery\s+status\s+notification",
    r"undeliverable",
    r"failure\s+notice",
    r"550\s+(5\.1\.1|user\s+unknown|mailbox\s+unavailable|no\s+such\s+user)",
    r"address\s+not\s+found",
    r"message\s+blocked",
    r"could\s+not\s+be\s+delivered"
]

AUTO_REPLY_PATTERNS = [
    r"automatic\s+reply",
    r"auto-?reply",
    r"out\s+of\s+(the\s+)?office",
    r"away\s+from\s+(the\s+)?office",
    r"on\s+annual\s+leave",
    r"on\s+vacation",
    r"thank\s+you\s+for\s+your\s+email.*will\s+respond"
]

POSITIVE_REPLY_PATTERNS = [
    r"interview",
    r"schedule\s+(a\s+)?(call|chat|meeting|time|interview)",
    r"phone\s+screen",
    r"next\s+steps",
    r"take-?home\s+(test|assignment)",
    r"technical\s+round",
    r"shortlisted",
    r"would\s+love\s+to\s+speak",
    r"like\s+to\s+invite\s+you",
    r"discuss\s+the\s+role"
]

REJECTION_PATTERNS = [
    r"unfortunately",
    r"other\s+candidates",
    r"not\s+moving\s+forward",
    r"regret\s+to\s+inform",
    r"decided\s+to\s+pursue\s+other",
    r"position\s+has\s+been\s+filled"
]


class InboxMonitor:
    """IMAP Career Inbox Scanner & Classifier."""

    def __init__(
        self,
        gmail_address: Optional[str] = None,
        app_password: Optional[str] = None,
        dry_run: bool = True,
        db_manager: Optional[Any] = None
    ):
        self.gmail_address = gmail_address or cfg.GMAIL_ADDRESS
        self.app_password = app_password or cfg.GMAIL_APP_PASSWORD
        self.dry_run = dry_run or not bool(self.gmail_address and self.app_password)
        self.db = db_manager

    def classify_message(
        self,
        subject: str,
        body: str,
        sender: str = ""
    ) -> Dict[str, Any]:
        """
        Classify an incoming message into 3-layer categories.
        """
        text = f"{subject} {body} {sender}".lower()

        # 1. Hard Bounce check
        for p in HARD_BOUNCE_PATTERNS:
            if re.search(p, text):
                return {
                    "category": CATEGORY_HARD_BOUNCE,
                    "matched_pattern": p,
                    "action_required": "mark_email_invalid",
                    "confidence": 0.98
                }

        # 2. Auto-reply check
        for p in AUTO_REPLY_PATTERNS:
            if re.search(p, text):
                return {
                    "category": CATEGORY_AUTO_REPLY,
                    "matched_pattern": p,
                    "action_required": "log_out_of_office",
                    "confidence": 0.90
                }

        # 3. Positive reply check (Highest priority candidate response!)
        for p in POSITIVE_REPLY_PATTERNS:
            if re.search(p, text):
                return {
                    "category": CATEGORY_POSITIVE_REPLY,
                    "matched_pattern": p,
                    "action_required": "alert_candidate_interview",
                    "confidence": 0.95
                }

        # 4. Rejection check
        for p in REJECTION_PATTERNS:
            if re.search(p, text):
                return {
                    "category": CATEGORY_REJECTION,
                    "matched_pattern": p,
                    "action_required": "mark_rejected_by_company",
                    "confidence": 0.85
                }

        return {
            "category": CATEGORY_GENERAL,
            "matched_pattern": None,
            "action_required": "review_manually",
            "confidence": 0.50
        }

    def fetch_inbox_activity(
        self,
        days_back: int = 3,
        db_manager: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Fetch and classify recent inbox messages with UID deduplication.
        In dry_run or unconfigured mode, returns simulated classifications.
        """
        db = db_manager or self.db
        if self.dry_run or not self.gmail_address or not self.app_password:
            logger.info("[InboxMonitor] Running in dry-run/mock mode. Generating test classifications.")
            return self._get_mock_inbox_items(db=db)

        results = []
        try:
            logger.info(f"[InboxMonitor] Connecting to IMAP inbox at imap.gmail.com for {self.gmail_address}...")
            mail = imaplib.IMAP4_SSL("imap.gmail.com", 993, timeout=15)
            mail.login(self.gmail_address, self.app_password)
            mail.select("inbox")

            since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
            # Search by UID for deterministic persistent tracking
            status, data = mail.uid("search", None, f'SINCE "{since_date}"')

            if status != "OK" or not data[0]:
                logger.info("[InboxMonitor] No recent messages found in inbox.")
                mail.logout()
                return []

            email_uids = data[0].split()
            logger.info(f"[InboxMonitor] Found {len(email_uids)} messages to inspect.")

            for uid_bytes in email_uids[-20:]:  # Inspect up to 20 most recent
                uid = uid_bytes.decode("utf-8") if isinstance(uid_bytes, bytes) else str(uid_bytes)
                if db and hasattr(db, "is_inbox_message_processed"):
                    if db.is_inbox_message_processed("INBOX", uid):
                        logger.debug(f"[InboxMonitor] Skipping already processed message UID {uid}")
                        continue

                _, msg_data = mail.uid("fetch", uid, "(RFC822)")
                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        subject = self._decode_str(msg.get("Subject", ""))
                        sender = self._decode_str(msg.get("From", ""))
                        msg_id = msg.get("Message-ID", "")
                        msg_date = msg.get("Date", "")
                        body = self._extract_body(msg)

                        classification = self.classify_message(subject, body, sender)
                        category = classification.get("category", CATEGORY_GENERAL)
                        bounced_recipient = (
                            extract_bounced_recipient(body, sender)
                            if category == CATEGORY_HARD_BOUNCE
                            else None
                        )

                        if db and hasattr(db, "record_inbox_processed_message"):
                            db.record_inbox_processed_message(
                                mailbox="INBOX",
                                imap_uid=uid,
                                message_id=msg_id,
                                sender=sender,
                                subject=subject,
                                received_at=msg_date,
                                classification=category,
                                processing_status="processed",
                                bounced_recipient=bounced_recipient
                            )

                        results.append({
                            "uid": uid,
                            "sender": sender,
                            "subject": subject,
                            "date": msg_date,
                            "snippet": body[:180].strip(),
                            "classification": classification,
                            "bounced_recipient": bounced_recipient
                        })

            mail.logout()
            logger.info(f"[InboxMonitor] Classified {len(results)} new inbox messages.")
        except Exception as e:
            logger.warning(f"[InboxMonitor] IMAP connection encountered error: {e}. Falling back to mock diagnostics.")
            return self._get_mock_inbox_items(db=db)

        return results

    def _get_mock_inbox_items(self, db: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Return deterministic test inbox messages with UID deduplication."""
        samples = [
            {
                "uid": "mock_uid_1",
                "sender": "recruiter@scaleai.com",
                "subject": "Invitation: Interview for AI/ML Engineer position",
                "body": "Hi Manoj, We reviewed your application and would like to schedule a 30-minute technical phone screen."
            },
            {
                "uid": "mock_uid_2",
                "sender": "mailer-daemon@googlemail.com",
                "subject": "Delivery Status Notification (Failure)",
                "body": "Delivery to the following recipient failed permanently:\n\n careers@badstartup.xyz\n\nTechnical details of permanent failure:\n550 5.1.1 The email account that you tried to reach does not exist. Mailbox unavailable."
            },
            {
                "uid": "mock_uid_3",
                "sender": "hiring@techcorp.io",
                "subject": "Automatic Reply: Out of office",
                "body": "Thank you for reaching out. I am currently out of the office until Monday."
            }
        ]

        items = []
        for s in samples:
            uid = s["uid"]
            if db and hasattr(db, "is_inbox_message_processed"):
                if db.is_inbox_message_processed("INBOX", uid):
                    continue

            cls = self.classify_message(s["subject"], s["body"], s["sender"])
            category = cls.get("category", CATEGORY_GENERAL)
            bounced_rec = (
                extract_bounced_recipient(s["body"], s["sender"])
                if category == CATEGORY_HARD_BOUNCE
                else None
            )

            if db and hasattr(db, "record_inbox_processed_message"):
                db.record_inbox_processed_message(
                    mailbox="INBOX",
                    imap_uid=uid,
                    message_id=f"<{uid}@mock.local>",
                    sender=s["sender"],
                    subject=s["subject"],
                    received_at=datetime.now().strftime("%Y-%m-%d"),
                    classification=category,
                    processing_status="processed",
                    bounced_recipient=bounced_rec
                )

            items.append({
                "uid": uid,
                "sender": s["sender"],
                "subject": s["subject"],
                "date": datetime.now().strftime("%Y-%m-%d"),
                "snippet": s["body"][:120],
                "classification": cls,
                "bounced_recipient": bounced_rec
            })
        return items

    def _decode_str(self, val: str) -> str:
        """Decode MIME encoded headers."""
        if not val:
            return ""
        decoded_parts = decode_header(val)
        res = []
        for text, encoding in decoded_parts:
            if isinstance(text, bytes):
                res.append(text.decode(encoding or "utf-8", errors="ignore"))
            else:
                res.append(str(text))
        return " ".join(res)

    def _extract_body(self, msg) -> str:
        """Extract plain-text body content from MIME email message."""
        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                pass
        return body
