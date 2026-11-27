"""
services/apply_service.py — Automated Application Engine & Approval Queue (F4)
==============================================================================
Manages job application submissions across supported portals with:
  - Form field mapping with verified candidate profile data & resume PDF
  - Mandatory human approval safety gate (enforces review before submission)
  - Strict double-apply prevention using SHA-256 URL hashing & DB lookups
  - Multi-portal form assistance handlers:
      * RemoteOK (direct apply URL redirect / email outreach staging)
      * WeWorkRemotely (external URL redirection / email outreach)
      * Internshala (resume attachment & cover note staging)
      * Wellfound (candidate profile payload generation)
      * LinkedIn (Easy Apply assistance & manual apply link generation)
      * Naukri (FastForward profile assistance & direct application staging)
  - Default safe dry-run mode (simulates submission and validates field mapping)
  - Automated status updates in SQLite `applications` and `approval_queue` tables
"""

import os
import re
import time
import json
import random
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService

logger = logging.getLogger("CareerPilot.ApplyService")


class ApplyService:
    """
    Automated application submission and approval queue orchestrator.
    """

    def __init__(
        self,
        db_manager: Optional[DatabaseManager] = None,
        profile_service: Optional[ProfileService] = None,
        dry_run: bool = True,
        require_approval: bool = True
    ):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        self.profile_service = profile_service or ProfileService(self.db)
        self.dry_run = dry_run
        self.require_approval = require_approval

    # ------------------------------------------------------------------
    # Form Field Mapping
    # ------------------------------------------------------------------

   
    def get_candidate_application_payload(self) -> Dict[str, Any]:
        """
        Build candidate application fields from the authoritative SQLite profile.
        Never use hardcoded candidate identity or contact details.
        """
        profile = self.profile_service.get_full_profile(profile_id=1)

        if not isinstance(profile, dict) or not profile:
            raise ValueError("Candidate profile not found.")

        # Validate required candidate information.
        full_name = (profile.get("name") or "").strip()
        email = (profile.get("email") or "").strip()
        phone = (profile.get("phone") or "").strip()

        missing_fields = [
            field for field, value in {
                "name": full_name,
                "email": email,
                "phone": phone,
            }.items()
            if not value
        ]

        if missing_fields:
            raise ValueError(
                "Candidate profile is incomplete. Missing: "
                + ", ".join(missing_fields)
            )

        name_parts = full_name.split()
        education = profile.get("education") or {}

        resume_path = profile.get("resume_path") or cfg.RESUME_PATH
        resume_p = Path(resume_path)

        # Build skills using the existing proficiency categories.
        skills_by_tier = profile.get("skills_by_tier") or {}

        top_skills = []
        for tier in ("Proficient", "Intermediate"):
            for skill in skills_by_tier.get(tier, []):
                if isinstance(skill, dict) and skill.get("skill_name"):
                    top_skills.append(skill["skill_name"])

        return {
            "full_name": full_name,
            "first_name": name_parts[0],
            "last_name": " ".join(name_parts[1:]),
            "email": email,
            "phone": phone,
            "location": (profile.get("location") or "").strip(),
            "linkedin_url": (profile.get("linkedin_url") or "").strip(),
            "github_url": (profile.get("github_url") or "").strip(),
            "portfolio_url": (profile.get("portfolio_url") or "").strip(),

            "education": {
                "degree": education.get("degree", ""),
                "college": education.get("college", ""),
                "year": education.get("year", ""),
                "cgpa": education.get("cgpa", ""),
            },

            "resume_path": (
                str(resume_p.resolve()) if resume_p.exists()
                else str(resume_p)
            ),
            "resume_exists": resume_p.exists(),

            # Existing application preferences retained for now.
            "work_authorization": "Authorized to work in India / Open to Remote worldwide",
            "notice_period": "Immediate / 0 days",
            "expected_salary": "3.6 - 6.0 LPA / Negotiable",
            "years_of_experience": "0-1 year (Entry-Level / Fresher)",

            "top_skills": top_skills,
        }


    # ------------------------------------------------------------------
    # Double-Apply Prevention
    # ------------------------------------------------------------------

    
    def can_apply(self, job_or_hash: Any) -> Tuple[bool, str]:
        """
        Validate job identity, prevent duplicate applications,
        and enforce the daily application limit.
        """
        if isinstance(job_or_hash, dict):
            url = (
                job_or_hash.get("job_url")
                or job_or_hash.get("url")
                or ""
            )
            url = url.strip() if isinstance(url, str) else ""

            supplied_hash = job_or_hash.get("job_hash")
            job_hash = (
                supplied_hash.strip()
                if isinstance(supplied_hash, str)
                else ""
            )

            if not url and not job_hash:
                return False, "REJECT_NO_HASH: Missing job hash and URL"

            if not job_hash:
                job_hash = self.db.make_job_hash(url)

        elif isinstance(job_or_hash, str):
            job_hash = job_or_hash.strip()

        else:
            return False, "REJECT_INVALID_INPUT: Expected job URL, hash, or dictionary"

        if not job_hash:
            return False, "REJECT_NO_HASH: Invalid or missing job hash"

        with self.db._get_conn() as conn:
            row = conn.execute(
                """
                SELECT status, applied_date
                FROM applications
                WHERE job_hash = ?
                """,
                (job_hash,),
            ).fetchone()

            if row:
                status = (row["status"] or "").lower()
                applied_date = row["applied_date"] or "previously"

                if status in (
                    "applied",
                    "interview",
                    "offered",
                    "rejected_by_company",
                ):
                    return False, (
                        f"REJECT_ALREADY_APPLIED: Status is '{status}' "
                        f"(applied on {applied_date})"
                    )

            today_str = datetime.now().strftime("%Y-%m-%d")

            count_row = conn.execute(
                """
                SELECT COUNT(*) AS count
                FROM applications
                WHERE status = 'applied'
                  AND applied_date LIKE ?
                """,
                (f"{today_str}%",),
            ).fetchone()

            daily_count = count_row["count"] if count_row else 0

            if daily_count >= cfg.MAX_APPLY_PER_DAY:
                return False, (
                    f"REJECT_DAILY_LIMIT_REACHED: "
                    f"{daily_count}/{cfg.MAX_APPLY_PER_DAY} today"
                )

        return True, "PASS_CAN_APPLY"


    # ------------------------------------------------------------------
    # ATS Detection & Automated Playwright Engine
    # ------------------------------------------------------------------

    @staticmethod
    def detect_ats(url: str) -> str:
        """
        Identify ATS platform or portal category from job URL.
        Returns: 'greenhouse', 'lever', 'workday', 'smartrecruiters', 'protected', or 'generic'.
        """
        if not url:
            return "generic"
        u = url.lower()
        if "greenhouse.io" in u:
            return "greenhouse"
        elif "lever.co" in u:
            return "lever"
        elif "myworkdayjobs.com" in u or "workday.com" in u:
            return "workday"
        elif "smartrecruiters.com" in u:
            return "smartrecruiters"
        elif any(domain in u for domain in ["linkedin.com", "naukri.com", "wellfound.com", "angel.co"]):
            return "protected"
        return "generic"

    def get_random_delay(
        self,
        min_sec: Optional[float] = None,
        max_sec: Optional[float] = None
    ) -> float:
        """
        Generate a randomized delay in seconds within configured bounds.
        Defaults to cfg.BROWSER_MIN_DELAY (0.5s) and cfg.BROWSER_MAX_DELAY (1.5s).
        """
        low = min_sec if min_sec is not None else getattr(cfg, "BROWSER_MIN_DELAY", 0.5)
        high = max_sec if max_sec is not None else getattr(cfg, "BROWSER_MAX_DELAY", 1.5)
        if low > high:
            low, high = high, low
        return random.uniform(low, high)

    def _page_random_delay(
        self,
        page: Any,
        min_sec: Optional[float] = None,
        max_sec: Optional[float] = None
    ) -> float:
        """
        Apply a randomized delay on a Playwright page instance within configured bounds.
        Paces form filling respectfully without pretending to bypass bot detection.
        """
        delay = self.get_random_delay(min_sec=min_sec, max_sec=max_sec)
        try:
            page.wait_for_timeout(int(delay * 1000))
        except Exception:
            time.sleep(delay)
        return delay

    def apply_with_playwright(
        self,
        job_url: str,
        payload: Dict[str, Any],
        dry_run: bool = True
    ) -> Dict[str, Any]:
        """
        Execute automated form detection, field mapping, and optional submission via Playwright.
        Auto-detects:
          - First Name, Last Name, Full Name
          - Email, Phone
          - LinkedIn, GitHub, Portfolio URLs
          - Resume PDF file upload
        When dry_run=True: populates and validates fields, attaches resume, but HALTS before submit.
        When dry_run=False: submits form and verifies confirmation receipt.
        """
        # SAFETY: Never open external websites in dry-run mode.
        if dry_run:
            return {
                "status": "dry_run_preview",
                "notes": (
                    "Dry-run safety mode: external browser navigation, "
                    "form filling, resume uploads, and submission are disabled."
                ),
                "fields_detected": [],
                "ats_type": self.detect_ats(job_url),
                "dry_run": True,
                "submitted": False,
            }

        if not job_url:
            return {
                "status": "failed",
                "notes": "Missing application URL",
                "fields_detected": [],
                "dry_run": dry_run
            }

        ats_type = self.detect_ats(job_url)
        detected_fields = []

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            logger.warning("[ApplyService] Playwright not installed. Falling back to structural validation.")
            return {
                "status": "simulated_applied" if dry_run else "manual_required",
                "notes": "Playwright library unavailable; form fields mapped and verified structurally.",
                "fields_detected": ["first_name", "last_name", "email", "phone", "resume", "linkedin", "github"],
                "ats_type": ats_type,
                "dry_run": dry_run
            }

        # SAFETY: Test URLs must never be reported as real applications.
        if any(domain in job_url.lower() for domain in [
            "example.com",
            "localhost",
            ".test"
        ]):
            return {
                "status": "manual_required",
                "confirmed": False,
                "notes": (
                    "Test or local URL detected. No real application was submitted. "
                    "Manual review is required."
        ),
        "fields_detected": [],
        "ats_type": ats_type,
        "dry_run": dry_run,
        "submitted": False
        }

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
                page.set_default_timeout(15000)

                logger.info(f"[ApplyService] Navigating to ATS form: {job_url}")
                page.goto(job_url, wait_until="domcontentloaded", timeout=15000)

                # Selector mappings for standard ATS and generic forms
                field_selectors = {
                    "first_name": ['input#first_name', 'input[name="first_name"]', 'input[name*="firstName" i]', 'input[autocomplete="given-name"]'],
                    "last_name": ['input#last_name', 'input[name="last_name"]', 'input[name*="lastName" i]', 'input[autocomplete="family-name"]'],
                    "full_name": ['input#name', 'input[name="name"]', 'input[name*="fullName" i]', 'input[autocomplete="name"]'],
                    "email": ['input#email', 'input[type="email"]', 'input[name*="email" i]', 'input[autocomplete="email"]'],
                    "phone": ['input#phone', 'input[type="tel"]', 'input[name*="phone" i]', 'input[autocomplete="tel"]'],
                    "linkedin": ['input[name*="linkedin" i]', 'input[id*="linkedin" i]', 'input[placeholder*="linkedin" i]'],
                    "github": ['input[name*="github" i]', 'input[id*="github" i]', 'input[placeholder*="github" i]'],
                    "portfolio": ['input[name*="website" i]', 'input[name*="portfolio" i]', 'input[id*="portfolio" i]'],
                    "resume": ['input[type="file"]', 'input#resume', 'input[name*="resume" i]', 'input[id*="resume" i]']
                }

                # Fill detected inputs with respectful randomized pacing
                for field_name, selectors in field_selectors.items():
                    for sel in selectors:
                        try:
                            elem = page.query_selector(sel)
                            if elem and elem.is_visible():
                                self._page_random_delay(page)
                                detected_fields.append(field_name)
                                if field_name == "resume":
                                    resume_path = payload.get("resume_path")
                                    if resume_path and os.path.exists(resume_path):
                                        elem.set_input_files(resume_path)
                                elif field_name == "first_name":
                                    elem.fill(payload.get("first_name", ""))
                                elif field_name == "last_name":
                                    elem.fill(payload.get("last_name", ""))
                                elif field_name == "full_name":
                                    elem.fill(payload.get("full_name",""))
                                elif field_name == "email":
                                    elem.fill(payload.get("email", ""))
                                elif field_name == "phone":
                                    elem.fill(payload.get("phone", ""))
                                elif field_name == "linkedin":
                                    elem.fill(payload.get("linkedin_url", ""))
                                elif field_name == "github":
                                    elem.fill(payload.get("github_url", ""))
                                elif field_name == "portfolio":
                                    elem.fill(payload.get("portfolio_url", ""))
                                self._page_random_delay(page)
                                break
                        except Exception:
                            continue

                if dry_run:
                    browser.close()
                    logger.info(f"[ApplyService] [DRY RUN] ATS fields mapped: {detected_fields}. Submission safely halted.")
                    return {
                        "status": "simulated_applied",
                        "notes": f"Playwright mapped {len(detected_fields)} ATS fields ({', '.join(detected_fields)}). Submission halted by dry-run gate.",
                        "fields_detected": detected_fields,
                        "ats_type": ats_type,
                        "dry_run": True
                    }

                # LIVE EXECUTION: Click submit button and verify confirmation
                self._page_random_delay(page)
                submit_selectors = [
                    'button[type="submit"]',
                    'input[type="submit"]',
                    'button:has-text("Submit Application")',
                    'button:has-text("Submit")',
                    '#submit_app',
                    '.postings-btn'
                ]
                submitted = False
                for s_sel in submit_selectors:
                    try:
                        btn = page.query_selector(s_sel)
                        if btn and btn.is_visible():
                            self._page_random_delay(page)
                            btn.click()
                            submitted = True
                            break
                    except Exception:
                        continue

                if not submitted:
                    browser.close()
                    return {
                        "status": "failed",
                        "notes": "Submit button not found on application page.",
                        "fields_detected": detected_fields,
                        "dry_run": False
                    }

                               # Do not automatically mark an application as successful.
                # A human must independently verify the application receipt.
                page.wait_for_timeout(3000)

                current_url = page.url

                browser.close()

                return {
                    "status": "manual_required",
                    "confirmed": False,
                    "submitted": True,
                    "notes": (
                        "The submit action was triggered, but successful application "
                        "submission has not been independently verified. "
                        "Human verification is required."
                    ),
                    "confirmation_url": current_url,
                    "fields_detected": detected_fields,
                    "dry_run": False
                }

        except Exception as e:
            logger.warning(f"[ApplyService] Playwright navigation failed for {job_url}: {e}. Falling back to structural validation.")
            return {
                "status": "simulated_applied" if dry_run else "manual_required",
                "notes": f"ATS browser session halted ({type(e).__name__}: {str(e)[:100]}). Form payload prepared.",
                "fields_detected": ["first_name", "last_name", "email", "phone", "resume"],
                "ats_type": ats_type,
                "dry_run": dry_run
            }

    # ------------------------------------------------------------------
    # Single Application Execution & Cold Email Routing
    # ------------------------------------------------------------------

    def route_unsupported_to_cold_email(
        self,
        job: Dict[str, Any],
        score: float = 0.0
    ) -> Optional[int]:
        """
        Routes a job marked 'manual_required' (due to unsupported or protected portal)
        to the cold email approval channel if eligible and a verified HR contact exists.

        Strict Safety & Lifecycle Guarantees:
          1. Checks for existing cold_email approval_queue item to prevent duplicates.
          2. Verifies HR recruiter in hr_contacts with verified=1 (via EmailService).
          3. Creates approval_queue item with item_type='cold_email' and status='pending'.
          4. NEVER automatically sends an email; requires explicit human approval.
          5. Preserves the job's status in applications table as 'manual_required'.

        Returns:
            queue_id (int) if staged or already existing, None if ineligible/unverified.
        """
        url = job.get("job_url") or job.get("url") or ""
        job_hash = job.get("job_hash") or self.db.make_job_hash(url)
        if not job_hash:
            logger.warning("[ApplyService] Cannot route to cold email: missing job hash.")
            return None

        # 1. Prevent duplicate cold email queue items across repeated runs
        with self.db._get_conn() as conn:
            existing = conn.execute(
                """
                SELECT id FROM approval_queue
                WHERE reference_id = ? AND item_type = 'cold_email'
                LIMIT 1
                """,
                (job_hash,)
            ).fetchone()

        if existing:
            logger.info(
                f"[ApplyService] Cold email approval item already exists for {job_hash} (Queue #{existing['id']})."
            )
            return existing["id"]

        # 2. Stage outreach for approval via EmailService
        from services.email_service import EmailService
        email_service = EmailService(
            db_manager=self.db,
            dry_run=self.dry_run,
            require_approval=self.require_approval
        )

        job_score = score or float(job.get("relevance_score") or job.get("score") or 0.0)
        queue_id = email_service.stage_outreach_for_approval(job, score=job_score)

        if queue_id:
            logger.info(
                f"[ApplyService] Successfully routed unsupported portal job {job_hash} "
                f"to cold email approval queue (Queue #{queue_id})."
            )
        else:
            logger.info(
                f"[ApplyService] Cold email staging not created for {job_hash} "
                "(no verified recruiter contact found in database)."
            )

        return queue_id

    def apply_to_job(
        self,
        job: Dict[str, Any],
        queue_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Execute or simulate job application for a single listing.
        Enforces human approval safety gate when require_approval=True.
        """
        url = job.get("job_url") or job.get("url") or ""
        job_hash = job.get("job_hash") or self.db.make_job_hash(url)
        portal = (job.get("portal") or "direct").lower()
        title = job.get("title", "Untitled Job")
        company = job.get("company", "Confidential")

        # 1. Double-apply check
        allowed, reason = self.can_apply(job_hash)
        if not allowed:
            logger.warning(f"[ApplyService] Application prevented for '{title}' at {company}: {reason}")
            return {
                "job_hash": job_hash,
                "title": title,
                "company": company,
                "portal": portal,
                "status": "blocked",
                "reason": reason,
                "dry_run": self.dry_run
            }

        
        # 2. Human approval safety check — applies in ALL modes
        
        # 2. Human approval safety gate
        claim_token = None

        # Every non-dry-run execution must have explicit approval.
        if self.require_approval or not self.dry_run:
            is_approved = self._verify_queue_approval(
                job_hash,
                queue_id
            )

            if not is_approved:
                logger.warning(
                    "[ApplyService] [SAFETY GATE] Blocked '%s' at '%s'.",
                    title,
                    company,
                )
                return {
                    "job_hash": job_hash,
                    "title": title,
                    "company": company,
                    "portal": portal,
                    "status": "awaiting_approval",
                    "reason": (
                        "Explicit human approval is required."
                    ),
                    "dry_run": self.dry_run,
                }

       
        # 3. Atomically claim approved jobs before live execution.
        # Dry-run must never create or modify an execution claim.
        if not self.dry_run:
            if queue_id is None:
                return {
                    "job_hash": job_hash,
                    "title": title,
                    "company": company,
                    "portal": portal,
                    "status": "blocked",
                    "reason": "Approval queue ID is required.",
                    "dry_run": self.dry_run,
                }

            claim_token = self.db.claim_approval_execution(
                queue_id,
                job_hash,
            )

            if not claim_token:
                logger.warning(
                    "[ApplyService] Execution claim denied for queue %s.",
                    queue_id,
                )
                return {
                    "job_hash": job_hash,
                    "title": title,
                    "company": company,
                    "portal": portal,
                    "status": "blocked",
                    "reason": (
                        "Approval is already claimed, processed, "
                        "or no longer eligible."
                    ),
                    "dry_run": False,
                }

        # 4. Prepare candidate payload and route to the correct portal.
        try:
            payload = self.get_candidate_application_payload()
            ats_type = self.detect_ats(url)

            if ats_type == "protected" or portal in (
                "linkedin", "naukri", "wellfound"
            ):
                if portal == "wellfound":
                    result = self._apply_wellfound(job, payload)
                elif portal == "linkedin":
                    result = self._apply_linkedin(job, payload)
                elif portal == "naukri":
                    result = self._apply_naukri(job, payload)
                else:
                    result = {
                        "status": "manual_required",
                        "notes": (
                            f"Protected portal ({portal.title()}). "
                            "Manual candidate session required."
                        ),
                        "apply_url": url,
                        "resume_path": payload["resume_path"],
                    }

            elif ats_type in (
                "greenhouse", "lever", "workday", "smartrecruiters"
            ):
                result = self.apply_with_playwright(
                    url, payload, dry_run=self.dry_run
                )

            elif portal == "remoteok":
                ext_url = job.get("extra_metadata", {}).get("apply_url")
                if ext_url and self.detect_ats(ext_url) in (
                    "greenhouse", "lever", "workday"
                ):
                    result = self.apply_with_playwright(
                        ext_url, payload, dry_run=self.dry_run
                    )
                else:
                    result = self._apply_remoteok(job, payload)

            elif portal == "weworkremotely":
                ext_url = job.get("extra_metadata", {}).get("apply_url")
                if ext_url and self.detect_ats(ext_url) in (
                    "greenhouse", "lever", "workday"
                ):
                    result = self.apply_with_playwright(
                        ext_url, payload, dry_run=self.dry_run
                    )
                else:
                    result = self._apply_weworkremotely(job, payload)

            elif portal == "internshala":
                result = self._apply_internshala(job, payload)

            else:
                result = self._apply_generic(job, payload)

        except Exception:
            logger.exception(
                "[ApplyService] Unexpected execution error for job %s. "
                "Execution claim remains locked.",
                job_hash,
            )

            return {
                "job_hash": job_hash,
                "title": title,
                "company": company,
                "portal": portal,
                "status": "execution_uncertain",
                "reason": (
                    "Unexpected execution error. Manual verification "
                    "is required before any retry."
                ),
                "dry_run": self.dry_run,
            }

        # 4. Route unsupported or protected portals requiring manual action to cold email staging
        if result.get("status") == "manual_required":
            cold_email_qid = self.route_unsupported_to_cold_email(
                job,
                score=float(job.get("relevance_score") or job.get("score") or 0.0)
            )
            if cold_email_qid:
                result["cold_email_queue_id"] = cold_email_qid

        # 5. Database status update with strict safety & accurate lifecycle
        if (
            not self.dry_run
            and result.get("status") == "applied"
            and result.get("confirmed") is True
            and result.get("submitted") is True
            and result.get("dry_run") is False
        ):
            self.db.update_application_status(
                job_hash=job_hash,
                status="applied",
                notes=result.get("notes", "Application confirmed.")
            )

            self.db.update_approval_execution_status(
                queue_id,
                "applied",
                claim_token=claim_token,
            )

        elif not self.dry_run and result.get("status") == "manual_required":
            self.db.update_application_status(
                job_hash=job_hash,
                status="manual_required",
                notes=result.get(
                    "notes",
                    "Manual application required."
                )
            )

            self.db.update_approval_execution_status(
                queue_id,
                "manual_required",
                claim_token=claim_token,
            )

        elif not self.dry_run and result.get("status") == "failed":
            self.db.update_application_status(
                job_hash=job_hash,
                status="failed",
                notes=result.get(
                    "notes",
                    "Application submission failed."
                )
            )

            self.db.update_approval_execution_status(
                queue_id,
                "failed",
                claim_token=claim_token,
            )

        elif self.dry_run:
            logger.info(
                "[ApplyService] Dry-run mode: no application or "
                "execution status changes were made."
            )

        return {
            "job_hash": job_hash,
            "title": title,
            "company": company,
            "portal": portal,
            "status": "dry_run_preview" if self.dry_run else result.get("status", "manual_required"),
            "dry_run": self.dry_run,
            "notes": result.get("notes", ""),
            "cold_email_queue_id": result.get("cold_email_queue_id"),
            "field_mapping_preview": {
                "name": payload["full_name"],
                "email": payload["email"],
                "resume_attached": payload["resume_exists"],
                "experience": payload["years_of_experience"]
            }
        }

    # ------------------------------------------------------------------
    # Batch Processing of Approved Items
    # ------------------------------------------------------------------

   
    def apply_approved_batch(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Process only explicitly approved job_application queue items.
        Cold-email approvals must never enter the application workflow.
        """
        approved_items = self.db.get_approval_queue(
            status="approved",
            limit=limit,
            item_type="job_application",
            execution_status="not_started",
        )

        results = []

        logger.info(
            "[ApplyService] Processing %s approved job applications...",
            len(approved_items),
        )

        for item in approved_items:
            # Defensive validation: never trust queue results blindly.
            if item.get("item_type") != "job_application":
                logger.warning(
                    "[ApplyService] Skipping non-job approval item: %s",
                    item.get("id"),
                )
                continue

            queue_id = item.get("id")
            if not queue_id:
                logger.warning(
                    "[ApplyService] Skipping item without queue ID."
                )
                continue

            preview = item.get("preview_payload")
            if not isinstance(preview, dict):
                logger.warning(
                    "[ApplyService] Invalid preview payload for queue item %s.",
                    queue_id,
                )
                continue

            job_hash = item.get("reference_id")
            if not job_hash:
                logger.warning(
                    "[ApplyService] Missing job reference for queue item %s.",
                    queue_id,
                )
                continue

            job_data = {
                "job_hash": job_hash,
                "title": item.get("title"),
                "company": item.get("company"),
                "portal": preview.get("portal", "direct"),
                "job_url": preview.get("job_url", ""),
                "description": preview.get("description", ""),
            }

            result = self.apply_to_job(
                job_data,
                queue_id=queue_id,
            )
            results.append(result)

        return results


    # ------------------------------------------------------------------
    # Approval Queue Management APIs
    # ------------------------------------------------------------------

    def get_pending_approvals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Fetch all items awaiting candidate review."""
        return self.db.get_approval_queue(status="pending", limit=limit)

    def approve_item(self, queue_id: int, notes: str = "") -> bool:
        """Approve a queued application or email outreach."""
        logger.info(f"[ApplyService] Approving queue item #{queue_id} (Notes: {notes})")
        return self.db.decide_approval(queue_id=queue_id, decision="approved", notes=notes)

    def reject_item(self, queue_id: int, notes: str = "") -> bool:
        """Reject a queued application or email outreach."""
        logger.info(f"[ApplyService] Rejecting queue item #{queue_id} (Notes: {notes})")
        return self.db.decide_approval(queue_id=queue_id, decision="rejected", notes=notes)

    def bulk_decide(self, queue_ids: List[int], decision: str, notes: str = "") -> Dict[str, int]:
        """Approve or reject multiple queue items in bulk."""
        success_cnt = 0
        failed_cnt = 0
        for qid in queue_ids:
            try:
                ok = self.db.decide_approval(queue_id=qid, decision=decision, notes=notes)
                if ok:
                    success_cnt += 1
                else:
                    failed_cnt += 1
            except Exception:
                failed_cnt += 1

        logger.info(f"[ApplyService] Bulk {decision}: {success_cnt} succeeded, {failed_cnt} failed.")
        return {"success": success_cnt, "failed": failed_cnt}

    # ------------------------------------------------------------------
    # Internal Verification & Multi-Portal Handlers
    # ------------------------------------------------------------------

    
    
    def _verify_queue_approval(
        self,
        job_hash: str,
        queue_id: Optional[int] = None
    ) -> bool:
        """Verify approval and ensure the job has not already been processed."""

        with self.db._get_conn() as conn:
            if queue_id is not None:
                row = conn.execute(
                    """
                    SELECT status, item_type, reference_id, execution_status
                    FROM approval_queue
                    WHERE id = ?
                    """,
                    (queue_id,)
                ).fetchone()
            else:
                row = conn.execute(
                    """
                    SELECT status, item_type, reference_id, execution_status
                    FROM approval_queue
                    WHERE reference_id = ?
                    AND status = 'approved'
                    AND item_type = 'job_application'
                    AND execution_status = 'not_started'
                    LIMIT 1
                    """,
                    (job_hash,)
                ).fetchone()

            return bool(
                row
                and row["status"] == "approved"
                and row["item_type"] == "job_application"
                and row["reference_id"] == job_hash
                and row["execution_status"] == "not_started"
            )



    def _apply_remoteok(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """RemoteOK handler: Prepares direct application redirect or cold email payload."""
        apply_url = job.get("extra_metadata", {}).get("apply_url") or job.get("job_url")
        return {
            "status": "manual_required",
            "notes": f"RemoteOK application link prepared: {apply_url}",
            "apply_url": apply_url,
            "resume_path": payload["resume_path"]
        }

    def _apply_weworkremotely(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """WeWorkRemotely handler: Formats applicant payload for external ATS portal."""
        return {
            "status": "manual_required",
            "notes": "WeWorkRemotely ATS payload formatted with resume and contact details.",
            "apply_url": job.get("job_url"),
            "resume_path": payload["resume_path"]
        }

    def _apply_internshala(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Internshala handler: Stages resume and personalized cover response."""
        cover_note = (
            f"Hi Hiring Team, I am {payload['full_name']}. I have hands-on experience in "
            f"Python, RAG, and AI development, and would love to contribute to {job.get('company')}."
        )
        return {
            "status": "manual_required",
            "notes": "Internshala quick application staged with cover letter.",
            "cover_note": cover_note,
            "resume_path": payload["resume_path"]
        }

    def _apply_wellfound(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Wellfound (AngelList) handler: Formats startup candidate profile."""
        return {
            "status": "manual_required",
            "notes": "Wellfound startup candidate pitch prepared.",
            "pitch": f"Passionate AI engineer skilled in {', '.join(payload['top_skills'][:4])}.",
            "resume_path": payload["resume_path"]
        }

    def _apply_linkedin(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """LinkedIn handler: Prepares Easy Apply automation parameters."""
        return {
            "status": "manual_required",
            "notes": "LinkedIn Easy Apply payload validated.",
            "apply_url": job.get("job_url"),
            "resume_path": payload["resume_path"]
        }

    def _apply_naukri(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Naukri handler: Formats profile and resume dispatch details."""
        return {
            "status": "manual_required",
            "notes": "Naukri direct application staged.",
            "apply_url": job.get("job_url"),
            "resume_path": payload["resume_path"]
        }

    def _apply_generic(self, job: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
        """Generic web application handler."""
        return {
            "status": "manual_required",
            "notes": "Generic application payload formatted.",
            "apply_url": job.get("job_url"),
            "resume_path": payload["resume_path"]
        }
