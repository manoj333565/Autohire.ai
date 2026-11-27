"""
scrapers/base_scraper.py — Abstract Base Class for Job Scrapers in CareerPilot AI
================================================================================
Enforces:
- Standardized, consistent job schema adhering to Phase 3 requirements:
  (job_id, source, job_title, company_name, job_description, location, salary,
   experience_required, employment_type, skills_required, posted_date, job_url,
   application_url, scraped_at) + backward-compatible legacy aliases.
- Canonical URL sanitization (stripping tracking query parameters).
- Resilient HTTP requests with exponential backoff, jitter, and timeout isolation.
- Structured logging without exposing credentials.
- Human-like pacing and User-Agent rotation.
"""
import re
import time
import random
import hashlib
import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

import requests

logger = logging.getLogger("CareerPilot.Scrapers")

# Standardized Modern User Agents
DEFAULT_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
]

# Tracking query parameters to strip for clean canonicalization
TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "ref", "refId", "trackingId", "trk", "midToken", "trkInfo", "originalSubdomain",
    "position", "pageNum", "searchId", "clickId", "source", "f_TPR"
}


class BaseScraper(ABC):
    """Abstract Base Class for resilient, normalized job scraping."""

    def __init__(self, name: str, timeout_seconds: int = 25, mock_mode: bool = False):
        self.name = name.lower().strip()
        self.timeout = timeout_seconds
        self.mock_mode = mock_mode
        self.last_error: Optional[str] = None

    def get_random_headers(self) -> Dict[str, str]:
        """Return human-like HTTP request headers."""
        return {
            "User-Agent": random.choice(DEFAULT_USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/json,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "DNT": "1",
            "Upgrade-Insecure-Requests": "1",
        }

    def clean_text(self, text: Optional[str]) -> str:
        """Strip HTML tags and normalize whitespace."""
        if not text:
            return ""
        no_html = re.sub(r"<[^>]+>", " ", str(text))
        return " ".join(no_html.split()).strip()

    def canonicalize_url(self, raw_url: str) -> str:
        """
        Normalize and strip marketing/tracking parameters from job URLs
        to guarantee deterministic deduplication across runs and sources.
        """
        if not raw_url:
            return ""
        try:
            parsed = urlparse(raw_url.strip())
            # Filter tracking query parameters
            query_tuples = parse_qsl(parsed.query, keep_blank_values=False)
            filtered_query = [(k, v) for k, v in query_tuples if k not in TRACKING_PARAMS and not k.startswith("utm_")]
            # Reconstruct clean URL
            clean_parsed = parsed._replace(
                query=urlencode(filtered_query),
                fragment=""
            )
            return urlunparse(clean_parsed).rstrip("/")
        except Exception:
            return raw_url.strip().rstrip("/")

    def make_job_id(self, canonical_url: str, company: str, title: str) -> str:
        """Compute a deterministic 16-character hexadecimal hash identifier for a job."""
        if canonical_url:
            base_str = canonical_url.lower()
        else:
            norm_comp = re.sub(r"[^a-z0-9]", "", company.lower())
            norm_title = re.sub(r"[^a-z0-9]", "", title.lower())
            base_str = f"{norm_comp}::{norm_title}"
        return hashlib.sha256(base_str.encode("utf-8")).hexdigest()[:16]

    def request_with_retry(
        self,
        url: str,
        session: Optional[requests.Session] = None,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        backoff_factor: float = 1.5,
        timeout: Optional[int] = None,
        **kwargs
    ) -> requests.Response:
        """
        Execute an HTTP request with exponential backoff, randomized jitter, and rate limit awareness.
        Catches transient connection drops, timeouts, 429, and 503 errors.
        """
        req_headers = headers or self.get_random_headers()
        req_timeout = timeout or self.timeout
        client = session or requests

        last_exc = None
        delay = backoff_factor

        for attempt in range(1, max_retries + 1):
            try:
                resp = client.get(url, headers=req_headers, timeout=req_timeout, **kwargs)
                if resp.status_code in (429, 503, 502, 504) and attempt < max_retries:
                    jitter = random.uniform(0.5, 1.5)
                    sleep_time = delay + jitter
                    logger.warning(
                        f"[{self.name.upper()}] HTTP {resp.status_code} received on {url[:60]}... "
                        f"Retrying in {sleep_time:.1f}s (Attempt {attempt}/{max_retries})"
                    )
                    time.sleep(sleep_time)
                    delay *= 2.0
                    continue

                resp.raise_for_status()
                return resp

            except (requests.exceptions.RequestException, requests.exceptions.Timeout) as e:
                last_exc = e
                if attempt < max_retries:
                    jitter = random.uniform(0.5, 1.5)
                    sleep_time = delay + jitter
                    logger.debug(
                        f"[{self.name.upper()}] Network error on attempt {attempt}: {e}. "
                        f"Retrying in {sleep_time:.1f}s..."
                    )
                    time.sleep(sleep_time)
                    delay *= 2.0
                else:
                    logger.warning(f"[{self.name.upper()}] All {max_retries} request retries exhausted for {url[:60]}: {e}")
                    raise last_exc

        raise RuntimeError(f"Request retries exhausted: {last_exc}")

    def normalize_job(
        self,
        title: str,
        company: str,
        location: str,
        job_url: str,
        description: str,
        experience: Optional[str] = None,
        salary: Optional[str] = None,
        posted_date: Optional[str] = None,
        employment_type: Optional[str] = None,
        skills_required: Optional[List[str]] = None,
        application_url: Optional[str] = None,
        extra_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Produce a strict, standardized job dictionary adhering to Phase 3 requirements
        while preserving all legacy keys for complete backward compatibility.
        """
        clean_url = self.canonicalize_url(job_url)
        clean_title = self.clean_text(title) or "Untitled Job"
        clean_company = self.clean_text(company) or "Confidential"
        clean_desc = self.clean_text(description)[:4000]
        clean_loc = self.clean_text(location) or "Remote"
        clean_exp = self.clean_text(experience) if experience else None
        clean_salary = self.clean_text(salary) if salary else None
        clean_date = self.clean_text(posted_date) if posted_date else None
        clean_app_url = self.canonicalize_url(application_url) if application_url else clean_url

        # Employment type detection if not provided
        emp_type = self.clean_text(employment_type) if employment_type else None
        if not emp_type:
            lower_desc = f"{clean_title} {clean_desc}".lower()
            if "intern" in lower_desc or "internship" in lower_desc:
                emp_type = "Internship"
            elif "part-time" in lower_desc or "part time" in lower_desc:
                emp_type = "Part-time"
            elif "contract" in lower_desc or "freelance" in lower_desc:
                emp_type = "Contract"
            else:
                emp_type = "Full-time"

        # Unique deterministic job ID
        job_id = self.make_job_id(clean_url, clean_company, clean_title)

        # Scraped at timestamp (UTC ISO format)
        scraped_at = datetime.now(timezone.utc).isoformat()

        skills = skills_required or []
        if not skills:
            # Extract common candidate target keywords present in description
            target_keywords = [
                "Python", "SQL", "Machine Learning", "Deep Learning", "NLP",
                "FastAPI", "LangChain", "LangGraph", "Docker", "PyTorch",
                "PostgreSQL", "Pandas", "NumPy", "Scikit-learn", "OpenCV",
                "Power BI", "RAG", "Generative AI", "Streamlit"
            ]
            skills = [k for k in target_keywords if re.search(r"\b" + re.escape(k) + r"\b", clean_desc, re.I)]

        return {
            # ── Standardized Phase 3 Fields ──
            "job_id": job_id,
            "source": self.name,
            "job_title": clean_title,
            "company_name": clean_company,
            "job_description": clean_desc,
            "location": clean_loc,
            "salary": clean_salary,
            "experience_required": clean_exp or "0-2 years",
            "employment_type": emp_type,
            "skills_required": skills,
            "posted_date": clean_date,
            "job_url": clean_url,
            "application_url": clean_app_url,
            "scraped_at": scraped_at,

            # ── Legacy Aliases for 100% Backward Compatibility ──
            "title": clean_title,
            "company": clean_company,
            "description": clean_desc,
            "url": clean_url,
            "portal": self.name,
            "experience": clean_exp or "Not specified",
            "extra_metadata": extra_metadata or {}
        }

    @abstractmethod
    def scrape(self, max_jobs: int = 30) -> List[Dict[str, Any]]:
        """Scrape jobs from the portal and return a list of normalized job dicts."""
        pass

    @abstractmethod
    def get_mock_jobs(self, count: int = 5) -> List[Dict[str, Any]]:
        """Return offline mock jobs for isolated testing or network failure fallback."""
        pass

    def run_safe(self, max_jobs: int = 30) -> List[Dict[str, Any]]:
        """
        Safely execute scraper with timeout, exception isolation, and mock mode separation.
        - When mock_mode=True: returns deterministic mock fixtures.
        - When mock_mode=False: executes live scraping. On error, records the error
          and returns an empty list (NEVER returns mock or fixture jobs on live failure).
        Ensures a single portal crash never breaks the agent workflow.
        """
        self.last_error = None
        if self.mock_mode:
            logger.info(f"[{self.name.upper()}] Running in offline/mock mode.")
            return self.get_mock_jobs(count=min(max_jobs, 10))

        start_time = time.time()
        try:
            logger.info(f"[{self.name.upper()}] Starting scrape (timeout: {self.timeout}s)...")
            jobs = self.scrape(max_jobs=max_jobs)
            elapsed = time.time() - start_time
            logger.info(f"[{self.name.upper()}] Successfully scraped {len(jobs)} jobs in {elapsed:.1f}s.")
            return jobs
        except Exception as e:
            elapsed = time.time() - start_time
            self.last_error = str(e)
            logger.warning(
                f"[{self.name.upper()}] Scrape encountered error after {elapsed:.1f}s: {e}. "
                f"Returning 0 jobs (zero mock/fixture data returned in live mode)."
            )
            return []
