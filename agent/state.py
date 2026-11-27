"""
agent/state.py — LangGraph TypedDict AgentState definition with Reducers
========================================================================
Maintains end-to-end multi-agent execution state across scraping, filtering,
scoring, approval staging, applying, cold outreach, memory, and reporting.
"""

from typing import TypedDict, List, Dict, Any, Optional, Annotated
from operator import add


class AgentState(TypedDict):
    """Unified LangGraph Agent State."""

    # ── Execution Metadata ─────────────────────────────────────────────
    run_id: str
    run_date: str
    dry_run: bool
    mock_mode: Optional[bool]
    require_human_approval: bool
    start_time: float

    # ── Memory ─────────────────────────────────────────────────────────
    session_memory: Dict[str, Any]

    # ── Scraping, Filtering & Scoring ──────────────────────────────────
    raw_jobs: List[Dict[str, Any]]
    filtered_jobs: List[Dict[str, Any]]
    rejected_jobs: List[Dict[str, Any]]
    scored_jobs: List[Dict[str, Any]]

    # ── Approval Safety Gate ───────────────────────────────────────────
    staged_approvals: List[Dict[str, Any]]
    approved_jobs: List[Dict[str, Any]]

    # ── Action Execution ───────────────────────────────────────────────
    applied_jobs: List[Dict[str, Any]]
    emailed_jobs: List[Dict[str, Any]]

    # ── PDF & CSV HR Drip Campaign ─────────────────────────────────────
    pdf_drip_contacts: List[Dict[str, Any]]
    pdf_drip_sent: List[Dict[str, Any]]
    pdf_drip_prepared: Optional[List[Dict[str, Any]]]
    csv_drip_staged: Optional[List[int]]

    # ── Inbox Monitoring & Response Tracking ───────────────────────────
    inbox_messages: Optional[List[Dict[str, Any]]]
    inbox_stats: Optional[Dict[str, Any]]

    # ── Aggregated Stats, Logs & Errors ────────────────────────────────
    stats: Dict[str, Any]
    logs: Annotated[List[str], add]
    errors: Annotated[List[str], add]

    # ── Final Report Summary ───────────────────────────────────────────
    report_summary: Dict[str, Any]
    report_html: Optional[str]

