"""
agent/nodes/email_node.py — Safe Recruiter Outreach Node
=========================================================
Processes confirmed live applications or simulated applications in dry-run mode.
Stages verified recruiter emails into SQLite approval_queue.
In live mode (dry_run=False), dispatches only human-approved outreach.
In dry-run mode (dry_run=True), stages draft previews without connecting to SMTP.
Never invents recipient addresses or bypasses approval.
"""

import logging
from typing import Dict, Any

from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.email_service import EmailService

logger = logging.getLogger("CareerPilot.Nodes.Email")


def email_outreach_node(state: AgentState) -> Dict[str, Any]:
    """
    Process recruiter outreach:
    - Stages personalized drafts for applications in approval_queue (item_type='cold_email')
    - When dry_run=True: Generates previews and stages drafts; ZERO SMTP emails dispatched
    - When dry_run=False: Dispatches only previously approved items via EmailService
    - Every email requires a verified HR contact and explicit human approval
    """
    dry_run = state.get("dry_run", True)
    require_approval = state.get("require_human_approval", True)
    applied_jobs = state.get("applied_jobs", [])
    emailed_results = []

    # Human approval is mandatory for this workflow.
    if not require_approval:
        message = (
            "Recruiter outreach blocked: mandatory human approval is not enabled."
        )
        logger.warning(f"[EmailNode] {message}")
        return {
            "emailed_jobs": [],
            "logs": [message],
            "stats": {
                **state.get("stats", {}),
                "emailed_count": 0,
            },
        }

    if not isinstance(applied_jobs, list):
        applied_jobs = []

    db = DatabaseManager(cfg.DB_PATH)
    email_service = EmailService(
        db_manager=db,
        dry_run=dry_run,
        require_approval=True,
    )

    # In dry-run mode, all processed applications (applied or manual_required) are eligible for draft preview
    # In live mode, only confirmed non-dry-run applications are eligible
    if dry_run:
        candidates = [
            job for job in applied_jobs
            if isinstance(job, dict) and job.get("status") in ("applied", "manual_required")
        ]
    else:
        candidates = [
            job for job in applied_jobs
            if isinstance(job, dict)
            and job.get("status") in ("applied", "manual_required")
            and job.get("dry_run") is False
        ]

    logger.info(
        f"[EmailNode] Reviewing {len(candidates)} application candidates (Dry-Run: {dry_run})."
    )

    daily_sent = db.get_daily_sent_email_count()
    max_daily = getattr(cfg, "MAX_EMAILS_PER_DAY", 25)
    remaining_quota = max(0, max_daily - daily_sent)

    if remaining_quota <= 0:
        msg = f"Daily recruiter email limit reached ({daily_sent}/{max_daily} sent today). Outreach paused."
        logger.info(f"[EmailNode] {msg}")
        return {
            "emailed_jobs": [],
            "logs": [msg],
            "stats": {
                **state.get("stats", {}),
                "emailed_count": 0,
            },
        }

    for job in candidates[:remaining_quota]:
        title = job.get("title", "Role")
        company = job.get("company", "Company")

        job_url = job.get("job_url") or job.get("url") or ""
        job_hash = job.get("job_hash") or db.make_job_hash(job_url)

        if not job_hash:
            logger.warning(f"[EmailNode] Skipping {company}: job reference is missing.")
            continue

        try:
            # 1. In live mode (dry_run=False), check if this outreach was already approved by a human
            if not dry_run:
                approved_items = db.get_approval_queue(
                    status="approved",
                    limit=500,
                    item_type="cold_email",
                )
                approved_item = next(
                    (
                        item for item in approved_items
                        if item.get("reference_id") == job_hash
                    ),
                    None,
                )

                if approved_item:
                    # EmailService independently verifies approval, rate limits, and sends
                    result = email_service.send_approved_outreach(approved_item["id"])
                    emailed_results.append({
                        "company": company,
                        "title": title,
                        "status": result.get("status", "unknown"),
                        "recipient": result.get("recipient"),
                        "queue_id": approved_item["id"],
                        "notes": "Live email outreach sent via approval-controlled email service.",
                    })
                    continue

            # 2. Check if a pending draft already exists in the approval queue
            pending_items = db.get_approval_queue(
                status="pending",
                limit=500,
                item_type="cold_email",
            )
            pending_item = next(
                (
                    item for item in pending_items
                    if item.get("reference_id") == job_hash
                ),
                None,
            )

            if pending_item:
                emailed_results.append({
                    "company": company,
                    "title": title,
                    "status": "awaiting_approval",
                    "queue_id": pending_item["id"],
                    "notes": "Existing cold-email request awaits review in approval queue.",
                })
                continue

            # 3. Stage a new outreach request via EmailService discovery waterfall
            queue_id = email_service.stage_outreach_for_approval(
                job,
                score=job.get("relevance_score", 0.0),
            )

            if queue_id:
                emailed_results.append({
                    "company": company,
                    "title": title,
                    "status": "awaiting_approval",
                    "queue_id": queue_id,
                    "notes": (
                        "Verified email draft staged for human approval. "
                        "Zero SMTP emails sent (dry-run mode: "
                        f"{dry_run})."
                    ),
                })
            else:
                emailed_results.append({
                    "company": company,
                    "title": title,
                    "status": "skipped_unverified_contact",
                    "notes": "No independently verified recruiter contact was available.",
                })

        except Exception as e:
            logger.exception(f"[EmailNode] Outreach processing failed for {company}: {e}")
            emailed_results.append({
                "company": company,
                "title": title,
                "status": "failed",
                "notes": f"Outreach processing error: {e}",
            })

    log_entry = (
        f"Recruiter Outreach: {len(emailed_results)} opportunities processed "
        f"(Dry-Run: {dry_run}, Staged: {sum(1 for r in emailed_results if r.get('status') == 'awaiting_approval')}, "
        f"Sent: {sum(1 for r in emailed_results if r.get('status') == 'sent')})."
    )
    logger.info(f"[EmailNode] {log_entry}")

    return {
        "emailed_jobs": emailed_results,
        "logs": [log_entry],
        "stats": {
            **state.get("stats", {}),
            "emailed_count": sum(
                1 for item in emailed_results
                if item.get("status") == "sent"
            ),
            "email_staged_count": sum(
                1 for item in emailed_results
                if item.get("status") == "awaiting_approval"
            ),
        },
    }
