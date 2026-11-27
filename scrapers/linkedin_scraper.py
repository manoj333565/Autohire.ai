"""
scrapers/linkedin_scraper.py — LinkedIn Job Scraper with Playwright Stealth
==========================================================================
Scrapes public LinkedIn search pages with stealth browser automation,
rate-limit and authwall detection, and guaranteed process cleanup.
"""
import asyncio
import random
import logging
from typing import List, Dict, Any
from scrapers.base_scraper import BaseScraper
import config as cfg

logger = logging.getLogger("CareerPilot.Scrapers.LinkedIn")

PUBLIC_SEARCH_URLS = [
    "https://www.linkedin.com/jobs/search?keywords=python%20developer%20fresher&location=India&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search?keywords=machine%20learning%20engineer%20fresher&location=India&f_TPR=r86400",
    "https://www.linkedin.com/jobs/search?keywords=generative%20ai%20engineer&location=Remote&f_TPR=r86400",
]


class LinkedInScraper(BaseScraper):
    """Defensive scraper for public LinkedIn job listings."""

    def __init__(self, timeout_seconds: int = 35, mock_mode: bool = False):
        super().__init__(name="linkedin", timeout_seconds=timeout_seconds, mock_mode=mock_mode)

    def scrape(self, max_jobs: int = 25) -> List[Dict[str, Any]]:
        """Synchronous wrapper around async Playwright execution."""
        if self.mock_mode:
            return self.get_mock_jobs(count=max_jobs)
        try:
            return asyncio.run(self._scrape_async(max_jobs))
        except RuntimeError:
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self._scrape_async(max_jobs))

    async def _scrape_async(self, max_jobs: int = 25) -> List[Dict[str, Any]]:
        try:
            from playwright.async_api import async_playwright
            from playwright_stealth import Stealth
        except ImportError:
            logger.warning("[LINKEDIN] Playwright or playwright-stealth not installed. PROTECTED / UNAVAILABLE.")
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
                    args=["--no-sandbox", "--disable-blink-features=AutomationControlled"]
                )
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    user_agent=self.get_random_headers()["User-Agent"]
                )
                page = await context.new_page()
                await Stealth().apply_stealth_async(page)

                for url in PUBLIC_SEARCH_URLS:
                    if len(jobs) >= max_jobs:
                        break
                    try:
                        logger.debug(f"[LINKEDIN] Navigating to {url}")
                        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)

                        current_url = page.url.lower()
                        page_title = (await page.title()).lower()

                        # Authwall / login redirect detection
                        if "authwall" in current_url or "sign in" in page_title or "login" in current_url:
                            logger.info(f"[LINKEDIN] Public rate limit or authwall triggered ({page_title}). Respecting site controls.")
                            break

                        await asyncio.sleep(random.uniform(2, 4))

                        # Scroll gently to trigger dynamic card loading
                        for _ in range(2):
                            await page.keyboard.press("PageDown")
                            await asyncio.sleep(1.2)

                        cards = await page.query_selector_all(
                            "div.base-card, li.jobs-search-results__list-item, div.job-search-card"
                        )

                        for card in cards[:12]:
                            title_el = await card.query_selector(
                                "h3.base-search-card__title, a.job-card-list__title, h3"
                            )
                            company_el = await card.query_selector(
                                "h4.base-search-card__subtitle, a.job-card-container__company-name, h4"
                            )
                            loc_el = await card.query_selector(
                                "span.job-search-card__location, span.job-card-container__metadata-item"
                            )
                            link_el = await card.query_selector(
                                "a.base-card__full-link, a[href*='/jobs/view/'], a[href*='/jobs/']"
                            )

                            title = (await title_el.inner_text()).strip() if title_el else "Python Developer"
                            company = (await company_el.inner_text()).strip() if company_el else "LinkedIn Employer"
                            location = (await loc_el.inner_text()).strip() if loc_el else "India / Remote"
                            href = await link_el.get_attribute("href") if link_el else ""

                            canonical = self.canonicalize_url(href)
                            if canonical and canonical not in seen_urls:
                                seen_urls.add(canonical)
                                jobs.append(self.normalize_job(
                                    title=title,
                                    company=company,
                                    location=location,
                                    job_url=canonical,
                                    description=f"{title} position at {company} ({location}). Sourced from LinkedIn public listings.",
                                    experience="0-1 year (Entry level)",
                                    salary="Competitive",
                                    posted_date="Today",
                                    employment_type="Full-time",
                                    application_url=canonical
                                ))
                                if len(jobs) >= max_jobs:
                                    break

                    except Exception as e:
                        logger.debug(f"[LINKEDIN] Page scrape error on {url}: {e}")

        except Exception as e:
            logger.warning(f"[LINKEDIN] Browser error: {e}")
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
                "title": "Junior Python / GenAI Engineer",
                "company": "Infosys Innovation Labs",
                "location": "Bengaluru, Karnataka, India",
                "job_url": "https://www.linkedin.com/jobs/view/mock-linkedin-python-501",
                "description": "Develop generative AI workflows, agentic architectures, and microservices in Python. Candidates with LangChain and vector database experience preferred.",
                "experience": "0-1 year",
                "salary": "₹5.5 - ₹8.0 LPA",
                "posted_date": "Today",
                "employment_type": "Full-time",
                "skills_required": ["Python", "Generative AI", "LangChain", "Vector Databases"],
                "application_url": "https://www.linkedin.com/jobs/view/mock-linkedin-python-501"
            },
            {
                "title": "Data Analyst / Python Developer",
                "company": "Tata Consultancy Services (TCS)",
                "location": "Hyderabad, Telangana, India",
                "job_url": "https://www.linkedin.com/jobs/view/mock-linkedin-data-502",
                "description": "SQL analysis, Python data processing pipelines, and business intelligence reporting.",
                "experience": "0-2 years",
                "salary": "₹4.5 - ₹6.5 LPA",
                "posted_date": "Yesterday",
                "employment_type": "Full-time",
                "skills_required": ["SQL", "Python", "Data Analysis", "Power BI"],
                "application_url": "https://www.linkedin.com/jobs/view/mock-linkedin-data-502"
            }
        ]
        return [self.normalize_job(**item) for item in mock_data[:count]]
