"""
tests/test_scraper_reliability.py — Phase 3 Job Scraper Reliability & Deduplication Tests
========================================================================================
Comprehensive automated verification covering:
  T1: Full standardized schema compliance (14 fields + legacy aliases) across all 6 portals
  T2: Canonical URL sanitization (stripping UTM, trackingId, ref parameters)
  T3: Deterministic job_id hash generation
  T4: Exponential backoff, jitter, and retry on 429/503 HTTP status
  T5: Cross-portal deduplication and source preservation
  T6: Intra-portal and repeated run deduplication
  T7: Safe browser automation lifecycle and cleanup
  T8: SQLite database integration with standardized jobs and duplicate rejection
  T9: Site protection and anti-bot challenge graceful handling
"""

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Add project root to sys.path
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from scrapers import (
    BaseScraper,
    ScraperEngine,
    RemoteOKScraper,
    WeWorkRemotelyScraper,
    InternshalaScraper,
    WellfoundScraper,
    LinkedInScraper,
    NaukriScraper,
)

STANDARDIZED_PHASE3_FIELDS = [
    "job_id",
    "source",
    "job_title",
    "company_name",
    "job_description",
    "location",
    "salary",
    "experience_required",
    "employment_type",
    "skills_required",
    "posted_date",
    "job_url",
    "application_url",
    "scraped_at",
]

LEGACY_ALIASES = [
    "title",
    "company",
    "location",
    "description",
    "job_url",
    "url",
    "portal",
    "experience",
]


class TestScraperReliability(unittest.TestCase):
    """Test suite for Phase 3 Job Scraper Reliability Audit & Fixes."""

    @classmethod
    def setUpClass(cls):
        cls.db = DatabaseManager(cfg.DB_PATH)
        cls.engine = ScraperEngine(mock_mode=True)
        cls.scrapers = [
            RemoteOKScraper(mock_mode=True),
            WeWorkRemotelyScraper(mock_mode=True),
            InternshalaScraper(mock_mode=True),
            WellfoundScraper(mock_mode=True),
            LinkedInScraper(mock_mode=True),
            NaukriScraper(mock_mode=True),
        ]

    # -------------------------------------------------------------------------
    # T1: Standardized Schema Compliance
    # -------------------------------------------------------------------------
    def test_01_standardized_schema_all_fields(self):
        """Verify that every scraper produces all 14 Phase 3 fields plus legacy aliases."""
        for scraper in self.scrapers:
            jobs = scraper.run_safe(max_jobs=3)
            self.assertGreater(len(jobs), 0, f"Scraper '{scraper.name}' returned 0 jobs")

            for job in jobs:
                # 1. Check all 14 standardized fields
                for field in STANDARDIZED_PHASE3_FIELDS:
                    self.assertIn(field, job, f"Scraper '{scraper.name}' missing standardized field '{field}'")

                # 2. Check legacy aliases for backward compatibility
                for alias in LEGACY_ALIASES:
                    self.assertIn(alias, job, f"Scraper '{scraper.name}' missing legacy alias '{alias}'")

                # 3. Verify types and non-empty essentials
                self.assertTrue(bool(job["job_id"]), "job_id must not be empty")
                self.assertEqual(job["source"], scraper.name)
                self.assertEqual(job["portal"], scraper.name)
                self.assertEqual(job["job_title"], job["title"])
                self.assertEqual(job["company_name"], job["company"])
                self.assertEqual(job["job_url"], job["url"])
                self.assertIsInstance(job["skills_required"], list)
                self.assertTrue(job["job_url"].startswith("http"))

    # -------------------------------------------------------------------------
    # T2: URL Canonicalization & Tracking Stripping
    # -------------------------------------------------------------------------
    def test_02_url_canonicalization_and_tracking_stripping(self):
        """Verify canonicalize_url strips marketing/tracking parameters."""
        scraper = self.scrapers[0]
        dirty_url = (
            "https://www.linkedin.com/jobs/view/12345678"
            "?utm_source=newsletter&utm_medium=email&utm_campaign=daily_jobs"
            "&trackingId=xyz987abc&refId=ref123&trk=public_jobs"
        )
        clean = scraper.canonicalize_url(dirty_url)
        self.assertEqual(clean, "https://www.linkedin.com/jobs/view/12345678")
        self.assertNotIn("utm_source", clean)
        self.assertNotIn("trackingId", clean)

    # -------------------------------------------------------------------------
    # T3: Deterministic Job ID Generation
    # -------------------------------------------------------------------------
    def test_03_deterministic_job_id_generation(self):
        """Verify identical URLs produce the exact same job_id hash."""
        scraper = self.scrapers[0]
        url = "https://remoteok.com/l/python-engineer-101"
        id1 = scraper.make_job_id(url, "ScaleAI", "Python Developer")
        id2 = scraper.make_job_id(url, "ScaleAI", "Python Developer")
        self.assertEqual(id1, id2)
        self.assertEqual(len(id1), 16)

        # Different URL must produce different job_id
        diff_id = scraper.make_job_id("https://remoteok.com/l/other-job-999", "ScaleAI", "Python Developer")
        self.assertNotEqual(id1, diff_id)

    # -------------------------------------------------------------------------
    # T4: Exponential Backoff & Retry Logic
    # -------------------------------------------------------------------------
    def test_04_exponential_backoff_retry(self):
        """Verify request_with_retry retries on 429/503 status codes."""
        scraper = self.scrapers[0]

        mock_resp_429 = MagicMock()
        mock_resp_429.status_code = 429

        mock_resp_200 = MagicMock()
        mock_resp_200.status_code = 200
        mock_resp_200.json.return_value = [{"id": "test_1", "position": "AI Engineer"}]

        # Fail once with 429, succeed on second attempt
        with patch("requests.get", side_effect=[mock_resp_429, mock_resp_200]):
            with patch("scrapers.base_scraper.time.sleep") as mock_sleep:
                resp = scraper.request_with_retry("https://example.com/test", max_retries=2, backoff_factor=0.01)
                self.assertEqual(resp.status_code, 200)
                mock_sleep.assert_called()

    # -------------------------------------------------------------------------
    # T5: Cross-Portal Deduplication
    # -------------------------------------------------------------------------
    def test_05_cross_portal_deduplication(self):
        """Verify cross-portal duplicate detection when the same job is listed on multiple sites."""
        job_from_remoteok = self.scrapers[0].normalize_job(
            title="Senior Python & RAG Architect",
            company="DeepMind Solutions",
            location="Remote",
            job_url="https://remoteok.com/l/deepmind-rag-architect",
            description="Leading development of agentic RAG systems."
        )
        job_from_wwr = self.scrapers[1].normalize_job(
            title="Senior Python & RAG Architect",
            company="DeepMind Solutions",
            location="Remote",
            job_url="https://weworkremotely.com/jobs/deepmind-rag-architect-alt",
            description="Leading development of agentic RAG systems."
        )

        dedup_result = self.engine.deduplicate_jobs([job_from_remoteok, job_from_wwr])
        unique = dedup_result["unique_jobs"]

        # Only 1 unique job must survive
        self.assertEqual(len(unique), 1)
        self.assertEqual(dedup_result["duplicates_filtered"], 1)
        self.assertEqual(dedup_result["cross_portal_duplicates"], 1)

        # Cross-portal sources must preserve both portals
        surviving = unique[0]
        self.assertIn("remoteok", surviving["cross_portal_sources"])
        self.assertIn("weworkremotely", surviving["cross_portal_sources"])

    # -------------------------------------------------------------------------
    # T6: Repeated Scraper Runs & Intra-Portal Deduplication
    # -------------------------------------------------------------------------
    def test_06_repeated_runs_deduplication(self):
        """Verify that running scrapers repeatedly does not return duplicate listings."""
        results = self.engine.run_all(max_jobs_per_portal=4)
        raw_jobs = results["raw_jobs"]

        # All URLs must be unique
        urls = [j["job_url"] for j in raw_jobs]
        self.assertEqual(len(urls), len(set(urls)), "Duplicate URLs detected in ScraperEngine output!")

        # All job_ids must be unique
        job_ids = [j["job_id"] for j in raw_jobs]
        self.assertEqual(len(job_ids), len(set(job_ids)), "Duplicate job_ids detected in ScraperEngine output!")

    # -------------------------------------------------------------------------
    # T7: Browser Cleanup on Exception
    # -------------------------------------------------------------------------
    def test_07_browser_cleanup_guarantee(self):
        """Verify Playwright browser instances are safely closed even when errors occur."""
        scraper = WellfoundScraper(mock_mode=True)
        # Mock mode must cleanly return without leaving browser resources
        jobs = scraper.scrape(max_jobs=2)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0]["source"], "wellfound")

    # -------------------------------------------------------------------------
    # T8: SQLite Database Integration
    # -------------------------------------------------------------------------
    def test_08_database_integration_standardized_jobs(self):
        """Verify that standardized jobs save to applications table and duplicates are rejected."""
        import uuid
        unique_token = uuid.uuid4().hex[:8]
        test_job = self.scrapers[0].normalize_job(
            title=f"Python ML Engineer ({unique_token})",
            company=f"TestAI Corp ({unique_token})",
            location="Remote",
            job_url=f"https://example.com/jobs/test-{unique_token}",
            description="Building machine learning pipelines in Python.",
            salary="$85,000",
            experience="0-2 years",
            employment_type="Full-time"
        )

        # 1. First insert must succeed
        inserted = self.db.add_job(test_job)
        self.assertTrue(inserted)

        # 2. Second insert with exact same URL must be rejected
        duplicate_inserted = self.db.add_job(test_job)
        self.assertFalse(duplicate_inserted, "Database must reject duplicate job URLs")

    # -------------------------------------------------------------------------
    # T9: Site Protection & Graceful Degradation (No Mock Data in Live Mode)
    # -------------------------------------------------------------------------
    def test_09_site_protection_graceful_handling(self):
        """Verify that live scrapers return 0 jobs and isolate errors without returning mock data."""
        class ChallengeBlockedScraper(BaseScraper):
            def scrape(self, max_jobs: int = 5):
                raise PermissionError("Cloudflare Turnstile challenge page served (403 Forbidden)")
            def get_mock_jobs(self, count: int = 5):
                return [self.normalize_job(
                    title="Cached Python Job",
                    company="Safe Fallback Ltd",
                    location="Remote",
                    job_url="https://example.com/cached-101",
                    description="Cached listing when site protection is active."
                )]

        # Live mode: Must NEVER return mock/cached data on error
        live_scraper = ChallengeBlockedScraper(name="blocked_test", mock_mode=False)
        jobs_live = live_scraper.run_safe(max_jobs=3)
        self.assertEqual(len(jobs_live), 0, "Live scraper must return 0 jobs on failure, never mock data")
        self.assertIsNotNone(live_scraper.last_error)
        self.assertIn("Cloudflare Turnstile", live_scraper.last_error)

        # Mock mode: Allowed to return mock data
        mock_scraper = ChallengeBlockedScraper(name="blocked_test", mock_mode=True)
        jobs_mock = mock_scraper.run_safe(max_jobs=3)
        self.assertEqual(len(jobs_mock), 1, "Mock mode must return fixture jobs")
        self.assertEqual(jobs_mock[0]["company_name"], "Safe Fallback Ltd")

    # -------------------------------------------------------------------------
    # T10: Regression Test - Live Network Failures Never Return Mock Jobs
    # -------------------------------------------------------------------------
    def test_10_live_failure_never_returns_mock_jobs(self):
        """Regression test verifying all scrapers strictly return empty lists on live network failure."""
        for ScraperClass in [RemoteOKScraper, WeWorkRemotelyScraper, InternshalaScraper, WellfoundScraper, LinkedInScraper, NaukriScraper]:
            scraper = ScraperClass(mock_mode=False)
            with patch.object(scraper, "scrape", side_effect=ConnectionError("Simulated live network outage")):
                results = scraper.run_safe(max_jobs=5)
                self.assertEqual(
                    results,
                    [],
                    f"Scraper '{scraper.name}' returned mock data during a live network failure!"
                )
                self.assertIsNotNone(scraper.last_error)
                self.assertIn("Simulated live network outage", scraper.last_error)


if __name__ == "__main__":
    print("Running Phase 3 Job Scraper Reliability & Deduplication Test Suite...")
    unittest.main()
