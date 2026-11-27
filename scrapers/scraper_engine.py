"""
scrapers/scraper_engine.py — Multi-Portal Orchestration Engine for CareerPilot AI
================================================================================
Coordinates execution across all 6 portals (RemoteOK, WeWorkRemotely, Internshala,
Wellfound, LinkedIn, Naukri) with independent failure isolation, performance metrics,
and multi-level deduplication (URL + cross-portal company/title matching).
"""
import re
import time
import logging
from typing import List, Dict, Any, Optional, Set

from scrapers.base_scraper import BaseScraper
from scrapers.remoteok_scraper import RemoteOKScraper
from scrapers.weworkremotely_scraper import WeWorkRemotelyScraper
from scrapers.internshala_scraper import InternshalaScraper
from scrapers.wellfound_scraper import WellfoundScraper
from scrapers.linkedin_scraper import LinkedInScraper
from scrapers.naukri_scraper import NaukriScraper
from scrapers.ats_scraper import AtsScraper

logger = logging.getLogger("CareerPilot.ScraperEngine")

PORTAL_SOURCE_TYPES = {
    "remoteok": "api",
    "weworkremotely": "rss",
    "internshala": "html",
    "wellfound": "browser",
    "linkedin": "browser",
    "naukri": "browser",
    "ats": "api",
}


COMPANY_SUFFIXES = re.compile(r"\b(inc|llc|ltd|corp|corporation|technologies|technology|solutions|pvt|private|limited)\b", re.IGNORECASE)


def normalize_string_key(s: Optional[str]) -> str:
    """Normalize text by stripping common business suffixes and non-alphanumeric characters."""
    if not s:
        return ""
    clean = COMPANY_SUFFIXES.sub("", s.lower())
    return re.sub(r"[^a-z0-9]", "", clean)


class ScraperEngine:
    """Master multi-portal scraping and deduplication engine."""

    def __init__(self, mock_mode: bool = False):
        self.mock_mode = mock_mode
        self.scrapers: Dict[str, BaseScraper] = {
            "remoteok": RemoteOKScraper(mock_mode=mock_mode),
            "weworkremotely": WeWorkRemotelyScraper(mock_mode=mock_mode),
            "internshala": InternshalaScraper(mock_mode=mock_mode),
            "wellfound": WellfoundScraper(mock_mode=mock_mode),
            "linkedin": LinkedInScraper(mock_mode=mock_mode),
            "naukri": NaukriScraper(mock_mode=mock_mode),
            "ats": AtsScraper(mock_mode=mock_mode),
        }

    def deduplicate_jobs(
        self,
        jobs: List[Dict[str, Any]],
        existing_urls: Optional[Set[str]] = None,
        existing_keys: Optional[Set[str]] = None
    ) -> Dict[str, Any]:
        """
        Multi-level deduplication covering:
        1. Exact canonical URL match
        2. Normalized (company, title) composite key match across portals/pages
        Preserves original and alternate source portal metadata.
        """
        seen_urls: Set[str] = set(existing_urls or [])
        seen_content_keys: Dict[str, Dict[str, Any]] = {}
        if existing_keys:
            for k in existing_keys:
                seen_content_keys[k] = None

        unique_jobs: List[Dict[str, Any]] = []
        duplicates_count = 0
        cross_portal_count = 0

        for job in jobs:
            raw_url = (job.get("job_url") or job.get("url") or "").strip()
            # Clean tracking query parameters
            clean_url = raw_url.split("?")[0].rstrip("/") if raw_url else ""
            company = job.get("company_name") or job.get("company") or ""
            title = job.get("job_title") or job.get("title") or ""
            source = job.get("source") or job.get("portal") or "direct"

            # 1. URL Deduplication
            if clean_url and (clean_url in seen_urls or raw_url in seen_urls):
                duplicates_count += 1
                logger.debug(f"[Deduplication] Dropped duplicate URL: {clean_url}")
                continue

            # 2. Cross-portal Normalized Content Deduplication (Company + Title)
            norm_comp = normalize_string_key(company)
            norm_title = normalize_string_key(title)
            content_key = f"{norm_comp}::{norm_title}"

            if norm_comp and norm_title and content_key in seen_content_keys:
                # Existing job found on another portal
                existing_entry = seen_content_keys[content_key]
                if existing_entry is not None:
                    # Update existing record to record cross-portal presence
                    alt_sources = existing_entry.setdefault("cross_portal_sources", [])
                    if source not in alt_sources:
                        alt_sources.append(source)
                cross_portal_count += 1
                duplicates_count += 1
                logger.debug(f"[Deduplication] Cross-portal duplicate detected: '{title}' at '{company}' from {source}")
                continue

            # Register as unique
            if clean_url:
                seen_urls.add(clean_url)
            if raw_url:
                seen_urls.add(raw_url)
            if norm_comp and norm_title:
                seen_content_keys[content_key] = job

            job["cross_portal_sources"] = [source]
            unique_jobs.append(job)

        return {
            "unique_jobs": unique_jobs,
            "total_input": len(jobs),
            "duplicates_filtered": duplicates_count,
            "cross_portal_duplicates": cross_portal_count,
        }

    def run_all(
        self,
        max_jobs_per_portal: int = 25,
        portals_to_run: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Execute enabled scrapers sequentially with isolated error boundaries.
        Returns a dictionary containing:
        - raw_jobs: Unified list of normalized and deduplicated job dictionaries
        - portal_stats: Detailed breakdown of jobs found, elapsed time, status, and source type
        - total_scraped: Count of raw jobs returned from scrapers
        - total_unique: Count of distinct jobs after multi-level deduplication
        - duplicates_filtered: Count of duplicate listings suppressed
        - errors: List of error messages encountered
        """
        targets = [p.lower() for p in (portals_to_run or self.scrapers.keys()) if p.lower() in self.scrapers]
        logger.info(f"ScraperEngine: Executing across {len(targets)} portals: {', '.join(targets)}")

        all_collected_jobs: List[Dict[str, Any]] = []
        portal_stats = {}
        errors = []

        for portal_name in targets:
            scraper = self.scrapers[portal_name]
            src_type = PORTAL_SOURCE_TYPES.get(portal_name, "html")
            start_time = time.time()
            try:
                logger.info(f"--> Initiating [{portal_name.upper()}] ({src_type.upper()}) scraper...")
                portal_jobs = scraper.run_safe(max_jobs=max_jobs_per_portal)
                elapsed = round(time.time() - start_time, 2)

                all_collected_jobs.extend(portal_jobs)
                
                # Check whether scraper recorded an unhandled internal failure
                scraper_err = getattr(scraper, "last_error", None)
                if scraper_err:
                    status_label = "failed"
                    err_msg = f"{portal_name.upper()} scraper failed: {scraper_err}"
                    errors.append(err_msg)
                    portal_stats[portal_name] = {
                        "status": "failed",
                        "jobs_found": 0,
                        "elapsed_seconds": elapsed,
                        "source_type": src_type,
                        "error": scraper_err
                    }
                    logger.warning(f"[{portal_name.upper()}] Result: failed ({scraper_err})")
                else:
                    status_label = "success"
                    if len(portal_jobs) == 0:
                        status_label = "PROTECTED / UNAVAILABLE" if portal_name in ("wellfound", "naukri", "linkedin") else "empty"

                    portal_stats[portal_name] = {
                        "status": status_label,
                        "jobs_found": len(portal_jobs),
                        "elapsed_seconds": elapsed,
                        "source_type": src_type,
                        "error": None
                    }
                    logger.info(f"[{portal_name.upper()}] Result: {status_label} ({len(portal_jobs)} raw jobs, {elapsed}s)")

            except Exception as e:
                elapsed = round(time.time() - start_time, 2)
                err_msg = f"{portal_name.upper()} scraper failed: {e}"
                logger.error(f"❌ {err_msg}")
                errors.append(err_msg)
                portal_stats[portal_name] = {
                    "status": "failed",
                    "jobs_found": 0,
                    "elapsed_seconds": elapsed,
                    "source_type": src_type,
                    "error": str(e)
                }

        # Multi-level deduplication across all portals
        dedup_res = self.deduplicate_jobs(all_collected_jobs)
        unique_jobs = dedup_res["unique_jobs"]

        logger.info(
            f"ScraperEngine Summary: {len(all_collected_jobs)} scraped -> "
            f"{len(unique_jobs)} unique ({dedup_res['duplicates_filtered']} duplicates filtered)."
        )

        return {
            "raw_jobs": unique_jobs,
            "portal_stats": portal_stats,
            "total_scraped": len(all_collected_jobs),
            "total_unique": len(unique_jobs),
            "duplicates_filtered": dedup_res["duplicates_filtered"],
            "cross_portal_duplicates": dedup_res["cross_portal_duplicates"],
            "errors": errors
        }
