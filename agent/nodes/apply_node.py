
"""
agent/nodes/apply_node.py — Application Execution Node

Processes only explicitly approved job applications.
Dry-run mode never submits applications or changes application records.
"""

import logging
from typing import Dict, Any

from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.apply_service import ApplyService

logger = logging.getLogger("CareerPilot.Nodes.Apply")


def apply_jobs_node(state: AgentState) -> Dict[str, Any]:
    """
    Process only explicitly approved job applications.

    No fallback to staged or scored jobs is permitted.
    """

    dry_run = state.get("dry_run", True)
    require_approval = state.get("require_human_approval", True)
    approved_jobs = state.get("approved_jobs", [])

    logger.info(
        "[ApplyNode] Starting application processing "
        "(Dry-Run: %s, Approval Required: %s)",
        dry_run,
        require_approval,
    )

    db = DatabaseManager(cfg.DB_PATH)

    apply_service = ApplyService(
        db_manager=db,
        dry_run=dry_run,
        require_approval=require_approval,
    )

    applied_results = []

    if not approved_jobs:
        log_entry = (
            "Application Engine: No explicitly approved job applications. "
            "Nothing processed."
        )

        logger.info("[ApplyNode] %s", log_entry)

        return {
            "applied_jobs": [],
            "logs": [log_entry],
            "stats": {
                **state.get("stats", {}),
                "applied_count": 0,
            },
        }

    for job in approved_jobs:
        if not isinstance(job, dict):
            logger.warning("[ApplyNode] Skipping invalid job entry.")
            continue

        queue_id = job.get("queue_id")

        title = job.get("title", "Job")
        company = job.get("company", "Company")

        # Require the actual approval queue ID.
        if queue_id is None:
            logger.warning(
                "[ApplyNode] Skipping '%s' at '%s': "
                "approval queue ID is missing.",
                title,
                company,
            )
            continue

        result = apply_service.apply_to_job(
            job,
            queue_id=queue_id,
        )

        applied_results.append(result)

        logger.info(
            "[ApplyNode] %s: %s at %s",
            result.get("status"),
            title,
            company,
        )

    log_entry = (
        f"Application Engine: {len(applied_results)} "
        "approved job applications processed. "
        f"Mode: {'Dry-Run' if dry_run else 'Live'}."
    )

    logger.info("[ApplyNode] %s", log_entry)

    return {
        "applied_jobs": applied_results,
        "logs": [log_entry],
        "stats": {
            **state.get("stats", {}),
            "applied_count": len(applied_results),
        },
    }
