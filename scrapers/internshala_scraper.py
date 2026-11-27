"""
scrapers/internshala_scraper.py — Internshala Jobs & Internships Scraper
========================================================================
Extracts fresher roles and internships across Indian cities and remote with
pagination support, robust CSS selectors, and exponential backoff retry.
"""
import time
import random
import logging
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger("CareerPilot.Scrapers.Internshala")

BASE_URL = "https://internshala.com"

CATEGORY_PATHS = [
    "/internships/python-internship",
    "/internships/machine-learning-internship",
    "/internships/artificial-intelligence-internship",
    "/jobs/python-jobs",
    "/jobs/fresher-jobs",
    "/jobs/ai-ml-jobs",
]


class InternshalaScraper(BaseScraper):
    """Scraper for Internshala fresher roles and internships."""

    def __init__(
        self,
        timeout_seconds: int = 25,
        mock_mode: bool = False,
        max_pages_per_category: int = 1,
        categories: Optional[List[str]] = None
    ):
        super().__init__(name="internshala", timeout_seconds=timeout_seconds, mock_mode=mock_mode)
        self.max_pages_per_category = max(1, max_pages_per_category)
        self.categories = categories or CATEGORY_PATHS

    def scrape(self, max_jobs: int = 30) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return self.get_mock_jobs(count=max_jobs)
        normalized_jobs = []
        seen_urls = set()
        consecutive_network_failures = 0

        for path in self.categories:
            if len(normalized_jobs) >= max_jobs:
                break

            # Short-circuit early if host is persistently unreachable (e.g. offline environment)
            if consecutive_network_failures >= 2:
                logger.warning("[INTERNSHALA] Multiple consecutive connection failures. Aborting further category crawls.")
                break

            # Configurable page depth (defaults to 1 page per category, expandable to 2+)
            pages_to_crawl = [path]
            for page_num in range(2, self.max_pages_per_category + 1):
                pages_to_crawl.append(f"{path}/page-{page_num}")

            for page_path in pages_to_crawl:
                if len(normalized_jobs) >= max_jobs:
                    break

                target_url = urljoin(BASE_URL, page_path)
                headers = self.get_random_headers()
                # Respectful human-like pacing
                time.sleep(random.uniform(0.8, 1.6))

                try:
                    resp = self.request_with_retry(
                        target_url,
                        headers=headers,
                        timeout=self.timeout,
                        max_retries=2,
                        backoff_factor=1.5
                    )
                    consecutive_network_failures = 0
                    if resp.status_code != 200:
                        continue

                    soup = BeautifulSoup(resp.text, "lxml")
                    # Multi-flavor selector match for Internshala listing cards
                    cards = soup.find_all(
                        "div",
                        class_=lambda c: c and any(k in c for k in ["internship_meta", "individual_internship", "container-fluid"])
                    )

                    for card in cards:
                        title_elem = (
                            card.find("h3", class_="job-internship-name") or
                            card.find("a", class_="job-title-href") or
                            card.find("div", class_="heading_4_5") or
                            card.find("h3")
                        )
                        if not title_elem:
                            continue
                        title = title_elem.get_text(strip=True)

                        company_elem = (
                            card.find("p", class_="company-name") or
                            card.find("a", class_="link_display_like_text") or
                            card.find("div", class_="heading_6")
                        )
                        company = company_elem.get_text(strip=True) if company_elem else "Internshala Employer"

                        loc_elem = (
                            card.find("span", class_="location_link") or
                            card.find("a", class_="location_link") or
                            card.find("div", id="location_names")
                        )
                        location = loc_elem.get_text(strip=True) if loc_elem else "India / Remote"

                        link_elem = (
                            card.find("a", class_="view_detail_button") or
                            card.find("a", class_="job-title-href") or
                            card.find("a", href=lambda h: h and ("/internship/detail/" in h or "/job/detail/" in h)) or
                            card.find("a", href=True)
                        )
                        raw_href = link_elem["href"] if link_elem and "href" in link_elem.attrs else ""
                        full_url = urljoin(BASE_URL, raw_href) if raw_href else ""
                        canonical = self.canonicalize_url(full_url)

                        if not canonical or canonical in seen_urls:
                            continue
                        seen_urls.add(canonical)

                        # Extract stipend/salary
                        salary_elem = card.find("span", class_="stipend") or card.find("span", class_="salary")
                        salary = salary_elem.get_text(strip=True) if salary_elem else None

                        is_internship = "/internship" in page_path or "/internship" in canonical
                        emp_type = "Internship" if is_internship else "Full-time"

                        job = self.normalize_job(
                            title=title,
                            company=company,
                            location=location,
                            job_url=canonical,
                            description=f"{title} position at {company} in {location}. Verified on Internshala portal.",
                            experience="0-1 year (Fresher / Intern)",
                            salary=salary,
                            posted_date="Recently",
                            employment_type=emp_type,
                            application_url=canonical,
                            extra_metadata={"is_internship": is_internship, "category_path": page_path}
                        )
                        normalized_jobs.append(job)
                        if len(normalized_jobs) >= max_jobs:
                            break

                except Exception as e:
                    consecutive_network_failures += 1
                    logger.debug(f"[INTERNSHALA] Path {page_path} request error: {e}")

        return normalized_jobs

    def get_mock_jobs(self, count: int = 5) -> List[Dict[str, Any]]:
        mock_data = [
            {
                "title": "Python Developer Intern",
                "company": "Zenith Infotech",
                "location": "Remote / Bengaluru, India",
                "job_url": "https://internshala.com/internship/detail/mock-python-intern-301",
                "description": "Work on Python backend development, PostgreSQL database management, and REST APIs. Suitable for CSE 2026 students.",
                "experience": "0-1 year (Fresher / Intern)",
                "salary": "₹25,000 - ₹35,000 /month",
                "posted_date": "1 day ago",
                "employment_type": "Internship",
                "skills_required": ["Python", "PostgreSQL", "REST APIs"],
                "application_url": "https://internshala.com/internship/detail/mock-python-intern-301"
            },
            {
                "title": "Artificial Intelligence & RAG Intern",
                "company": "NeuralMind Systems",
                "location": "Remote, India",
                "job_url": "https://internshala.com/internship/detail/mock-ai-intern-302",
                "description": "Build document question-answering systems using LangChain, LangGraph, and vector databases.",
                "experience": "0-1 year",
                "salary": "₹30,000 - ₹45,000 /month",
                "posted_date": "Today",
                "employment_type": "Internship",
                "skills_required": ["LangChain", "LangGraph", "Vector Databases", "Python", "RAG"],
                "application_url": "https://internshala.com/internship/detail/mock-ai-intern-302"
            },
            {
                "title": "Data Science & Machine Learning Intern",
                "company": "AlphaMetrics Labs",
                "location": "Hybrid / Pune, India",
                "job_url": "https://internshala.com/internship/detail/mock-ds-intern-303",
                "description": "Data wrangling, SQL query optimization, feature engineering, and predictive modeling using Scikit-Learn.",
                "experience": "0-1 year",
                "salary": "₹20,000 - ₹30,000 /month",
                "posted_date": "2 days ago",
                "employment_type": "Internship",
                "skills_required": ["Data Science", "SQL", "Pandas", "Scikit-learn"],
                "application_url": "https://internshala.com/internship/detail/mock-ds-intern-303"
            }
        ]
        return [self.normalize_job(**item) for item in mock_data[:count]]
