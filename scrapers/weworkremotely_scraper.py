"""
scrapers/weworkremotely_scraper.py — WeWorkRemotely RSS/XML Job Scraper
======================================================================
Parses WeWorkRemotely public RSS feeds with XML and HTML fallback parsers,
exponential backoff, and multi-category support.
"""
import re
import random
import logging
from typing import List, Dict, Any
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger("CareerPilot.Scrapers.WeWorkRemotely")

RSS_FEEDS = [
    "https://weworkremotely.com/categories/remote-programming-jobs.rss",
    "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss"
]


class WeWorkRemotelyScraper(BaseScraper):
    """Scraper for WeWorkRemotely public programming and DevOps feeds."""

    def __init__(self, timeout_seconds: int = 20, mock_mode: bool = False):
        super().__init__(name="weworkremotely", timeout_seconds=timeout_seconds, mock_mode=mock_mode)

    def scrape(self, max_jobs: int = 30) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return self.get_mock_jobs(count=max_jobs)
        headers = self.get_random_headers()
        normalized_jobs = []
        seen_urls = set()

        for feed_url in RSS_FEEDS:
            if len(normalized_jobs) >= max_jobs:
                break

            try:
                resp = self.request_with_retry(
                    feed_url,
                    headers=headers,
                    timeout=self.timeout,
                    max_retries=3,
                    backoff_factor=1.5
                )

                # Robust XML parsing with HTML fallback
                try:
                    soup = BeautifulSoup(resp.content, "xml")
                    items = soup.find_all("item")
                except Exception:
                    soup = BeautifulSoup(resp.content, "html.parser")
                    items = soup.find_all("item")

                for item in items:
                    title_elem = item.find("title")
                    title_text = title_elem.text if title_elem else ""

                    company = "WeWorkRemotely Employer"
                    title = title_text
                    if ":" in title_text:
                        parts = title_text.split(":", 1)
                        company = parts[0].strip()
                        title = parts[1].strip()

                    link_elem = item.find("link")
                    raw_url = link_elem.text if link_elem else ""
                    if not raw_url:
                        # Fallback for some RSS flavors where link is in guid or text
                        guid_elem = item.find("guid")
                        if guid_elem and "http" in guid_elem.text:
                            raw_url = guid_elem.text

                    desc_elem = item.find("description")
                    desc = desc_elem.text if desc_elem else ""

                    pub_elem = item.find("pubDate")
                    pub_date = pub_elem.text if pub_elem else None

                    canonical = self.canonicalize_url(raw_url)
                    if not canonical or canonical in seen_urls or not title:
                        continue
                    seen_urls.add(canonical)

                    # Extract basic salary if present in description
                    salary = None
                    sal_match = re.search(r"(\$\d{1,3}(?:,\d{3})*(?:\s*-\s*\$\d{1,3}(?:,\d{3})*)?)", desc)
                    if sal_match:
                        salary = sal_match.group(1)

                    job = self.normalize_job(
                        title=title,
                        company=company,
                        location="Remote",
                        job_url=canonical,
                        description=desc,
                        experience="0-2 years (Entry / Mid Level)",
                        salary=salary,
                        posted_date=pub_date,
                        employment_type="Full-time",
                        application_url=canonical,
                        extra_metadata={"feed": feed_url, "is_remote": True}
                    )
                    normalized_jobs.append(job)
                    if len(normalized_jobs) >= max_jobs:
                        break

            except Exception as e:
                logger.warning(f"[WEWORKREMOTELY] Feed error on {feed_url}: {e}")

        return normalized_jobs

    def get_mock_jobs(self, count: int = 5) -> List[Dict[str, Any]]:
        mock_data = [
            {
                "title": "Backend Python Developer (FastAPI / LangChain)",
                "company": "Kinetik Systems",
                "location": "Remote",
                "job_url": "https://weworkremotely.com/jobs/mock-wwr-python-201",
                "description": "Seeking Python backend developer skilled in API design, asynchronous processing, and LLM integrations.",
                "experience": "0-2 years",
                "salary": "$75,000",
                "posted_date": "2026-11-16",
                "employment_type": "Full-time",
                "skills_required": ["Python", "FastAPI", "LangChain", "SQL"],
                "application_url": "https://weworkremotely.com/jobs/mock-wwr-python-201"
            },
            {
                "title": "AI/ML Engineer - NLP & Conversational AI",
                "company": "OmniVoice AI",
                "location": "Remote",
                "job_url": "https://weworkremotely.com/jobs/mock-wwr-nlp-202",
                "description": "Work on state-of-the-art NLP models, embedding models, and RAG pipelines for real-time customer assistants.",
                "experience": "0-2 years",
                "salary": "$85,000",
                "posted_date": "2026-11-17",
                "employment_type": "Full-time",
                "skills_required": ["NLP", "Machine Learning", "Python", "RAG"],
                "application_url": "https://weworkremotely.com/jobs/mock-wwr-nlp-202"
            },
            {
                "title": "Data Analyst / Python Data Specialist",
                "company": "MetricStream Global",
                "location": "Remote",
                "job_url": "https://weworkremotely.com/jobs/mock-wwr-data-203",
                "description": "Extract data insights using PostgreSQL, Python (Pandas/NumPy), and build automated reporting dashboards.",
                "experience": "0-1 year",
                "salary": "$55,000",
                "posted_date": "2026-11-17",
                "employment_type": "Full-time",
                "skills_required": ["Data Analysis", "Python", "Pandas", "PostgreSQL", "Power BI"],
                "application_url": "https://weworkremotely.com/jobs/mock-wwr-data-203"
            }
        ]
        return [self.normalize_job(**item) for item in mock_data[:count]]
