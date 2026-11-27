"""
scrapers/ats_scraper.py — Public ATS & Direct Company Career Feed Scraper
=========================================================================
Ingests authorized public career feeds from enterprise Applicant Tracking Systems (ATS):
- Greenhouse (https://boards-api.greenhouse.io/v1/boards/{company}/jobs)
- Lever (https://api.lever.co/v0/postings/{company}?mode=json)
- Ashby (https://api.ashbyhq.com/posting-api/job-board/{company})

Features:
- Standardized 14-field schema adhering to Phase 3 & 4 architecture.
- Deterministic canonical URL deduplication and 16-hex job_id hashing.
- Resilient error handling and respectful exponential backoff.
- Configurable company board lists with safe tech defaults.
- Mock mode fallback for offline deterministic unit testing.
"""

import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional

from scrapers.base_scraper import BaseScraper
import config as cfg

logger = logging.getLogger("CareerPilot.AtsScraper")

# Default public company career boards with active technical openings
DEFAULT_ATS_TARGETS = {
    "greenhouse": ["cloudflare", "figma", "gitlab"],
    "lever": ["palantir"],
    "ashby": ["linear", "ramp"],
}


class AtsScraper(BaseScraper):
    """Scraper for public, official company ATS job boards."""

    def __init__(
        self,
        timeout_seconds: int = 25,
        mock_mode: bool = False,
        targets: Optional[Dict[str, List[str]]] = None
    ):
        super().__init__(name="ats", timeout_seconds=timeout_seconds, mock_mode=mock_mode)
        self.targets = targets or DEFAULT_ATS_TARGETS

    def scrape(self, max_jobs: int = 30) -> List[Dict[str, Any]]:
        """Fetch real public job postings across Greenhouse, Lever, and Ashby."""
        if self.mock_mode:
            return self.get_mock_jobs(count=max_jobs)

        jobs: List[Dict[str, Any]] = []
        collected_hashes = set()

        # 1. Scrape Greenhouse Boards
        for company in self.targets.get("greenhouse", []):
            if len(jobs) >= max_jobs:
                break
            try:
                gh_jobs = self._scrape_greenhouse(company, max_per_board=max_jobs - len(jobs))
                for j in gh_jobs:
                    if j["job_id"] not in collected_hashes:
                        collected_hashes.add(j["job_id"])
                        jobs.append(j)
            except Exception as e:
                logger.warning(f"[ATS:GREENHOUSE] Failed board '{company}': {e}")

        # 2. Scrape Lever Boards
        for company in self.targets.get("lever", []):
            if len(jobs) >= max_jobs:
                break
            try:
                lever_jobs = self._scrape_lever(company, max_per_board=max_jobs - len(jobs))
                for j in lever_jobs:
                    if j["job_id"] not in collected_hashes:
                        collected_hashes.add(j["job_id"])
                        jobs.append(j)
            except Exception as e:
                logger.warning(f"[ATS:LEVER] Failed board '{company}': {e}")

        # 3. Scrape Ashby Boards
        for company in self.targets.get("ashby", []):
            if len(jobs) >= max_jobs:
                break
            try:
                ashby_jobs = self._scrape_ashby(company, max_per_board=max_jobs - len(jobs))
                for j in ashby_jobs:
                    if j["job_id"] not in collected_hashes:
                        collected_hashes.add(j["job_id"])
                        jobs.append(j)
            except Exception as e:
                logger.warning(f"[ATS:ASHBY] Failed board '{company}': {e}")

        logger.info(f"[ATS] Ingestion completed. Retrieved {len(jobs)} real listings from public feeds.")
        return jobs[:max_jobs]

    def _scrape_greenhouse(self, company: str, max_per_board: int = 10) -> List[Dict[str, Any]]:
        """Fetch jobs from Greenhouse public board API."""
        url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
        resp = self.request_with_retry(url, headers={"Accept": "application/json"})
        data = resp.json()
        raw_jobs = data.get("jobs", [])
        
        extracted = []
        for rj in raw_jobs:
            if len(extracted) >= max_per_board:
                break
            title = rj.get("title", "").strip()
            if not title:
                continue
            
            raw_url = rj.get("absolute_url", "")
            canonical_url = self.canonicalize_url(raw_url)
            loc_data = rj.get("location", {})
            location = loc_data.get("name", "Remote") if isinstance(loc_data, dict) else str(loc_data)
            description = self.clean_text(rj.get("content", "")) or f"{title} at {company.title()} via Greenhouse."
            
            # Format company name cleanly
            company_display = company.capitalize()
            
            job = self.normalize_job(
                title=title,
                company=company_display,
                location=location,
                job_url=canonical_url,
                description=description,
                posted_date=rj.get("updated_at", datetime.now(timezone.utc).isoformat()),
                employment_type="Full-time",
                application_url=canonical_url
            )
            extracted.append(job)
        return extracted

    def _scrape_lever(self, company: str, max_per_board: int = 10) -> List[Dict[str, Any]]:
        """Fetch jobs from Lever public postings API."""
        url = f"https://api.lever.co/v0/postings/{company}?mode=json"
        resp = self.request_with_retry(url, headers={"Accept": "application/json"})
        raw_jobs = resp.json()
        if not isinstance(raw_jobs, list):
            return []

        extracted = []
        for rj in raw_jobs:
            if len(extracted) >= max_per_board:
                break
            title = rj.get("text", "").strip()
            if not title:
                continue

            raw_url = rj.get("hostedUrl", "")
            canonical_url = self.canonicalize_url(raw_url)
            categories = rj.get("categories", {}) or {}
            location = categories.get("location", "Remote")
            commitment = categories.get("commitment", "Full-time")
            desc = self.clean_text(rj.get("descriptionPlain", "")) or f"{title} at {company.title()} via Lever."

            job = self.normalize_job(
                title=title,
                company=company.capitalize(),
                location=location,
                job_url=canonical_url,
                description=desc,
                posted_date=datetime.fromtimestamp(rj.get("createdAt", 0) / 1000.0, timezone.utc).isoformat() if rj.get("createdAt") else "Recent",
                employment_type=commitment,
                application_url=rj.get("applyUrl") or canonical_url
            )
            extracted.append(job)
        return extracted

    def _scrape_ashby(self, company: str, max_per_board: int = 10) -> List[Dict[str, Any]]:
        """Fetch jobs from Ashby public job board API."""
        url = f"https://api.ashbyhq.com/posting-api/job-board/{company}"
        resp = self.request_with_retry(url, headers={"Accept": "application/json"})
        data = resp.json()
        raw_jobs = data.get("jobs", [])

        extracted = []
        for rj in raw_jobs:
            if len(extracted) >= max_per_board:
                break
            title = rj.get("title", "").strip()
            if not title:
                continue

            raw_url = rj.get("jobUrl", "")
            canonical_url = self.canonicalize_url(raw_url)
            location = rj.get("location", "Remote")
            employment_type = rj.get("employmentType", "Full-time")

            job = self.normalize_job(
                title=title,
                company=company.capitalize(),
                location=location,
                job_url=canonical_url,
                description=f"{title} at {company.capitalize()} via Ashby job board.",
                posted_date=rj.get("publishedAt", "Recent"),
                employment_type=employment_type,
                application_url=canonical_url
            )
            extracted.append(job)
        return extracted

    def get_mock_jobs(self, count: int = 5) -> List[Dict[str, Any]]:
        """Deterministic mock data for offline testing."""
        mock_data = [
            {
                "title": "Software Engineer, AI & Data Platform",
                "company": "Cloudflare",
                "location": "Remote / India",
                "job_url": "https://boards.greenhouse.io/cloudflare/jobs/mock-gh-101",
                "description": "Develop high-throughput edge AI inference platforms and Python data pipelines.",
                "experience": "0-2 years",
                "salary": "$110k - $140k",
                "posted_date": "Recently",
                "employment_type": "Full-time",
                "application_url": "https://boards.greenhouse.io/cloudflare/jobs/mock-gh-101"
            },
            {
                "title": "Forward Deployed Software Engineer",
                "company": "Palantir",
                "location": "Remote / Hybrid",
                "job_url": "https://jobs.lever.co/palantir/mock-lever-202",
                "description": "Deploy enterprise machine learning, LLM systems, and analytics applications.",
                "experience": "1-3 years",
                "salary": "$120k - $160k",
                "posted_date": "Recently",
                "employment_type": "Full-time",
                "application_url": "https://jobs.lever.co/palantir/mock-lever-202"
            },
            {
                "title": "Fullstack AI Engineer",
                "company": "Linear",
                "location": "Remote",
                "job_url": "https://jobs.ashbyhq.com/linear/mock-ashby-303",
                "description": "Build high-performance collaboration features, agentic workflows, and fast interfaces.",
                "experience": "0-2 years",
                "salary": "$125k - $155k",
                "posted_date": "Recently",
                "employment_type": "Full-time",
                "application_url": "https://jobs.ashbyhq.com/linear/mock-ashby-303"
            }
        ]

        normalized = []
        for i, raw in enumerate(mock_data[:count]):
            norm = self.normalize_job(
                title=raw["title"],
                company=raw["company"],
                location=raw["location"],
                job_url=raw["job_url"],
                description=raw["description"],
                experience=raw["experience"],
                salary=raw["salary"],
                posted_date=raw["posted_date"],
                employment_type=raw["employment_type"],
                application_url=raw["application_url"]
            )
            normalized.append(norm)
        return normalized
