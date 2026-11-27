"""
agent/nodes/inbox_node.py — Inbox Monitor Node (F5 / F6 Integration)
=====================================================================
Periodically scans candidate inbox for recruiter responses, interviews,
and bounces using EmailService.run_inbox_monitor_cycle().
Fault-tolerant: IMAP connection failures or offline network do not crash the workflow.
"""

import logging
from typing import Dict, Any

from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.email_service import EmailService

logger = logging.getLogger("CareerPilot.Nodes.InboxMonitor")


def inbox_monitor_node(state: AgentState) -> Dict[str, Any]:
    """
    Execute inbox monitoring cycle:
    - Scans recent recruiter messages via IMAP/Gmail API
    - Classifies emails (interview, rejection, bounce, general)
    - Updates application and contact records in SQLite
    - Handles failures gracefully without interrupting workflow
    """
    dry_run = state.get("dry_run", True)
    logger.info(f"[InboxMonitorNode] Starting inbox monitoring cycle (Dry-Run: {dry_run})...")

    db = DatabaseManager(cfg.DB_PATH)
    email_service = EmailService(
        db_manager=db,
        dry_run=dry_run,
        require_approval=state.get("require_human_approval", True),
    )

    try:
        # Run monitoring cycle for recent 3 days
        cycle_result = email_service.run_inbox_monitor_cycle(days_back=3)
        inspected = cycle_result.get("processed_count", 0)
        cycle_stats = cycle_result.get("stats", {})
        messages = cycle_result.get("messages", [])

        interviews = cycle_stats.get("interviews_detected", 0)
        bounces = cycle_stats.get("bounces_handled", 0)
        rejections = cycle_stats.get("rejections_logged", 0)

        log_msg = (
            f"Inbox Monitor: Inspected {inspected} recent emails: "
            f"{interviews} interviews, {bounces} bounces, {rejections} rejections."
        )
        logger.info(f"[InboxMonitorNode] {log_msg}")

        return {
            "inbox_messages": messages,
            "inbox_stats": cycle_stats,
            "logs": [log_msg],
            "stats": {
                **state.get("stats", {}),
                "inbox_inspected": inspected,
                "interviews_detected": interviews,
                "bounces_handled": bounces,
                "rejections_logged": rejections,
            },
        }

    except Exception as e:
        err_msg = f"Inbox monitoring cycle encountered error: {e}"
        logger.warning(f"[InboxMonitorNode] {err_msg}")
        return {
            "inbox_messages": [],
            "inbox_stats": {"error": str(e)},
            "errors": [err_msg],
            "logs": [f"Inbox monitor safely bypassed due to network/IMAP error: {e}"],
            "stats": {
                **state.get("stats", {}),
                "inbox_inspected": 0,
                "interviews_detected": 0,
                "bounces_handled": 0,
            },
        }
