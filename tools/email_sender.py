"""
tools/email_sender.py — Gmail SMTP SSL Email Dispatcher with MIME Resume Attachment (F5)
========================================================================================
Builds and sends responsive HTML cold outreach emails with:
  -  Personalized email body using the active candidate profile
  - Verified skills showcase (RAG, Agentic AI, LangGraph, FastAPI)
  - Resume PDF MIME attachment (`data/resume.pdf`)
  - CAN-SPAM compliant opt-out footer
  - Safe dry-run mode (validates template and attachment without network dispatch)
  - Automatic fallback between SSL (port 465) and STARTTLS (port 587)
"""
from html import escape
import re
import smtplib
import logging
from pathlib import Path
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from typing import Dict, Any, Optional

import config as cfg

logger = logging.getLogger("CareerPilot.Tools.EmailSender")


class EmailSender:
    """
    Gmail SMTP SSL / STARTTLS Outbound Mail Engine.
    """

    def __init__(
        self,
        gmail_address: Optional[str] = None,
        app_password: Optional[str] = None,
        resume_path: Optional[str] = None,
        dry_run: bool = True
    ):
        self.gmail_address = gmail_address or cfg.GMAIL_ADDRESS
        self.app_password = app_password or cfg.GMAIL_APP_PASSWORD
        self.resume_path = Path(resume_path or cfg.RESUME_PATH)
        self.dry_run = dry_run

    def build_message(
        self,
        recipient_email: str,
        job_title: str,
        company_name: str,
        recipient_name: Optional[str] = None,
        custom_subject: Optional[str] = None,
        custom_text_body: Optional[str] = None,
        custom_html_body: Optional[str] = None
    ) -> MIMEMultipart:
        """
        Assemble a responsive multipart MIME email with HTML body and PDF resume attachment.
        Supports custom LLM-generated subject/bodies with standard fallback.
        """
        

        # Load candidate information from the active profile
        profile = getattr(cfg, "CANDIDATE_PROFILE", {}) or {}
        personal = profile.get("personal", {})

        candidate_name = (personal.get("name") or "").strip()
        candidate_email = (personal.get("email") or "").strip()

        if not candidate_name or not candidate_email:
            raise ValueError(
                "Candidate name or email is missing from the active profile."
            )

        github_url = (personal.get("github_url") or "").strip()
        linkedin_url = (personal.get("linkedin_url") or "").strip()
        portfolio_url = (personal.get("portfolio_url") or "").strip()

        # Build profile links
        profile_links = []

        for label, url in [
            ("GitHub", github_url),
            ("LinkedIn", linkedin_url),
            ("Portfolio", portfolio_url),
        ]:
            if url.startswith(("https://", "http://")):
                profile_links.append(f"{label}: {url}")

        profile_links_text = "\n".join(profile_links)
        profile_links_html = " | ".join(
        f'<a href="{escape(url, quote=True)}">{escape(label)}</a>'
        for label, url in [
        ("GitHub", github_url),
        ("LinkedIn", linkedin_url),
        ("Portfolio", portfolio_url),
    ]
    if url.startswith(("https://", "http://"))
    )

        # Create email message
        msg = MIMEMultipart("mixed")

        if self.gmail_address:
            msg["From"] = f"{candidate_name} <{self.gmail_address}>"

        msg["To"] = recipient_email
        msg["Subject"] = (
            custom_subject
            or f"Application for {job_title} — {candidate_name} (AI/ML Engineer)"
        )

        salutation = (
            f"Hi {recipient_name},"
            if recipient_name
            else "Hi Hiring Team,"
        )

        # 1. Plain-Text Fallback
        text_body = custom_text_body or f"""{salutation}

I am writing to express my strong interest in the {job_title} position at {company_name}.

I am an AI and Software Engineer with experience in Python, Generative AI, RAG pipelines, Agentic AI, and backend development.

Key Technical Highlights:

- Proficient: RAG, Agentic AI, Vector Databases
- Intermediate: LangChain, LangGraph, FastAPI, Hugging Face Transformers
- Learning: Docker, PyTorch and TensorFlow

I have attached my resume for your review.

{profile_links_text}

I would welcome the opportunity to discuss how my skills align with your team's goals.

Best regards,
{candidate_name}
{candidate_email}

---
To opt out of future correspondence, reply with 'Unsubscribe'.
"""

        

        # 2. Modern Styled HTML Body
        html_body = custom_html_body or f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.6; margin: 0; padding: 20px; background-color: #f8fafc; }}
  .container {{ max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; padding: 32px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
  .header {{ border-bottom: 2px solid #1a56db; padding-bottom: 16px; margin-bottom: 24px; }}
  h2 {{ color: #1a56db; margin: 0 0 6px 0; font-size: 20px; }}
  .subtitle {{ color: #64748b; font-size: 14px; margin: 0; }}
  .skills-card {{ background: #eff6ff; border-left: 4px solid #1a56db; padding: 14px 18px; margin: 20px 0; border-radius: 0 6px 6px 0; }}
  .skills-card ul {{ margin: 6px 0 0 0; padding-left: 20px; }}
  .btn {{ display: inline-block; background-color: #1a56db; color: #ffffff !important; padding: 10px 20px; border-radius: 6px; text-decoration: none; font-weight: 600; font-size: 14px; margin-top: 12px; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #e2e8f0; font-size: 12px; color: #94a3b8; text-align: center; }}
</style>
</head>
<body>
<div class="container">
  <div class="header">
    <h2>{salutation}</h2>
    <p class="subtitle">Application for <strong>{job_title}</strong> at <strong>{company_name}</strong></p>
  </div>

  <p>I am writing to express my strong enthusiasm for the <strong>{job_title}</strong> opportunity at <strong>{company_name}</strong>.</p>

  <p>I am a Software & AI Engineer holding a B.Tech in Computer Science, specializing in designing and deploying scalable <strong>Generative AI, RAG pipelines, LangGraph agents, and Python microservices</strong>.</p>

  <div class="skills-card">
    <strong>Technical Proficiency Highlights:</strong>
    <ul>
      <li><strong>Proficient:</strong> RAG Pipelines, Autonomous AI Agents, Vector Databases</li>
      <li><strong>Intermediate:</strong> LangChain, LangGraph, FastAPI, Hugging Face Transformers</li>
      <li><strong>Core:</strong> Python, SQLite / PostgreSQL, Docker, RESTful Architecture</li>
    </ul>
  </div>

 <p>
    My updated resume is attached to this email.
    You can also explore my professional profiles:
    {profile_links_html}
</p>I would appreciate the chance to discuss how my hands-on background can add immediate value to {company_name}.</p>

  <p>Warm regards,<br>
<strong>{escape(candidate_name)}</strong><br>
<span style="color: #64748b; font-size: 13px;">AI/ML Engineer &bull; {escape(candidate_email)}</span></p>

  <div class="footer">
    Sent by CareerPilot AI on behalf of {escape(candidate_name)}.<br>
    To unsubscribe from future direct communications, reply with "Unsubscribe".
  </div>
</div>
</body>
</html>"""

        # Attach alternative plain/html text parts
        msg_alternative = MIMEMultipart("alternative")
        msg_alternative.attach(MIMEText(text_body, "plain", "utf-8"))
        msg_alternative.attach(MIMEText(html_body, "html", "utf-8"))
        msg.attach(msg_alternative)

        # 3. Validate and Attach Resume PDF
        if not self.resume_path or not self.resume_path.exists():
            raise FileNotFoundError(
                f"Required resume PDF not found at: {self.resume_path}. "
                "Email dispatch blocked to avoid sending messages promising absent attachments."
            )

        try:
            with open(self.resume_path, "rb") as f:
                header = f.read(5)
                if header != b"%PDF-":
                    raise ValueError(
                        f"Resume file at {self.resume_path} is not a valid PDF document (magic bytes: {header!r})."
                    )
                f.seek(0)
                pdf_bytes = f.read()

                if len(pdf_bytes) < 100:
                    raise ValueError(
                        f"Resume PDF file at {self.resume_path} is corrupt or empty ({len(pdf_bytes)} bytes)."
                    )

            part = MIMEBase("application", "pdf")
            part.set_payload(pdf_bytes)
            encoders.encode_base64(part)
            clean_filename = f"{re.sub(r'[^a-zA-Z0-9_-]', '_', candidate_name)}_Resume.pdf"
            part.add_header("Content-Disposition", f"attachment; filename=\"{clean_filename}\"")
            msg.attach(part)
            logger.debug(f"[EmailSender] Attached valid resume PDF: {clean_filename} ({len(pdf_bytes)} bytes)")
        except (FileNotFoundError, ValueError):
            raise
        except Exception as e:
            raise ValueError(f"Could not read or attach resume PDF from {self.resume_path}: {e}") from e

        return msg

    def send_cold_email(
        self,
        recipient_email: str,
        job_title: str,
        company_name: str,
        recipient_name: Optional[str] = None,
        custom_subject: Optional[str] = None,
        custom_text_body: Optional[str] = None,
        custom_html_body: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Send or simulate sending a cold outreach email with optional AI-generated pitch.
        """
        msg = self.build_message(
            recipient_email=recipient_email,
            job_title=job_title,
            company_name=company_name,
            recipient_name=recipient_name,
            custom_subject=custom_subject,
            custom_text_body=custom_text_body,
            custom_html_body=custom_html_body
        )

        if self.dry_run:
            logger.info(f"[EmailSender] [DRY RUN] Simulated cold email to {recipient_email} for '{job_title}' at {company_name}.")
            return {
                "status": "simulated_sent",
                "recipient": recipient_email,
                "job_title": job_title,
                "company": company_name,
                "dry_run": True,
                "has_attachment": self.resume_path.exists(),
                "subject": msg["Subject"]
            }

        # Live dispatch
        if not self.gmail_address or not self.app_password:
            raise ValueError("Gmail credentials not configured in environment (GMAIL_ADDRESS, GMAIL_APP_PASSWORD).")

        try:
            logger.info(f"[EmailSender] Attempting Gmail SSL dispatch to {recipient_email} (port 465)...")
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
                logger.info(f"✅ [EMAIL DISPATCHED] Successfully sent to {recipient_email}")
                return {
                    "status": "sent",
                    "recipient": recipient_email,
                    "job_title": job_title,
                    "company": company_name,
                    "dry_run": False
                }
        except Exception as ssl_err:
            logger.warning(f"[EmailSender] SSL 465 failed: {ssl_err}. Falling back to STARTTLS (port 587)...")
            with smtplib.SMTP("smtp.gmail.com", 587, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)
                logger.info(f"✅ [EMAIL DISPATCHED] Successfully sent via STARTTLS to {recipient_email}")
                return {
                    "status": "sent",
                    "recipient": recipient_email,
                    "job_title": job_title,
                    "company": company_name,
                    "dry_run": False
                }

    
    def send_summary_email(
        self,
        to_email: str,
        subject: str,
        html_body: str,
        dry_run: Optional[bool] = None
    ) -> bool:
        """
        Send a daily summary only when live dispatch is explicitly enabled.

        Returns True only after confirmed SMTP dispatch.
        Dry-run and missing credentials always return False.
        """

        is_dry = self.dry_run if dry_run is None else dry_run

        # Never report simulated delivery as successful.
        if is_dry:
            logger.info(
                "[EmailSender] Dry-run active. "
                "Summary email was not sent."
            )
            return False

        # Never use a hardcoded sender address or pretend success.
        if not self.gmail_address or not self.app_password:
            logger.error(
                "[EmailSender] Summary email blocked: "
                "sender credentials are not configured."
            )
            return False

        if not isinstance(to_email, str) or not to_email.strip():
            logger.error(
                "[EmailSender] Summary email blocked: recipient is missing."
            )
            return False

        msg = MIMEMultipart("alternative")
        candidate_name = (
            (cfg.CANDIDATE_PROFILE.get("personal", {}).get("name") or "").strip()
        )

        if not candidate_name:
            logger.error("[EmailSender] Summary email blocked: candidate name missing.")
            return False

        msg["From"] = f"{candidate_name} Agent <{self.gmail_address}>"
        msg["To"] = to_email.strip()
        msg["Subject"] = subject

        part = MIMEText(html_body, "html", "utf-8")
        msg.attach(part)

        # Live dispatch: SSL 465 with STARTTLS 587 fallback.
        try:
            with smtplib.SMTP_SSL(
                "smtp.gmail.com", 465, timeout=15
            ) as server:
                server.login(self.gmail_address, self.app_password)
                server.send_message(msg)

            logger.info(
                "[EmailSender] Summary email successfully dispatched."
            )
            return True

        except Exception as ssl_err:
            logger.warning(
                "[EmailSender] SSL dispatch failed: %s. "
                "Trying STARTTLS.",
                ssl_err,
            )

            try:
                with smtplib.SMTP(
                    "smtp.gmail.com", 587, timeout=15
                ) as server:
                    server.ehlo()
                    server.starttls()
                    server.ehlo()
                    server.login(self.gmail_address, self.app_password)
                    server.send_message(msg)

                logger.info(
                    "[EmailSender] Summary email dispatched via STARTTLS."
                )
                return True

            except Exception:
                logger.exception(
                    "[EmailSender] Summary email dispatch failed."
                )
                return False



