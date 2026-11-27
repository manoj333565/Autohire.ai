"""
agent/nodes/memory_node.py — Cross-Session Memory Management Nodes
==================================================================
Manages state persistence across agent invocations using SQLite `memory_store`,
`daily_memory_summaries`, and records agent run start / lifecycle.

Features:
- Startup Context Synthesis: Injects previous summaries, applied hashes, and interaction notes
  into AgentState to prevent repeating actions while keeping DB constraints authoritative.
- Persistent Daily Summaries: Reflects actual confirmed SQLite operations and counts.
"""

import logging
from datetime import datetime
from typing import Dict, Any

from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.memory_service import MemoryService

logger = logging.getLogger("CareerPilot.Nodes.Memory")


def load_memory_node(state: AgentState) -> Dict[str, Any]:
    """
    Load cross-session memory, previous run insights, and record run start in DB.
    Injects previous daily summaries, completed operations, and candidate insights.
    """
    run_id = state.get("run_id", "default_run")
    dry_run = state.get("dry_run", True)
    logger.info(f"[MemoryNode] Initializing run {run_id} (Dry-run: {dry_run})...")

    db = DatabaseManager(cfg.DB_PATH)
    memory_service = MemoryService(db_manager=db)

    # 1. Record run start in agent_runs table
    try:
        db.record_agent_run_start(run_id=run_id, dry_run=dry_run)
    except Exception as e:
        logger.warning(f"[MemoryNode] Warning recording run start: {e}")

    # 2. Synthesize startup context across historical summaries and records
    startup_context = memory_service.synthesize_startup_context()

    # 3. Retain backwards-compatible top-level keys for existing nodes
    session_mem = {
        **startup_context,
        "daily_outreach_counter": db.get_daily_sent_email_count(),
    }

    # Also pull last_run_summary if available
    last_summary_row = db.get_memory("last_run_summary")
    if last_summary_row:
        session_mem["last_run_summary"] = last_summary_row.get("content")

    summary_cnt = len(startup_context.get("recent_daily_summaries", []))
    applied_cnt = len(startup_context.get("previously_applied_hashes", []))
    emailed_cnt = len(startup_context.get("previously_emailed_recipients", []))

    summary_log = (
        f"Run {run_id} started. Loaded {summary_cnt} daily summaries, "
        f"{applied_cnt} previous applications, and {emailed_cnt} sent email contacts."
    )
    logger.info(f"[MemoryNode] {summary_log}")

    return {
        "session_memory": session_mem,
        "logs": [summary_log],
    }


def save_memory_node(state: AgentState) -> Dict[str, Any]:
    """
    Save session learnings and metrics into SQLite `memory_store` and `daily_memory_summaries`.
    Ensures that summary counts reflect actual verified database records.
    """
    run_id = state.get("run_id", "default_run")
    run_date = state.get("run_date") or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"[MemoryNode] Persisting cross-session memory for run {run_id}...")

    db = DatabaseManager(cfg.DB_PATH)
    memory_service = MemoryService(db_manager=db)

    metrics = {
        "total_scraped": len(state.get("raw_jobs", [])),
        "passed_filters": len(state.get("filtered_jobs", [])),
        "scored": len(state.get("scored_jobs", [])),
        "applied": len(state.get("applied_jobs", [])),
        "emailed": len(state.get("emailed_jobs", [])),
        "pdf_drip": len(state.get("pdf_drip_sent", [])),
    }

    errors = state.get("errors", [])

    completed_actions = []
    if metrics["total_scraped"] > 0:
        completed_actions.append(f"scraped_{metrics['total_scraped']}_jobs")
    if metrics["passed_filters"] > 0:
        completed_actions.append(f"filtered_{metrics['passed_filters']}_jobs")
    if metrics["scored"] > 0:
        completed_actions.append(f"scored_{metrics['scored']}_jobs")
    if len(state.get("staged_approvals", [])) > 0:
        completed_actions.append(f"staged_{len(state.get('staged_approvals', []))}_approvals")
    if metrics["applied"] > 0:
        completed_actions.append(f"applied_{metrics['applied']}_jobs")
    if metrics["emailed"] > 0:
        completed_actions.append(f"emailed_{metrics['emailed']}_recruiters")
    if len(state.get("inbox_messages", [])) > 0:
        completed_actions.append(f"inspected_{len(state.get('inbox_messages', []))}_inbox_emails")
    if len(state.get("csv_drip_staged", [])) > 0:
        completed_actions.append(f"staged_{len(state.get('csv_drip_staged', []))}_csv_drip")

    # 1. Save persistent daily memory summary
    daily_summary_record = memory_service.save_daily_summary(
        run_id=run_id,
        date_str=run_date,
        metrics=metrics,
        errors=errors,
        completed_actions=completed_actions,
    )

    # 2. Maintain legacy last_run_summary key for backward compatibility
    try:
        summary_payload = {
            "run_id": run_id,
            "run_date": run_date,
            "raw_jobs_scraped": metrics["total_scraped"],
            "filtered_passed": metrics["passed_filters"],
            "scored_jobs": metrics["scored"],
            "staged_approvals": len(state.get("staged_approvals", [])),
            "applied_count": metrics["applied"],
            "emailed_count": metrics["emailed"],
            "pdf_drip_count": metrics["pdf_drip"],
            "confirmed_db_applied": daily_summary_record.get("total_applications", 0),
            "confirmed_db_emailed": daily_summary_record.get("total_emails_sent", 0),
            "pending_approvals": daily_summary_record.get("pending_approvals", 0),
        }
        db.store_memory(
            memory_key="last_run_summary",
            category="workflow_stats",
            content=str(summary_payload),
            metadata=summary_payload
        )
    except Exception as e:
        logger.error(f"[MemoryNode] Error saving legacy memory summary: {e}")

    log_entry = f"Run {run_id} cross-session memory persisted."
    logger.info(f"[MemoryNode] {log_entry}")

    return {
        "logs": [log_entry],
    }
