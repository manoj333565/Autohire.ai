"""
tests/test_scrapers.py — Automated Unit & Integration Tests for Multi-Portal Job Scrapers (F1)
Validates:
- Strict normalized schema compliance across all 6 portals
- Individual scraper execution (RemoteOK, WeWorkRemotely, Internshala, Wellfound, LinkedIn, Naukri)
- ScraperEngine multi-portal aggregation and URL deduplication
- Independent exception boundaries and failure containment
"""
import sys
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

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

REQUIRED_SCHEMA_KEYS = [
    "title",
    "company",
    "location",
    "description",
    "job_url",
    "portal",
    "experience",
    "salary",
    "posted_date",
]


def test_schema_normalization():
    """Verify that normalize_job produces the exact required assignment schema."""
    scraper = RemoteOKScraper(mock_mode=True)
    job = scraper.normalize_job(
        title="Python Backend Developer",
        company="Innovative Tech",
        location="Remote",
        job_url="https://example.com/jobs/101",
        description="FastAPI, RAG, and Agentic AI development.",
        experience="0-2 years",
        salary="$80,000",
        posted_date="2026-11-17"
    )

    for key in REQUIRED_SCHEMA_KEYS:
        assert key in job, f"Missing required normalized schema key: '{key}'"
    assert job["portal"] == "remoteok"
    assert job["title"] == "Python Backend Developer"
    assert job["job_url"] == "https://example.com/jobs/101"


def test_individual_scrapers_mock_mode():
    """Verify that each of the 6 portals yields compliant normalized jobs."""
    scraper_classes = [
        RemoteOKScraper,
        WeWorkRemotelyScraper,
        InternshalaScraper,
        WellfoundScraper,
        LinkedInScraper,
        NaukriScraper,
    ]

    for ScraperClass in scraper_classes:
        scraper = ScraperClass(mock_mode=True)
        jobs = scraper.run_safe(max_jobs=5)

        assert len(jobs) > 0, f"Scraper '{scraper.name}' returned 0 jobs in mock mode"
        for job in jobs:
            for key in REQUIRED_SCHEMA_KEYS:
                assert key in job, f"Job from '{scraper.name}' missing key '{key}'"
            assert job["portal"] == scraper.name
            assert job["job_url"].startswith("http")


def test_scraper_engine_aggregation():
    """Verify multi-portal aggregation and deduplication across all 6 portals."""
    engine = ScraperEngine(mock_mode=True)
    results = engine.run_all(max_jobs_per_portal=5)

    assert "raw_jobs" in results
    assert "portal_stats" in results
    assert results["total_scraped"] > 0

    stats = results["portal_stats"]
    assert len(stats) == 7
    for portal in ["remoteok", "weworkremotely", "internshala", "wellfound", "linkedin", "naukri", "ats"]:
        assert portal in stats
        assert stats[portal]["status"] == "success"
        assert stats[portal]["jobs_found"] > 0

    # Verify no duplicate URLs exist in raw_jobs
    urls = [j["job_url"] for j in results["raw_jobs"]]
    assert len(urls) == len(set(urls)), "Duplicate URLs detected in raw_jobs!"


def test_failure_isolation():
    """Verify that a failure in one scraper does not crash or interrupt other scrapers."""
    engine = ScraperEngine(mock_mode=True)

    # Deliberately break the RemoteOK scraper
    class BrokenScraper(BaseScraper):
        def scrape(self, max_jobs: int = 10):
            raise ConnectionError("Simulated RemoteOK API downtime / 503 Service Unavailable")
        def get_mock_jobs(self, count: int = 5):
            raise TimeoutError("Simulated total network crash on this portal")

    engine.scrapers["remoteok"] = BrokenScraper(name="remoteok", mock_mode=False)

    # Run all portals
    results = engine.run_all(max_jobs_per_portal=3)

    # Other 5 portals must still succeed
    assert results["total_scraped"] > 0, "Pipeline crashed when one scraper failed!"
    assert len(results["errors"]) > 0, "Expected an error message logged for remoteok"
    assert results["portal_stats"]["remoteok"]["status"] == "failed"
    assert results["portal_stats"]["weworkremotely"]["status"] == "success"
    assert results["portal_stats"]["internshala"]["status"] == "success"


if __name__ == "__main__":
    print("Running Milestone 4 Multi-Portal Job Scrapers Test Suite...")
    test_schema_normalization()
    print("  ✅ test_schema_normalization passed")
    test_individual_scrapers_mock_mode()
    print("  ✅ test_individual_scrapers_mock_mode passed (all 6 portals verified)")
    test_scraper_engine_aggregation()
    print("  ✅ test_scraper_engine_aggregation passed (aggregation & deduplication verified)")
    test_failure_isolation()
    print("  ✅ test_failure_isolation passed (independent error boundaries verified)")
    print("\n🎉 ALL MULTI-PORTAL SCRAPER TESTS PASSED SUCCESSFULLY!")
