"""
scrapers/__init__.py — Multi-Portal Job Scrapers Module for CareerPilot AI
"""
from scrapers.base_scraper import BaseScraper
from scrapers.scraper_engine import ScraperEngine
from scrapers.remoteok_scraper import RemoteOKScraper
from scrapers.weworkremotely_scraper import WeWorkRemotelyScraper
from scrapers.internshala_scraper import InternshalaScraper
from scrapers.wellfound_scraper import WellfoundScraper
from scrapers.linkedin_scraper import LinkedInScraper
from scrapers.naukri_scraper import NaukriScraper

__all__ = [
    "BaseScraper",
    "ScraperEngine",
    "RemoteOKScraper",
    "WeWorkRemotelyScraper",
    "InternshalaScraper",
    "WellfoundScraper",
    "LinkedInScraper",
    "NaukriScraper",
]
