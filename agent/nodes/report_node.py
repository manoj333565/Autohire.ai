"""
agent/nodes/report_node.py — Daily Reporting & Run Analytics Node (F9)
======================================================================
Compiles workflow analytics, records run metrics in `daily_stats` and
`agent_runs` SQLite tables, and generates the executive daily run summary.
"""

import time
import logging
from datetime import datetime
from typing import Dict, Any
from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager

logger = logging.getLogger("CareerPilot.Nodes.Report")


def generate_report_node(state: AgentState) -> Dict[str, Any]:
    """
    Synthesize end-of-run analytics, persist daily metrics, and close the run record.
    """
    run_id = state.get("run_id", "default_run")
    start_time = state.get("start_time", time.time())
    duration = round(time.time() - start_time, 2)
    today_str = datetime.now().strftime("%Y-%m-%d")

    raw_jobs_cnt = len(state.get("raw_jobs", []))
    filtered_cnt = len(state.get("filtered_jobs", []))
    scored_cnt = len(state.get("scored_jobs", []))
    staged_cnt = len(state.get("staged_approvals", []))
    applied_cnt = len(state.get("applied_jobs", []))
    emailed_cnt = len(state.get("emailed_jobs", []))
    pdf_drip_cnt = len(state.get("pdf_drip_sent", []))
    errors = state.get("errors", [])

    logger.info(f"[ReportNode] Compiling analytics for run {run_id} ({duration}s)...")

    db = DatabaseManager(cfg.DB_PATH)

    # 1. Update daily_stats table
    try:
        db.update_daily_stats(
            date=today_str,
            total_scraped=raw_jobs_cnt,
            total_new=filtered_cnt,
            total_applied=applied_cnt,
            total_emailed=emailed_cnt,
            run_time_seconds=duration
        )
    except Exception as e:
        logger.warning(f"[ReportNode] Could not update daily_stats: {e}")

    # 2. Record agent run completion in agent_runs
    status_str = "completed" if not errors else "completed_with_warnings"
    try:
        db.record_agent_run_complete(
            run_id=run_id,
            status=status_str,
            duration_seconds=duration,
            scraped=raw_jobs_cnt,
            new_jobs=filtered_cnt,
            scored=scored_cnt,
            applied=applied_cnt,
            emailed=emailed_cnt,
            failed=len(errors),
            pdf_emails=pdf_drip_cnt,
            logs="\n".join(state.get("logs", [])),
            errors="\n".join(errors)
        )
    except Exception as e:
        logger.warning(f"[ReportNode] Could not record agent run completion: {e}")

    report_summary = {
        "run_id": run_id,
        "run_date": today_str,
        "duration_seconds": duration,
        "status": status_str,
        "dry_run": state.get("dry_run", True),
        "candidate": cfg.CANDIDATE_NAME,
        "metrics": {
            "scraped": raw_jobs_cnt,
            "passed_filters": filtered_cnt,
            "scored": scored_cnt,
            "staged_approvals": staged_cnt,
            "applied": applied_cnt,
            "emailed": emailed_cnt,
            "pdf_drip_sent": pdf_drip_cnt,
            "errors": len(errors)
        }
    }

    # 3. Generate Executive HTML Report via ReportService (F9)
    report_html = ""
    try:
        from services.report_service import ReportService
        report_svc = ReportService(db_manager=db, dry_run=state.get("dry_run", True))
        report_result = report_svc.generate_daily_report(
            state=state,
            date_str=today_str,
            send_email=False,
            dry_run=state.get("dry_run", True)
        )
        report_html = report_result.get("html_content", "")
        report_summary["html_path"] = report_result.get("html_path", "")
    except Exception as e:
        logger.warning(f"[ReportNode] Could not generate executive HTML report: {e}")

    log_entry = (
        f"Workflow Execution Complete in {duration}s! "
        f"Scraped: {raw_jobs_cnt} | Filtered: {filtered_cnt} | Scored: {scored_cnt} | "
        f"Staged: {staged_cnt} | Applied: {applied_cnt} | Drip: {pdf_drip_cnt}"
    )
    logger.info(f"[ReportNode] {log_entry}")

    return {
        "report_summary": report_summary,
        "report_html": report_html,
        "logs": [log_entry]
    }

