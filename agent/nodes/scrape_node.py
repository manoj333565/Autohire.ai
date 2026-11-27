"""
agent/nodes/scrape_node.py — Multi-Portal Job Scraper Node (F1 / F6)
====================================================================
Wraps ScraperEngine to discover and aggregate job listings across 6 portals:
RemoteOK, WeWorkRemotely, Internshala, Wellfound, LinkedIn, and Naukri.
Fault-tolerant: handles individual portal or engine exceptions gracefully
without crashing downstream graph nodes.
"""

import logging
from typing import Dict, Any
import config as cfg
from agent.state import AgentState
from scrapers.scraper_engine import ScraperEngine

logger = logging.getLogger("CareerPilot.Nodes.Scrape")


def scrape_jobs_node(state: AgentState) -> Dict[str, Any]:
    """
    Execute multi-portal job scraping.
    Robust per-portal error isolation: any scraper error logs a warning
    and allows other portals and downstream graph nodes to execute gracefully.
    """
    dry_run = state.get("dry_run", True)
    mock_mode = state.get("mock_mode")
    if mock_mode is None:
        mock_mode = getattr(cfg, "MOCK_MODE", False)

    logger.info(
        f"[ScrapeNode] Launching multi-portal scraping (Dry-Run: {dry_run}, Mock Mode: {mock_mode})..."
    )

    raw_jobs = []
    portal_stats = {}
    errors = []

    try:
        engine = ScraperEngine(mock_mode=mock_mode)
        # Target 5 jobs per portal for balanced execution
        results = engine.run_all(max_jobs_per_portal=5)
        raw_jobs = results.get("raw_jobs", [])
        portal_stats = results.get("portal_stats", {})
        errors = results.get("errors", [])
    except Exception as e:
        err_msg = f"ScraperEngine failure: {e}"
        logger.error(f"[ScrapeNode] ❌ {err_msg}")
        errors.append(err_msg)

    summary_log = f"Scraped {len(raw_jobs)} total listings across {len(portal_stats)} portals."
    logger.info(f"[ScrapeNode] {summary_log}")

    return {
        "raw_jobs": raw_jobs,
        "logs": [summary_log],
        "errors": errors,
        "stats": {
            **state.get("stats", {}),
            "total_scraped": len(raw_jobs),
            "portal_stats": portal_stats,
        },
    }
