
"""
agent/nodes/stage_approvals_node.py — Human Approval Staging Safety Gate (F4 Safety)
====================================================================================
Enforces mandatory human-in-the-loop oversight before any application submission
or external communication. Enqueues qualified jobs into SQLite `approval_queue`.
"""

import logging
from typing import Dict, Any
from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager

logger = logging.getLogger("CareerPilot.Nodes.StageApprovals")


def stage_approvals_node(state: AgentState) -> Dict[str, Any]:
    """
    Stage qualified jobs into the approval queue for human review,
    and fetch previously approved jobs ready for execution.
    """
    scored_jobs = state.get("scored_jobs", [])
    threshold = cfg.MATCH_SCORE_THRESHOLD

    logger.info(
        f"[StageApprovalsNode] Enforcing human-approval safety gate "
        f"(Threshold: {threshold}/10)..."
    )

    db = DatabaseManager(cfg.DB_PATH)
    staged_items = []

    # 1. Enqueue jobs meeting threshold into approval queue
    for job in scored_jobs:
        score = job.get("relevance_score", 0.0)

        if score >= threshold:
            job_hash = job.get("job_hash") or db.make_job_hash(
                job.get("job_url", "")
            )
            title = job.get("title", "Untitled Job")
            company = job.get("company", "Confidential")

            llm_sc = job.get("llm_score")
            emb_sc = job.get("embedding_score")
            score_breakdown = f"Score: {score}/10"
            if emb_sc is not None and llm_sc is not None:
                score_breakdown += f" (LLM: {llm_sc} | Emb: {emb_sc})"

            details = (
                f"{score_breakdown} | Matched: "
                f"{', '.join(job.get('matched_skills', [])[:4])}"
            )

            try:
                queue_id = db.enqueue_for_approval(
                    item_type="job_application",
                    reference_id=job_hash,
                    title=title,
                    company=company,
                    details=details,
                    preview_payload=job,
                    score=score
                )

                if queue_id:
                    staged_items.append({
                        **job,
                        "queue_id": queue_id
                    })

                    logger.info(
                        f"[STAGED] '{title}' at {company} "
                        f"(Score: {score}) -> Queue ID {queue_id}"
                    )

            except Exception as e:
                logger.debug(
                    f"[StageApprovalsNode] Item already staged "
                    f"or queue conflict: {e}"
                )

    # 2. Retrieve ONLY approved job applications.
    # Cold emails and other approval types must never enter
    # the application workflow.

    approved_queue_items = db.get_approval_queue(
        status="approved",
        limit=cfg.MAX_APPLICATIONS_PER_DAY,
        item_type="job_application",
        execution_status="not_started"

    )

    approved_jobs = []

    for item in approved_queue_items:

        # Reject cold emails and all other approval types.
        if item.get("item_type") != "job_application":
            continue

        preview_payload = item.get("preview_payload")

        # Reject invalid or missing job payloads.
        if not isinstance(preview_payload, dict):
            logger.warning(
                "[StageApprovalsNode] Skipping invalid job approval payload. "
                "Queue ID: %s",
                item.get("id") or item.get("queue_id")
            )
            continue

        approved_jobs.append({
            **preview_payload,
            "queue_id": item.get("id") or item.get("queue_id")
        })

    # 3. Generate execution summary.
    summary_log = (
        f"Staged {len(staged_items)} jobs into Approval Queue. "
        f"{len(approved_jobs)} candidate-approved jobs ready for execution."
    )

    logger.info(f"[StageApprovalsNode] {summary_log}")

    return {
        "staged_approvals": staged_items,
        "approved_jobs": approved_jobs,
        "logs": [summary_log],
        "stats": {
            **state.get("stats", {}),
            "staged_for_approval": len(staged_items),
            "approved_ready_count": len(approved_jobs)
        }
    }
