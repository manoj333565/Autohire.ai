"""
scrapers/naukri_scraper.py — Naukri India Job Portal Scraper
===========================================================
Defensive scraper for Indian fresher and entry-level IT positions with
Playwright stealth patches, overlay dismissal, and guaranteed process cleanup.
"""
import asyncio
import random
import logging
from typing import List, Dict, Any
from scrapers.base_scraper import BaseScraper
import config as cfg

logger = logging.getLogger("CareerPilot.Scrapers.Naukri")

NAUKRI_SEARCH_URLS = [
    "https://www.naukri.com/python-developer-jobs-for-freshers",
    "https://www.naukri.com/python-developer-jobs-for-freshers-2",
    "https://www.naukri.com/machine-learning-engineer-jobs-for-freshers",
    "https://www.naukri.com/data-analyst-jobs-for-freshers",
]


class NaukriScraper(BaseScraper):
    """Defensive scraper for Naukri India job listings."""

    def __init__(self, timeout_seconds: int = 30, mock_mode: bool = False):
        super().__init__(name="naukri", timeout_seconds=timeout_seconds, mock_mode=mock_mode)

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
            logger.warning("[NAUKRI] Playwright or playwright-stealth not installed. PROTECTED / UNAVAILABLE.")
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

                for url in NAUKRI_SEARCH_URLS:
                    if len(jobs) >= max_jobs:
                        break
                    try:
                        logger.debug(f"[NAUKRI] Navigating to {url}")
                        await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)

                        page_title = (await page.title()).lower()
                        if "access denied" in page_title or "verify you are human" in page_title:
                            logger.info(f"[NAUKRI] Site protection challenge active ({page_title}). Respecting site controls.")
                            break

                        await asyncio.sleep(random.uniform(2, 4))

                        # Dismiss possible promotional banners / chatbots
                        for selector in ["span.crossIcon", "div.chatbot_Drawer__close", "div.drawer__close", "span#block"]:
                            try:
                                close_btn = await page.query_selector(selector)
                                if close_btn:
                                    await close_btn.click()
                                    await asyncio.sleep(0.5)
                            except Exception:
                                pass

                        cards = await page.query_selector_all(
                            "div.cust-job-tuple, article.jobTuple, div.srp-jobtuple-wrapper"
                        )

                        for card in cards[:12]:
                            title_el = await card.query_selector("a.title, a[class*='title']")
                            company_el = await card.query_selector("a.comp-name, a.subTitle, a[class*='comp-name']")
                            exp_el = await card.query_selector("span.exp-wrap, span.experience, span[class*='exp']")
                            sal_el = await card.query_selector("span.sal-wrap, span.salary, span[class*='sal']")
                            loc_el = await card.query_selector("span.loc-wrap, span.location, span[class*='loc']")

                            title = (await title_el.inner_text()).strip() if title_el else "Python Developer"
                            company = (await company_el.inner_text()).strip() if company_el else "Naukri Verified Employer"
                            href = await title_el.get_attribute("href") if title_el else ""
                            experience = (await exp_el.inner_text()).strip() if exp_el else "0-1 Yrs"
                            salary = (await sal_el.inner_text()).strip() if sal_el else None
                            location = (await loc_el.inner_text()).strip() if loc_el else "India"

                            canonical = self.canonicalize_url(href)
                            if canonical and canonical not in seen_urls:
                                seen_urls.add(canonical)
                                jobs.append(self.normalize_job(
                                    title=title,
                                    company=company,
                                    location=location,
                                    job_url=canonical,
                                    description=f"{title} position at {company} in {location}. Full listing on Naukri.com.",
                                    experience=experience,
                                    salary=salary,
                                    posted_date="Recently",
                                    employment_type="Full-time",
                                    application_url=canonical
                                ))
                                if len(jobs) >= max_jobs:
                                    break

                    except Exception as e:
                        logger.debug(f"[NAUKRI] Page scrape error on {url}: {e}")

        except Exception as e:
            logger.warning(f"[NAUKRI] Browser error: {e}")
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
                "title": "Python Developer - Trainee / Fresher",
                "company": "Wipro Digital",
                "location": "Noida / Gurgaon, India",
                "job_url": "https://www.naukri.com/job-listings-mock-naukri-python-601",
                "description": "Looking for entry-level Python developers with knowledge of data structures, OOP, SQL, and web frameworks.",
                "experience": "0-1 Yrs",
                "salary": "₹4.0 - ₹6.0 LPA",
                "posted_date": "1 day ago",
                "employment_type": "Full-time",
                "skills_required": ["Python", "OOP", "SQL", "Data Structures"],
                "application_url": "https://www.naukri.com/job-listings-mock-naukri-python-601"
            },
            {
                "title": "Associate AI Engineer / Data Science Trainee",
                "company": "Tech Mahindra",
                "location": "Pune, Maharashtra, India",
                "job_url": "https://www.naukri.com/job-listings-mock-naukri-ai-602",
                "description": "Assisting with machine learning data preprocessing, model testing, and LLM prompt engineering.",
                "experience": "0-1 Yrs",
                "salary": "₹4.5 - ₹7.0 LPA",
                "posted_date": "Today",
                "employment_type": "Full-time",
                "skills_required": ["Machine Learning", "Data Preprocessing", "Python", "Prompt Engineering"],
                "application_url": "https://www.naukri.com/job-listings-mock-naukri-ai-602"
            }
        ]
        return [self.normalize_job(**item) for item in mock_data[:count]]
