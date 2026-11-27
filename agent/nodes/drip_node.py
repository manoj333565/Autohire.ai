"""
agent/nodes/drip_node.py — Multi-Channel Recruiter Drip Node (CSV & PDF)
========================================================================
Integrates DripService (CSV-based recruiter outreach) and legacy PDF HR contact
ingestion into a unified, approval-controlled drip stage.
Enforces daily limits and stages items into approval_queue with item_type='drip_email'.
"""

import logging
from typing import Dict, Any, List

from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.drip_service import DripService

logger = logging.getLogger("CareerPilot.Nodes.Drip")


def drip_campaign_node(state: AgentState) -> Dict[str, Any]:
    """
    Execute drip campaign staging:
    1. Stages pending CSV drip contacts using DripService.stage_pending_for_approval()
    2. Enforces daily limits
    3. Reads PDF HR contacts for preview (preserving legacy functionality)
    4. Respects dry_run: stages for human review without sending SMTP emails
    """
    dry_run = state.get("dry_run", True)
    require_approval = state.get("require_human_approval", True)
    limit = max(1, int(getattr(cfg, "DAILY_DRIP_LIMIT", 5)))

    logger.info(
        f"[DripNode] Executing drip campaign staging (Dry-Run: {dry_run}, Limit: {limit})..."
    )

    db = DatabaseManager(cfg.DB_PATH)
    drip_service = DripService(
        db_manager=db,
        dry_run=dry_run,
        require_approval=require_approval,
    )

    staged_queue_ids: List[int] = []
    errors: List[str] = []

    # 1. Stage pending CSV contacts into approval_queue (item_type='drip_email')
    try:
        staged_queue_ids = drip_service.stage_pending_for_approval(limit=limit)
    except Exception as e:
        err = f"CSV drip staging error: {e}"
        logger.warning(f"[DripNode] {err}")
        errors.append(err)

    # 2. Ingest/Preview legacy PDF HR contacts
    prepared_records = []
    pdf_contacts = []
    try:
        pdf_contacts = db.get_pdf_hr_batch(limit=limit)
        for contact in pdf_contacts:
            prepared_records.append({
                "email": contact.get("email"),
                "company": contact.get("company") or "Company",
                "hr_name": contact.get("name") or "Hiring Manager",
                "status": "pending_human_review",
                "dry_run": True,
                "email_sent": False,
            })
    except Exception as e:
        err = f"PDF HR contact read error: {e}"
        logger.warning(f"[DripNode] {err}")
        errors.append(err)

    summary_log = (
        f"Drip Campaign: Staged {len(staged_queue_ids)} CSV contacts into approval queue "
        f"and prepared {len(prepared_records)} PDF contacts for preview."
    )
    logger.info(f"[DripNode] {summary_log}")

    return {
        "csv_drip_staged": staged_queue_ids,
        "pdf_drip_contacts": pdf_contacts,
        "pdf_drip_sent": [],
        "pdf_drip_prepared": prepared_records,
        "logs": [summary_log],
        "errors": errors,
        "stats": {
            **state.get("stats", {}),
            "csv_drip_staged": len(staged_queue_ids),
            "pdf_drip_processed": len(prepared_records),
            "hr_drip_processed": len(prepared_records) + len(staged_queue_ids),
            "drip_sent": 0,
        },
    }


# Backwards compatibility aliases
pdf_drip_node = drip_campaign_node
hr_drip_node = drip_campaign_node
