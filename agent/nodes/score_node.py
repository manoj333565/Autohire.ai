"""
agent/nodes/score_node.py — LLM Job Scoring Node (F3)
=====================================================
Scores filtered opportunities using Gemini Flash with structured schema,
weighted candidate skill tiers (Proficient=1.0, Intermediate=0.7, Learning=0.3),
and persists score records in SQLite.
"""

import logging
from typing import Dict, Any
from agent.state import AgentState
import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService
from services.scoring_service import ScoringService

logger = logging.getLogger("CareerPilot.Nodes.Score")


def score_jobs_node(state: AgentState) -> Dict[str, Any]:
    """
    Score all filtered jobs and rank them descending by relevance score.
    """
    filtered_jobs = state.get("filtered_jobs", [])
    if not filtered_jobs:
        logger.info("[ScoreNode] No filtered jobs to score.")
        return {
            "scored_jobs": [],
            "logs": ["No new jobs passed filtering; scoring skipped."]
        }

    logger.info(f"[ScoreNode] Scoring {len(filtered_jobs)} filtered opportunities...")

    db = DatabaseManager(cfg.DB_PATH)
    profile_service = ProfileService(db)
    dry_run = state.get("dry_run", True)
    mock_mode = state.get("mock_mode")
    if mock_mode is None:
        mock_mode = getattr(cfg, "MOCK_MODE", False) or not bool(cfg.GEMINI_API_KEY)

    scoring_service = ScoringService(
        db_manager=db,
        profile_service=profile_service,
        api_key=cfg.GEMINI_API_KEY,
        model_name=cfg.GEMINI_MODEL,
        mock_mode=mock_mode
    )

    scored_jobs = scoring_service.score_jobs(filtered_jobs, persist_to_db=True)

    top_score = scored_jobs[0]["relevance_score"] if scored_jobs else 0.0
    avg_score = round(sum(j["relevance_score"] for j in scored_jobs) / len(scored_jobs), 2) if scored_jobs else 0.0

    valid_embs = [j.get("embedding_score") for j in scored_jobs if j.get("embedding_score") is not None]
    avg_embedding = round(sum(valid_embs) / len(valid_embs), 2) if valid_embs else None
    avg_llm = round(sum(j.get("llm_score", j["relevance_score"]) for j in scored_jobs) / len(scored_jobs), 2) if scored_jobs else 0.0

    emb_msg = f", Avg Emb: {avg_embedding}/10" if avg_embedding is not None else ""
    log_entry = (
        f"Scored {len(scored_jobs)} jobs. Top match: {top_score}/10, Average: {avg_score}/10 "
        f"(Avg LLM: {avg_llm}/10{emb_msg})."
    )
    logger.info(f"[ScoreNode] {log_entry}")

    return {
        "scored_jobs": scored_jobs,
        "logs": [log_entry],
        "stats": {
            **state.get("stats", {}),
            "scored_count": len(scored_jobs),
            "top_score": top_score,
            "average_score": avg_score,
            "avg_embedding_score": avg_embedding,
            "avg_llm_score": avg_llm
        }
    }
