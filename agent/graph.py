"""
agent/graph.py — LangGraph Multi-Agent Workflow Definition
==========================================================
Coordinates job discovery, multi-criteria filtering, semantic scoring,
human approval gating, safe application dispatch, verified recruiter outreach,
inbox monitoring, secondary drip campaign, memory lifecycle, and reporting.

Features:
- Persistent SQLite checkpointing via SqliteSaver with stable thread_id tracking
- Resilient recovery and workflow resumption across restarts
- Mandatory human review safety gate
- Non-blocking error handling and portal isolation
"""

import time
import uuid
import sqlite3
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver

from agent.state import AgentState
from agent.nodes import (
    load_memory_node,
    scrape_jobs_node,
    filter_jobs_node,
    score_jobs_node,
    stage_approvals_node,
    apply_jobs_node,
    email_outreach_node,
    inbox_monitor_node,
    drip_campaign_node,
    pdf_drip_node,
    save_memory_node,
    generate_report_node,
)

import config as cfg

logger = logging.getLogger("CareerPilot.AgentGraph")


def get_checkpointer(db_path: Optional[str] = None) -> SqliteSaver:
    """
    Configure and return an initialized SqliteSaver checkpointer.
    Uses check_same_thread=False for thread safety and avoids database locks.
    """
    if db_path is None:
        db_path = getattr(cfg, "CHECKPOINT_DB_PATH", str(cfg.DATABASE_DIR / "checkpoints.db"))

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    saver = SqliteSaver(conn)
    saver.setup()
    return saver


def should_continue_after_filter(state: AgentState) -> str:
    """Skip scoring when no jobs pass filtering."""
    filtered_jobs = state.get("filtered_jobs", [])

    if not filtered_jobs:
        logger.info(
            "[AgentGraph] No filtered jobs. "
            "Routing directly to inbox monitoring and drip campaign."
        )
        return "inbox_monitor"

    return "score"


def should_apply_after_approval(state: AgentState) -> str:
    """
    Continue only when approved job applications exist.
    Never use scored or merely staged jobs as approval.
    """
    approved_jobs = state.get("approved_jobs", [])

    if not approved_jobs:
        logger.info(
            "[AgentGraph] No approved job applications. "
            "Routing to inbox monitoring and drip campaign."
        )
        return "inbox_monitor"

    logger.info(
        "[AgentGraph] %s approved job applications available.",
        len(approved_jobs),
    )
    return "apply"


def should_email_after_apply(state: AgentState) -> str:
    """
    Route to email node after applications have been processed.
    In dry-run mode, enables staging cold-email draft previews in approval queue.
    In live mode, enables verified recruiter outreach.
    """
    applied_jobs = state.get("applied_jobs", [])

    candidates = [
        job for job in applied_jobs
        if isinstance(job, dict)
        and job.get("status") in ("applied", "manual_required")
    ]

    if not candidates:
        logger.info(
            "[AgentGraph] No candidate applications for email outreach. "
            "Routing to inbox monitor."
        )
        return "inbox_monitor"

    logger.info(
        "[AgentGraph] %s applications available for email staging/outreach.",
        len(candidates),
    )
    return "email"


def build_job_agent_graph(checkpointer: Optional[Any] = None):
    """
    Build and compile the LangGraph state machine with SQLite checkpointer.
    
    If checkpointer is None, initializes default SqliteSaver.
    If checkpointer is False, compiles without checkpointer (for isolated tests).
    """
    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("load_memory", load_memory_node)
    graph.add_node("scrape", scrape_jobs_node)
    graph.add_node("filter", filter_jobs_node)
    graph.add_node("score", score_jobs_node)
    graph.add_node("stage_approvals", stage_approvals_node)
    graph.add_node("apply", apply_jobs_node)
    graph.add_node("email", email_outreach_node)
    graph.add_node("inbox_monitor", inbox_monitor_node)
    graph.add_node("drip", drip_campaign_node)
    graph.add_node("pdf_drip", pdf_drip_node)
    graph.add_node("save_memory", save_memory_node)
    graph.add_node("report", generate_report_node)

    # Pipeline: Ingestion & Filtering
    graph.add_edge(START, "load_memory")
    graph.add_edge("load_memory", "scrape")
    graph.add_edge("scrape", "filter")

    # Routing from filter
    graph.add_conditional_edges(
        "filter",
        should_continue_after_filter,
        {
            "score": "score",
            "inbox_monitor": "inbox_monitor",
            "drip": "drip",
            "pdf_drip": "drip",
        },
    )

    # Scoring and approval staging
    graph.add_edge("score", "stage_approvals")

    # Routing from stage approvals
    graph.add_conditional_edges(
        "stage_approvals",
        should_apply_after_approval,
        {
            "apply": "apply",
            "inbox_monitor": "inbox_monitor",
            "drip": "drip",
            "pdf_drip": "drip",
        },
    )

    # Routing from apply
    graph.add_conditional_edges(
        "apply",
        should_email_after_apply,
        {
            "email": "email",
            "inbox_monitor": "inbox_monitor",
            "drip": "drip",
            "pdf_drip": "drip",
        },
    )

    # Downstream execution flow
    graph.add_edge("email", "inbox_monitor")
    graph.add_edge("inbox_monitor", "drip")
    graph.add_edge("drip", "save_memory")
    graph.add_edge("pdf_drip", "save_memory")
    graph.add_edge("save_memory", "report")
    graph.add_edge("report", END)

    if checkpointer is None:
        checkpointer = get_checkpointer()
    elif checkpointer is False:
        checkpointer = None

    compiled_app = graph.compile(checkpointer=checkpointer)

    logger.info("[AgentGraph] Compiled LangGraph StateGraph successfully.")
    return compiled_app


def get_initial_state(
    dry_run: bool = True,
    require_approval: bool = True,
    mock_mode: Optional[bool] = None,
) -> AgentState:
    """Construct the initial state for a workflow execution."""
    run_id = f"run_{uuid.uuid4().hex[:10]}"

    if mock_mode is None:
        mock_mode = getattr(cfg, "MOCK_MODE", False)

    return {
        "run_id": run_id,
        "run_date": datetime.now().strftime("%Y-%m-%d"),
        "dry_run": dry_run,
        "mock_mode": mock_mode,
        "require_human_approval": require_approval,
        "start_time": time.time(),
        "session_memory": {},
        "raw_jobs": [],
        "filtered_jobs": [],
        "rejected_jobs": [],
        "scored_jobs": [],
        "staged_approvals": [],
        "approved_jobs": [],
        "applied_jobs": [],
        "emailed_jobs": [],
        "inbox_messages": [],
        "inbox_stats": {},
        "csv_drip_staged": [],
        "pdf_drip_contacts": [],
        "pdf_drip_sent": [],
        "pdf_drip_prepared": [],
        "stats": {},
        "logs": [],
        "errors": [],
        "report_summary": {},
        "report_html": None,
    }


def execute_workflow(
    dry_run: bool = True,
    require_approval: bool = True,
    mock_mode: Optional[bool] = None,
    thread_id: Optional[str] = None,
    checkpointer: Optional[Any] = None,
) -> Dict[str, Any]:
    """Initialize and execute the LangGraph workflow with persistent checkpointing."""
    if checkpointer is None:
        checkpointer = get_checkpointer()

    app = build_job_agent_graph(checkpointer=checkpointer)

    initial_state = get_initial_state(
        dry_run=dry_run,
        require_approval=require_approval,
        mock_mode=mock_mode,
    )
    if thread_id:
        initial_state["run_id"] = thread_id

    stable_thread_id = thread_id or initial_state["run_id"]
    run_config = {"configurable": {"thread_id": stable_thread_id}}

    final_state = app.invoke(initial_state, config=run_config)
    return final_state


def resume_workflow(
    thread_id: str,
    new_inputs: Optional[Dict[str, Any]] = None,
    checkpointer: Optional[Any] = None,
) -> Dict[str, Any]:
    """Resume an existing workflow execution from its saved SQLite checkpoint."""
    if checkpointer is None:
        checkpointer = get_checkpointer()

    app = build_job_agent_graph(checkpointer=checkpointer)
    run_config = {"configurable": {"thread_id": thread_id}}

    final_state = app.invoke(new_inputs, config=run_config)
    return final_state
