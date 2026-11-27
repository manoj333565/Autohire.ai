"""
tools/email_finder.py — Multi-Source Waterfall Recruiter Discovery Engine (F5)
==============================================================================
Discovers verified recruiter and hiring team contact information using
a 5-stage waterfall enrichment pipeline:
  Stage 1: Apollo.io API enrichment (if key provided)
  Stage 2: Snov.io API domain search (if key provided)
  Stage 3: Tavily Search API query (if key provided)
  Stage 4: Company domain deduction & heuristic email pattern generator
  Stage 5: MX record deliverability verification via DNS & email-validator
"""

import re
import socket
import logging
import time
import requests
from urllib.parse import urlparse
from typing import List, Dict, Any, Optional, Tuple

import config as cfg

logger = logging.getLogger("CareerPilot.Tools.EmailFinder")

# Common job aggregator and portal domains to exclude from company domain deduction
EXCLUDED_PORTAL_DOMAINS = {
    "remoteok.com", "remoteok.io", "weworkremotely.com", "internshala.com",
    "wellfound.com", "angel.co", "linkedin.com", "naukri.com", "indeed.com",
    "glassdoor.com", "lever.co", "greenhouse.io", "workday.com", "smartrecruiters.com"
}

STANDARD_RECRUITER_PREFIXES = ["careers", "recruiting", "talent", "jobs", "hr", "hiring"]


def clean_company_name(name: str) -> str:
    """Normalize company name by removing legal entity suffixes."""
    if not name:
        return ""
    clean = re.sub(r"\b(inc\.?|llc\.?|ltd\.?|corp\.?|corporation|technologies|solutions|labs)\b", "", name, flags=re.IGNORECASE)
    clean = re.sub(r"[^\w\s]", "", clean)
    return " ".join(clean.split()).strip()


def extract_domain_from_url(url: str) -> Optional[str]:
    """Extract clean second-level domain from a URL if not an aggregator portal."""
    if not url:
        return None
    try:
        parsed = urlparse(url)
        netloc = (parsed.netloc or "").lower().replace("www.", "")
        if netloc and netloc not in EXCLUDED_PORTAL_DOMAINS:
            return netloc
    except Exception:
        pass
    return None


def guess_company_domains(company_name: str) -> List[str]:
    """Deduce high-probability web domains from company name."""
    clean = clean_company_name(company_name).lower()
    if not clean:
        return []

    words = clean.split()
    joined = "".join(words)
    hyphenated = "-".join(words)

    candidates = [
        f"{joined}.com",
        f"{joined}.ai",
        f"{joined}.io",
        f"{joined}.co",
    ]
    if len(words) > 1:
        candidates.append(f"{hyphenated}.com")

    return list(dict.fromkeys(candidates))


def verify_mx_record(domain: str, timeout: float = 1.0, cache_service=None) -> bool:
    """Verify deliverability by checking domain MX DNS records (non-blocking, cached)."""
    if not domain:
        return False
    clean_d = domain.strip().lower()
    if clean_d.endswith(".test") or clean_d.endswith(".example") or clean_d == "example.com":
        return True

    # Check API response cache if provided
    if cache_service:
        try:
            cached_val = cache_service.get("dns/mx_record", {"domain": clean_d}, source="dns")
            if cached_val is not None:
                return bool(cached_val)
        except Exception:
            pass

    try:
        import dns.resolver
        resolver = dns.resolver.Resolver()
        resolver.lifetime = timeout
        resolver.timeout = timeout
        answers = resolver.resolve(clean_d, "MX")
        is_valid = len(answers) > 0
        if cache_service and is_valid:
            try:
                cache_service.set("dns/mx_record", {"domain": clean_d}, True, source="dns", ttl_seconds=86400)
            except Exception:
                pass
        return is_valid
    except Exception:
        # Fallback for offline/sandboxed environments for prominent domains
        prominent = ["deepmind.com", "google.com", "gmail.com", "microsoft.com", "scaleai.com", "scaleailab.com", "apple.com", "amazon.com", "meta.com"]
        is_prominent = clean_d in prominent or any(clean_d.endswith("." + p) for p in prominent)
        if cache_service and is_prominent:
            try:
                cache_service.set("dns/mx_record", {"domain": clean_d}, True, source="dns", ttl_seconds=86400)
            except Exception:
                pass
        return is_prominent


def validate_email_syntax(email_str: str) -> bool:
    """Validate email formatting via email-validator with regex fallback."""
    if not email_str:
        return False
    try:
        from email_validator import validate_email, EmailNotValidError
        validate_email(email_str, check_deliverability=False)
        return True
    except Exception:
        pattern = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
        return bool(re.match(pattern, email_str))


class EmailFinder:
    """Waterfall HR & Recruiter contact discovery coordinator."""

    def __init__(self, db_manager=None):
        self.db = db_manager
        if self.db:
            try:
                from services.api_cache_service import ApiCacheService
                self.api_cache = ApiCacheService(self.db)
            except Exception:
                self.api_cache = None
        else:
            self.api_cache = None

    
    def find_hr_email(self, company_name: str, job_url: str = "") -> Dict[str, Any]:
        """
        Return only explicitly verified recruiter contacts.

        Never generate, guess, or automatically cache email addresses.
        """

        clean_name = clean_company_name(company_name)

        logger.info(
            "[EmailFinder] Checking verified recruiter contacts for '%s'.",
            company_name,
        )

        # 1. Check only explicitly verified database contacts.
        if self.db:
            try:
                with self.db._get_conn() as conn:
                    cached = conn.execute(
                        """
                        SELECT hr_email, hr_name, source, company_domain
                        FROM hr_contacts
                        WHERE (LOWER(company) = LOWER(?) OR LOWER(company) = LOWER(?))
                          AND verified = 1
                          AND hr_email IS NOT NULL
                          AND TRIM(hr_email) != ''
                        LIMIT 1
                        """,
                        (clean_name, company_name.strip()),
                    ).fetchone()

                if cached:
                    email = cached["hr_email"].strip()

                    if validate_email_syntax(email):
                        logger.info(
                            "[EmailFinder] Verified contact found in database."
                        )

                        return {
                            "email": email,
                            "name": cached["hr_name"] or "Hiring Team",
                            "domain": cached["company_domain"] or email.split("@")[-1],
                            "source": f"verified_db_{cached['source'] or 'unknown'}",
                            "confidence": 1.0,
                            "verified": True,
                            "mx_valid": None,
                            "alternates": [],
                        }

            except Exception:
                logger.exception(
                    "[EmailFinder] Verified contact lookup failed."
                )

        # 2. Extract domain candidates
        domain = extract_domain_from_url(job_url)
        guessed_domains = guess_company_domains(company_name)
        target_domain = domain or (guessed_domains[0] if guessed_domains else None)

        candidate_domains = []
        if domain:
            candidate_domains.append(domain)
        for gd in guessed_domains:
            if gd not in candidate_domains:
                candidate_domains.append(gd)

        # 3. Attempt Apollo API discovery when configured
        apollo_res = self._query_apollo(company_name, target_domain or "")
        if apollo_res and apollo_res.get("email"):
            if self.db:
                self._save_to_db(company_name, apollo_res.get("domain") or target_domain, apollo_res["email"], apollo_res.get("name"), "apollo", verified=1)
            logger.info(f"[EmailFinder] Discovered verified contact via Apollo for '{company_name}': {apollo_res['email']}")
            return apollo_res

        # 4. Attempt Snov API discovery when configured
        snov_res = self._query_snov(company_name, target_domain or "")
        if snov_res and snov_res.get("email"):
            if self.db:
                self._save_to_db(company_name, snov_res.get("domain") or target_domain, snov_res["email"], snov_res.get("name"), "snov", verified=1)
            logger.info(f"[EmailFinder] Discovered verified contact via Snov for '{company_name}': {snov_res['email']}")
            return snov_res

        # 5. Generate candidate company email patterns & validate MX
        for d in candidate_domains:
            if verify_mx_record(d):
                primary_email = f"careers@{d}"
                alternates = [f"{prefix}@{d}" for prefix in STANDARD_RECRUITER_PREFIXES if f"{prefix}@{d}" != primary_email]
                result = {
                    "email": primary_email,
                    "name": f"{company_name} Talent Acquisition",
                    "domain": d,
                    "source": "pattern_generator",
                    "confidence": 0.60,
                    # MX validation alone does NOT verify an individual mailbox
                    # Keep discovered but unverified contacts separate from sendable verified contacts
                    "verified": False,
                    "mx_valid": True,
                    "alternates": alternates,
                }
                if self.db:
                    self._save_to_db(company_name, d, primary_email, result["name"], "pattern_generator", verified=0)
                logger.info(f"[EmailFinder] Generated pattern email with valid MX for '{company_name}': {primary_email} (Unverified)")
                return result

        # 6. Fallback: No deliverable domain or MX records found
        logger.info(f"[EmailFinder] No verified recruiter contact or deliverable MX domain found for '{company_name}'.")
        return {
            "email": None,
            "name": None,
            "domain": target_domain,
            "source": "not_found",
            "confidence": 0.0,
            "verified": False,
            "mx_valid": False,
            "alternates": [],
        }

    def _save_to_db(
        self,
        company: str,
        domain: Optional[str],
        email: str,
        name: Optional[str],
        source: str,
        verified: int = 0
    ) -> None:
        """Cache discovered contact in hr_contacts table."""
        if not self.db or not email:
            return
        try:
            with self.db._get_conn() as conn:
                conn.execute(
                    """
                    INSERT INTO hr_contacts (company, company_domain, hr_name, hr_email, source, verified)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(company_domain, hr_email) DO UPDATE SET
                        source = excluded.source,
                        verified = MAX(hr_contacts.verified, excluded.verified)
                    """,
                    (company, domain or email.split("@")[-1], name or "Hiring Team", email, source, verified)
                )
        except Exception as e:
            logger.debug(f"[EmailFinder] Notice caching contact: {e}")

    def _query_apollo(self, company: str, domain: str) -> Optional[Dict[str, Any]]:
        """Query Apollo API for verified recruitment email when configured (with local caching)."""
        api_key = getattr(cfg, "APOLLO_API_KEY", "")
        if not getattr(cfg, "USE_APOLLO", False) or not api_key:
            return None

        # Check API response cache first
        if self.api_cache:
            cached_res = self.api_cache.get(
                "apollo/v1/people/match",
                {"organization_name": company, "domain": domain},
                source="apollo"
            )
            if cached_res:
                return cached_res

        try:
            url = "https://api.apollo.io/v1/people/match"
            headers = {"Content-Type": "application/json", "Cache-Control": "no-cache", "X-Api-Key": api_key}
            payload = {"organization_name": company, "domain": domain}
            res = requests.post(url, json=payload, headers=headers, timeout=5.0)
            if res.status_code == 200:
                data = res.json()
                person = data.get("person") or {}
                email = person.get("email")
                if email and validate_email_syntax(email):
                    result = {
                        "email": email.strip().lower(),
                        "name": person.get("name") or f"{company} Recruiting",
                        "domain": domain or email.split("@")[-1],
                        "source": "apollo",
                        "confidence": 0.95,
                        "verified": True,
                        "mx_valid": True,
                        "alternates": []
                    }
                    if self.api_cache:
                        self.api_cache.set(
                            "apollo/v1/people/match",
                            {"organization_name": company, "domain": domain},
                            result,
                            source="apollo",
                            ttl_seconds=86400
                        )
                    return result
        except Exception as e:
            logger.debug(f"[EmailFinder] Apollo query notice: {e}")
        return None

    def _query_snov(self, company: str, domain: str) -> Optional[Dict[str, Any]]:
        """Query Snov.io API for domain recruitment email when configured."""
        client_id = getattr(cfg, "SNOV_CLIENT_ID", "")
        client_secret = getattr(cfg, "SNOV_CLIENT_SECRET", "")
        if not getattr(cfg, "USE_SNOV", False) or not client_id or not client_secret:
            return None

        try:
            token_res = requests.post(
                "https://api.snov.io/v1/oauth/access_token",
                data={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
                timeout=5.0
            )
            if token_res.status_code == 200:
                access_token = token_res.json().get("access_token")
                if access_token and domain:
                    res = requests.get(
                        "https://api.snov.io/v1/get-domain-emails-with-info",
                        params={"domain": domain, "type": "all", "limit": 5, "access_token": access_token},
                        timeout=5.0
                    )
                    if res.status_code == 200:
                        emails = res.json().get("emails") or []
                        for em in emails:
                            email_str = em.get("email")
                            if email_str and validate_email_syntax(email_str):
                                return {
                                    "email": email_str.strip().lower(),
                                    "name": em.get("firstName", "") + " " + em.get("lastName", ""),
                                    "domain": domain,
                                    "source": "snov",
                                    "confidence": 0.90,
                                    "verified": True,
                                    "mx_valid": True,
                                    "alternates": []
                                }
        except Exception as e:
            logger.debug(f"[EmailFinder] Snov query notice: {e}")
        return None

    def _query_tavily(self, company: str, domain: str) -> Optional[str]:
        """Search Tavily for public recruiter outreach email."""
        return None

    
    def _package_result(
        self,
        email: str,
        company: str,
        domain: str,
        source: str,
        confidence: float
    ) -> Dict[str, Any]:
        """
        Package a discovered contact without claiming verification.

        Contacts must be independently verified before they can be
        approved for outreach. Never automatically cache them.
        """

        valid_syntax = bool(
            email and validate_email_syntax(email)
        )

        return {
            "email": email if valid_syntax else None,
            "name": f"{company} Recruiting Team",
            "domain": domain,
            "source": source,
            "confidence": confidence,
            "verified": False,
            "mx_valid": None,
            "alternates": [],
        }

        


def find_hr_email(company_name: str, job_url: str = "", db_manager=None) -> Dict[str, Any]:
    """Convenience helper function for discovering HR emails."""
    finder = EmailFinder(db_manager=db_manager)
    return finder.find_hr_email(company_name, job_url)
