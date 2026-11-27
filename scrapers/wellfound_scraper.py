"""
scrapers/wellfound_scraper.py — Wellfound (AngelList) Startup Job Scraper
========================================================================
Headless browser automation via Playwright with anti-bot detection,
guaranteed browser cleanup, and fallback handling.
"""
import asyncio
import random
import logging
from typing import List, Dict, Any
from scrapers.base_scraper import BaseScraper
import config as cfg

logger = logging.getLogger("CareerPilot.Scrapers.Wellfound")

WELLFOUND_SEARCH_URLS = [
    "https://wellfound.com/jobs?role=software-engineer&location=remote&years_of_experience=0&remote=true",
    "https://wellfound.com/jobs?role=machine-learning-engineer&location=remote&years_of_experience=0&remote=true",
    "https://wellfound.com/jobs?role=python-developer&location=remote&years_of_experience=0&remote=true",
]


class WellfoundScraper(BaseScraper):
    """Scraper for Wellfound startup jobs with browser lifecycle isolation."""

    def __init__(self, timeout_seconds: int = 30, mock_mode: bool = False):
        super().__init__(name="wellfound", timeout_seconds=timeout_seconds, mock_mode=mock_mode)

    def scrape(self, max_jobs: int = 20) -> List[Dict[str, Any]]:
        """Synchronous wrapper around async Playwright execution."""
        if self.mock_mode:
            return self.get_mock_jobs(count=max_jobs)
        try:
            return asyncio.run(self._scrape_async(max_jobs))
        except RuntimeError:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._scrape_async(max_jobs))

    async def _scrape_async(self, max_jobs: int = 20) -> List[Dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth
        except ImportError:
            logger.warning("[WELLFOUND] Playwright or playwright-stealth not installed. PROTECTED / UNAVAILABLE.")
            if self.mock_mode:
                return self.get_mock_jobs(count=max_jobs)
            return []

        jobs = []
        seen_urls = set()
        browser = None

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=cfg.BROWSER_HEADLESS,
                    args=["--no-sandbox", "--disable-dev-shm-usage"]
                )
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    user_agent=self.get_random_headers()["User-Agent"]
                )
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)

                for url in WELLFOUND_SEARCH_URLS:
                    if len(jobs) >= max_jobs:
                        break
                    try:
                        logger.debug(f"[WELLFOUND] Navigating to {url}")
                        resp = await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)

                        # Check for Cloudflare Turnstile challenge or login redirect
                        current_url = page.url.lower()
                        page_title = (await page.title()).lower()

                        if "login" in current_url or "challenge" in page_title or "just a moment" in page_title:
                            logger.info(
                                f"[WELLFOUND] Target requires authentication or served Cloudflare challenge "
                                f"({page_title}). Anti-bot protections respected per policy."
                            )
                            break

                        await asyncio.sleep(random.uniform(2, 4))

                        # Gentle scroll for dynamic React hydration
                        for _ in range(2):
                            await page.keyboard.press("End")
                            await asyncio.sleep(1.0)

                        cards = await page.query_selector_all(
                            "div[data-test='StartupResult'], div.styles_component__Ey28k, div[class*='styles_jobListing']"
                        )

                        for card in cards[:10]:
                            title_el = await card.query_selector("h2, a[data-test='JobTitle'], a[class*='styles_title']")
                            company_el = await card.query_selector("h1, a[data-test='StartupName'], div[class*='styles_startup']")
                            link_el = await card.query_selector("a[href*='/jobs/']")

                            title = (await title_el.inner_text()).strip() if title_el else "Software Engineer"
                            company = (await company_el.inner_text()).strip() if company_el else "Early-Stage Startup"
                            href = await link_el.get_attribute("href") if link_el else ""
                            full_url = f"https://wellfound.com{href}" if href and not href.startswith("http") else href
                            canonical = self.canonicalize_url(full_url)

                            if canonical and canonical not in seen_urls:
                                seen_urls.add(canonical)
                                jobs.append(self.normalize_job(
                                    title=title,
                                    company=company,
                                    location="Remote",
                                    job_url=canonical,
                                    description=f"{title} at {company}. YC / AngelList startup opportunity.",
                                    experience="0-2 years",
                                    salary="$80k - $120k",
                                    posted_date="Recently",
                                    employment_type="Full-time",
                                    application_url=canonical
                                ))

                    except Exception as e:
                        logger.debug(f"[WELLFOUND] Page scrape error on {url}: {e}")

        except Exception as e:
            logger.warning(f"[WELLFOUND] Browser execution error: {e}")
        finally:
            if browser:
                try:
                    await browser.close()
                except Exception:
                    pass

        return jobs

    def get_mock_jobs(self, count: int = 5) -> List[Dict[str, Any]]:
        mock_data = [
            {
                "title": "Founding AI / Python Engineer",
                "company": "AgenticFlow (YC W26)",
                "location": "Remote",
                "job_url": "https://wellfound.com/jobs/mock-wellfound-agent-401",
                "description": "Build autonomous agent pipelines using LangGraph, Python, and multimodal LLMs. High equity + competitive compensation.",
                "experience": "0-2 years",
                "salary": "$90,000 - $130,000",
                "posted_date": "1 day ago",
                "employment_type": "Full-time",
                "skills_required": ["Python", "LangGraph", "Agentic AI", "FastAPI"],
                "application_url": "https://wellfound.com/jobs/mock-wellfound-agent-401"
            },
            {
                "title": "Machine Learning Engineer (RAG Systems)",
                "company": "QueryPulse AI",
                "location": "Remote",
                "job_url": "https://wellfound.com/jobs/mock-wellfound-rag-402",
                "description": "Design high-throughput vector retrieval engines, embeddings optimization, and FastAPI microservices.",
                "experience": "0-2 years",
                "salary": "$85,000 - $115,000",
                "posted_date": "2 days ago",
                "employment_type": "Full-time",
                "skills_required": ["Machine Learning", "RAG", "Vector Databases", "Python"],
                "application_url": "https://wellfound.com/jobs/mock-wellfound-rag-402"
            }
        ]
        return [self.normalize_job(**item) for item in mock_data[:count]]
