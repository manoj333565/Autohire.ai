"""
agent/nodes/filter_node.py — 7-Stage Intelligent Filtering & Skills-Gap Node (F2)
================================================================================
Applies 7-stage filtering: SHA-256 deduplication, blacklist, Java rejection,
whitelist/role check, seniority exclusion, location check, and NLP skills-gap.
"""

import logging
from typing import Dict, Any
from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.filter_service import FilterService

logger = logging.getLogger("CareerPilot.Nodes.Filter")


def filter_jobs_node(state: AgentState) -> Dict[str, Any]:
    """
    Filter raw jobs using 7-stage pipeline and candidate preferences.
    """
    raw_jobs = state.get("raw_jobs", [])
    logger.info(f"[FilterNode] Running 7-stage filtering on {len(raw_jobs)} raw listings...")

    db = DatabaseManager(cfg.DB_PATH)
    filter_service = FilterService(db_manager=db)

    passed, rejected = filter_service.run_pipeline(raw_jobs)
    filter_stats = filter_service.get_stats()

    log_entry = (
        f"Filter Funnel: {len(raw_jobs)} in -> {len(passed)} passed | "
        f"{len(rejected)} rejected (S1={filter_stats.get('rejected_stage1')}, "
        f"S2={filter_stats.get('rejected_stage2')}, S3={filter_stats.get('rejected_stage3')}, "
        f"S4={filter_stats.get('rejected_stage4')}, S5={filter_stats.get('rejected_stage5')}, "
        f"S6={filter_stats.get('rejected_stage6')}, S7={filter_stats.get('rejected_stage7')})"
    )
    logger.info(f"[FilterNode] {log_entry}")

    # Also persist passed new jobs into applications table so they have DB tracking records
    for job in passed:
        try:
            db.add_job(job)
        except Exception as e:
            logger.debug(f"[FilterNode] Job already exists in applications: {e}")

    return {
        "filtered_jobs": passed,
        "rejected_jobs": rejected,
        "logs": [log_entry],
        "stats": {
            **state.get("stats", {}),
            "filter_passed": len(passed),
            "filter_rejected": len(rejected),
            "filter_details": filter_stats
        }
    }
