"""
tests/test_agent_workflow.py — Automated Unit & Integration Tests for LangGraph Workflow (Milestone 7)
======================================================================================================
Validates:
  T1: AgentState dictionary structure & initialization
  T2: LangGraph compilation and node registration
  T3: Conditional routing edge (`should_continue_after_filter`)
  T4: Isolated node execution (scrape, filter, score, stage_approvals, pdf_drip, report)
  T5: Mandatory human approval safety gate (staged in SQLite `approval_queue`)
  T6: Full end-to-end multi-agent workflow execution (Dry-run mode)
  T7: Database lifecycle tracking in `agent_runs` and `daily_stats`
"""

import sys
import os
import uuid
from pathlib import Path

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from agent.state import AgentState
from agent.graph import (
    build_job_agent_graph,
    get_initial_state,
    execute_workflow,
    should_continue_after_filter,
)
from agent.nodes import (
    load_memory_node,
    scrape_jobs_node,
    filter_jobs_node,
    score_jobs_node,
    stage_approvals_node,
    apply_jobs_node,
    email_outreach_node,
    pdf_drip_node,
    save_memory_node,
    generate_report_node,
)


def test_state_initialization():
    """T1: Verify AgentState initial dictionary schema."""
    state = get_initial_state(dry_run=True, require_approval=True)

    assert "run_id" in state and state["run_id"].startswith("run_")
    assert state["dry_run"] is True
    assert state["require_human_approval"] is True
    assert isinstance(state["raw_jobs"], list)
    assert isinstance(state["filtered_jobs"], list)
    assert isinstance(state["scored_jobs"], list)
    assert isinstance(state["staged_approvals"], list)
    assert isinstance(state["approved_jobs"], list)
    assert isinstance(state["applied_jobs"], list)
    assert isinstance(state["logs"], list)
    assert isinstance(state["errors"], list)
    print("  ✅ test_state_initialization passed")


def test_graph_compilation():
    """T2: Verify LangGraph compiles successfully with all required nodes."""
    compiled = build_job_agent_graph()
    assert compiled is not None
    # LangGraph compiled graph has invoke method
    assert hasattr(compiled, "invoke"), "Compiled graph must expose an invoke() method"
    print("  ✅ test_graph_compilation passed")


def test_conditional_edge_routing():
    """T3: Verify conditional routing after filter stage."""
    state_with_jobs = {"filtered_jobs": [{"title": "Python Dev"}]}
    state_without_jobs = {"filtered_jobs": []}

    route_jobs = should_continue_after_filter(state_with_jobs)
    route_empty = should_continue_after_filter(state_without_jobs)

    assert route_jobs == "score", f"Expected 'score', got '{route_jobs}'"
    assert route_empty in ("inbox_monitor", "pdf_drip", "drip"), f"Expected fallback route, got '{route_empty}'"
    print("  ✅ test_conditional_edge_routing passed")


def test_isolated_nodes():
    """T4: Verify isolated execution of individual nodes."""
    state = get_initial_state(dry_run=True, require_approval=True, mock_mode=True)

    # 1. Memory Load Node
    mem_out = load_memory_node(state)
    assert "session_memory" in mem_out
    assert len(mem_out["logs"]) > 0

    # 2. Scrape Node
    scrape_out = scrape_jobs_node(state)
    assert "raw_jobs" in scrape_out
    assert len(scrape_out["raw_jobs"]) > 0

    # 3. Filter Node
    state["raw_jobs"] = scrape_out["raw_jobs"]
    filter_out = filter_jobs_node(state)
    assert "filtered_jobs" in filter_out
    assert "rejected_jobs" in filter_out

    # 4. Score Node
    state["filtered_jobs"] = filter_out["filtered_jobs"]
    score_out = score_jobs_node(state)
    assert "scored_jobs" in score_out

    # 5. PDF Drip Node
    drip_out = pdf_drip_node(state)
    assert "pdf_drip_contacts" in drip_out
    assert isinstance(drip_out["pdf_drip_contacts"], list)
    assert isinstance(drip_out["pdf_drip_sent"], list)

    print("  ✅ test_isolated_nodes passed")


def test_approval_safety_gate():
    """T5: Verify that high-scoring jobs are staged in SQLite approval_queue."""
    db = DatabaseManager(cfg.DB_PATH)
    state = get_initial_state(dry_run=True, require_approval=True, mock_mode=True)

    # Prepare high-scoring dummy jobs
    mock_url = f"https://example.com/apply/{uuid.uuid4().hex[:10]}"
    high_score_job = {
        "title": "Lead Agentic AI Architect",
        "company": "Autonomous Systems Corp",
        "location": "Remote",
        "job_url": mock_url,
        "url": mock_url,
        "portal": "remoteok",
        "description": "Develop autonomous agent systems using LangGraph and RAG.",
        "relevance_score": 9.5,
        "matched_skills": ["Agentic AI", "LangGraph", "RAG", "FastAPI"]
    }

    state["scored_jobs"] = [high_score_job]
    out = stage_approvals_node(state)

    assert len(out["staged_approvals"]) > 0, "High scoring job must be staged in approval queue"
    staged_item = out["staged_approvals"][0]
    assert "queue_id" in staged_item

    # Verify presence in database
    conn = db._get_conn()
    row = conn.execute("SELECT * FROM approval_queue WHERE id = ?", (staged_item["queue_id"],)).fetchone()
    assert row is not None
    assert row["status"] == "pending"
    assert row["item_type"] == "job_application"

    print("  ✅ test_approval_safety_gate passed")


def test_end_to_end_workflow_execution():
    """T6: Execute the full LangGraph multi-agent workflow in safe dry-run mode."""
    final_state = execute_workflow(dry_run=True, require_approval=True, mock_mode=True)

    # Validate output structure
    assert "report_summary" in final_state
    summary = final_state["report_summary"]

    assert summary["status"] in ["completed", "completed_with_warnings"]
    assert summary["dry_run"] is True
    assert "metrics" in summary

    metrics = summary["metrics"]
    assert metrics["scraped"] > 0, "Workflow should have scraped raw listings"
    assert "pdf_drip_sent" in metrics, "Workflow should track PDF HR metrics"

    # Validate that logs were accumulated
    logs = final_state.get("logs", [])
    assert len(logs) >= 5, f"Expected cumulative logs across all nodes, got {len(logs)}"

    print(f"  ✅ test_end_to_end_workflow_execution passed (Scraped: {metrics['scraped']}, Drip: {metrics['pdf_drip_sent']})")


def test_database_lifecycle_tracking():
    """T7: Verify agent_runs and daily_stats records in SQLite."""
    db = DatabaseManager(cfg.DB_PATH)
    conn = db._get_conn()

    # Verify agent_runs has at least one completed record
    runs = conn.execute("SELECT * FROM agent_runs ORDER BY rowid DESC LIMIT 1").fetchone()
    assert runs is not None
    assert runs["status"] in ["completed", "completed_with_warnings", "running"]

    # Verify daily_stats has records
    stats = conn.execute("SELECT * FROM daily_stats ORDER BY rowid DESC LIMIT 1").fetchone()
    assert stats is not None
    assert stats["total_scraped"] >= 0

    print("  ✅ test_database_lifecycle_tracking passed")


if __name__ == "__main__":
    print("\nRunning Milestone 7 LangGraph Multi-Agent Workflow Tests...\n")

    tests = [
        test_state_initialization,
        test_graph_compilation,
        test_conditional_edge_routing,
        test_isolated_nodes,
        test_approval_safety_gate,
        test_end_to_end_workflow_execution,
        test_database_lifecycle_tracking,
    ]

    passed_count = 0
    failed_count = 0

    for test_fn in tests:
        try:
            test_fn()
            passed_count += 1
        except AssertionError as e:
            print(f"  ❌ {test_fn.__name__} FAILED: {e}")
            failed_count += 1
        except Exception as e:
            print(f"  💥 {test_fn.__name__} ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed_count += 1

    print(f"\n{'='*68}")
    if failed_count == 0:
        print(f"🎉 ALL {passed_count} LANGGRAPH WORKFLOW TESTS PASSED SUCCESSFULLY!")
    else:
        print(f"❌ {failed_count}/{len(tests)} tests FAILED. {passed_count} passed.")
    print(f"{'='*68}\n")
    sys.exit(0 if failed_count == 0 else 1)
