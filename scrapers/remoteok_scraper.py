"""
scrapers/remoteok_scraper.py — RemoteOK Public JSON API Scraper
==============================================================
Queries https://remoteok.com/api with exponential backoff, jitter,
and rate-limit resilience.
"""
import time
import random
import logging
from typing import List, Dict, Any
from scrapers.base_scraper import BaseScraper

logger = logging.getLogger("CareerPilot.Scrapers.RemoteOK")

REMOTEOK_API_URL = "https://remoteok.com/api"


class RemoteOKScraper(BaseScraper):
    """Scraper for RemoteOK jobs using their public JSON REST API."""

    def __init__(self, timeout_seconds: int = 20, mock_mode: bool = False):
        super().__init__(name="remoteok", timeout_seconds=timeout_seconds, mock_mode=mock_mode)

    def scrape(self, max_jobs: int = 30) -> List[Dict[str, Any]]:
        if self.mock_mode:
            return self.get_mock_jobs(count=max_jobs)
        headers = self.get_random_headers()
        headers["Accept"] = "application/json"

        # Respectful delay before hitting API
        time.sleep(random.uniform(1.0, 2.0))

        resp = self.request_with_retry(
            REMOTEOK_API_URL,
            headers=headers,
            timeout=self.timeout,
            max_retries=3,
            backoff_factor=2.0
        )
        data = resp.json()

        # RemoteOK's first item is a legal notice/disclaimer object
        listings = [item for item in data if isinstance(item, dict) and "id" in item]
        normalized_jobs = []

        target_keywords = [
            "python", "ai", "machine learning", "ml", "data", "engineer",
            "backend", "software", "developer", "deep learning", "nlp"
        ]

        for item in listings:
            title = item.get("position", "")
            tags = [t.lower() for t in item.get("tags", [])]
            combined_text = f"{title} {' '.join(tags)}".lower()

            # Filter for relevancy to target roles
            if not any(kw in combined_text for kw in target_keywords):
                continue

            raw_url = item.get("url") or f"https://remoteok.com/l/{item.get('id', '')}"
            apply_url = item.get("apply_url") or raw_url

            salary_min = item.get("salary_min")
            salary_max = item.get("salary_max")
            salary_str = (
                f"${salary_min:,} - ${salary_max:,}"
                if salary_min and salary_max
                else (f"${salary_min:,}" if salary_min else None)
            )

            # Map skills from tags
            skills = [t.title() for t in item.get("tags", []) if len(t) <= 25]

            job = self.normalize_job(
                title=title,
                company=item.get("company", "RemoteOK Company"),
                location=item.get("location") or "Remote",
                job_url=raw_url,
                description=item.get("description", ""),
                experience="0-2 years",
                salary=salary_str,
                posted_date=item.get("date", ""),
                employment_type="Full-time",
                skills_required=skills,
                application_url=apply_url,
                extra_metadata={
                    "remoteok_id": item.get("id"),
                    "tags": tags,
                    "is_remote": True
                }
            )
            normalized_jobs.append(job)
            if len(normalized_jobs) >= max_jobs:
                break

        return normalized_jobs

    def get_mock_jobs(self, count: int = 5) -> List[Dict[str, Any]]:
        mock_data = [
            {
                "title": "Junior Python & AI Backend Developer",
                "company": "ScaleAI Cloud",
                "location": "Remote",
                "job_url": "https://remoteok.com/l/mock-remoteok-python-101",
                "description": "Develop scalable Python and FastAPI microservices for LLM agent workflows and RAG pipelines.",
                "experience": "0-1 year",
                "salary": "$60,000 - $85,000",
                "posted_date": "2026-11-15",
                "employment_type": "Full-time",
                "skills_required": ["Python", "FastAPI", "AI", "RAG"],
                "application_url": "https://remoteok.com/l/mock-remoteok-python-101"
            },
            {
                "title": "Machine Learning Engineer (Entry Level)",
                "company": "NextGen Vision",
                "location": "Remote",
                "job_url": "https://remoteok.com/l/mock-remoteok-ml-102",
                "description": "Looking for entry-level ML engineer with PyTorch and data processing expertise.",
                "experience": "0-2 years",
                "salary": "$70,000 - $95,000",
                "posted_date": "2026-11-16",
                "employment_type": "Full-time",
                "skills_required": ["Machine Learning", "PyTorch", "Python"],
                "application_url": "https://remoteok.com/l/mock-remoteok-ml-102"
            },
            {
                "title": "Data Scientist / Analytics Intern",
                "company": "DataVibe Analytics",
                "location": "Remote",
                "job_url": "https://remoteok.com/l/mock-remoteok-ds-103",
                "description": "Perform EDA, SQL queries, feature engineering, and build predictive machine learning models.",
                "experience": "0-1 year",
                "salary": "$50,000 - $70,000",
                "posted_date": "2026-11-16",
                "employment_type": "Internship",
                "skills_required": ["Data Science", "SQL", "Pandas", "Python"],
                "application_url": "https://remoteok.com/l/mock-remoteok-ds-103"
            },
            {
                "title": "Generative AI Application Developer",
                "company": "Cognitive AI Labs",
                "location": "Remote",
                "job_url": "https://remoteok.com/l/mock-remoteok-genai-104",
                "description": "Build agentic AI applications with LangChain, LangGraph, and vector databases (Qdrant).",
                "experience": "0-2 years",
                "salary": "$80,000 - $110,000",
                "posted_date": "2026-11-17",
                "employment_type": "Full-time",
                "skills_required": ["Agentic AI", "LangChain", "Vector Databases", "Python"],
                "application_url": "https://remoteok.com/l/mock-remoteok-genai-104"
            },
            {
                "title": "Python Software Engineer",
                "company": "Nova Solutions",
                "location": "Remote",
                "job_url": "https://remoteok.com/l/mock-remoteok-se-105",
                "description": "Design and maintain backend REST APIs in Python and SQL databases.",
                "experience": "0-2 years",
                "salary": "$65,000 - $90,000",
                "posted_date": "2026-11-17",
                "employment_type": "Full-time",
                "skills_required": ["Python", "SQL", "FastAPI"],
                "application_url": "https://remoteok.com/l/mock-remoteok-se-105"
            }
        ]
        return [self.normalize_job(**item) for item in mock_data[:count]]
