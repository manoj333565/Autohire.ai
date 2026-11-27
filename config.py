"""
config.py — Centralized Configuration System for CareerPilot AI / Autohire.ai
Handles environment variables, feature flags, candidate profile loading,
and safe operational defaults.
"""
import os
import json
import logging
from pathlib import Path
from dotenv import load_dotenv

# Base Directory Resolution
BASE_DIR = Path(__file__).resolve().parent

# Load environment variables from .env file
ENV_PATH = BASE_DIR / ".env"
if ENV_PATH.exists():
    load_dotenv(dotenv_path=ENV_PATH, override=True)
else:
    load_dotenv(override=True)

# ── Paths ─────────────────────────────────────────────────────────────────────
DATABASE_DIR = BASE_DIR / "database"
DATABASE_DIR.mkdir(exist_ok=True)
DB_PATH = str(DATABASE_DIR / "tracker.db")
CHECKPOINT_DB_PATH = str(DATABASE_DIR / "checkpoints.db")
BACKUP_DB_PATH = str(BASE_DIR / "backup_reference" / "tracker_reference_backup.db")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
PROFILE_PATH = str(DATA_DIR / "user_profile.json")
RESUME_PATH = str(DATA_DIR / "resume.pdf")
PDF_HR_PATH = str(DATA_DIR / "CompanyWise HR contact.pdf")

LOGS_DIR = BASE_DIR / "logs"
LOGS_DIR.mkdir(exist_ok=True)


# ── AI & Gemini Configuration ────────────────────────────────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-3.8-flash"
).strip()

GEMINI_MAX_RPM = int(os.getenv("GEMINI_MAX_RPM", 6))
GEMINI_DELAY_BETWEEN_CALLS = int(
    os.getenv("GEMINI_DELAY_BETWEEN_CALLS", 10)
)

GEMINI_MAX_RETRIES = int(os.getenv("GEMINI_MAX_RETRIES", 3))
GEMINI_TIMEOUT_SECONDS = float(
    os.getenv("GEMINI_TIMEOUT_SECONDS", 60.0)
)
GEMINI_BACKOFF_FACTOR = float(
    os.getenv("GEMINI_BACKOFF_FACTOR", 2.0)
)

# Only add fallback models after verifying API availability.
GEMINI_FALLBACK_MODELS = []


# ── Gmail Configuration ──────────────────────────────────────────────────────
GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "").strip()
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "").strip()
PERSONAL_EMAIL = os.getenv("PERSONAL_EMAIL", "").strip()

# ── Safety & Rate Limits ─────────────────────────────────────────────────────
def _parse_bool(val: str, default: bool = True) -> bool:
    if val is None:
        return default
    return str(val).lower() in ("true", "1", "yes", "y")

DRY_RUN = _parse_bool(os.getenv("DRY_RUN", "True"), default=True)
MOCK_MODE = _parse_bool(os.getenv("MOCK_MODE", "False"), default=False)
REQUIRE_HUMAN_APPROVAL = _parse_bool(os.getenv("REQUIRE_HUMAN_APPROVAL", "True"), default=True)

MAX_APPLY_PER_DAY = int(os.getenv("MAX_APPLY_PER_DAY", 30))
MAX_APPLICATIONS_PER_DAY = MAX_APPLY_PER_DAY
MAX_EMAIL_PER_DAY = int(os.getenv("MAX_EMAIL_PER_DAY", 25))
MAX_EMAILS_PER_DAY = MAX_EMAIL_PER_DAY
DAILY_DRIP_LIMIT = int(os.getenv("DAILY_DRIP_LIMIT", 50))
EMAIL_DELAY_SECONDS = int(os.getenv("EMAIL_DELAY_SECONDS", 20))
MATCH_SCORE_THRESHOLD = float(os.getenv("MATCH_SCORE_THRESHOLD", 7.0))

# ── Browser Automation & Form Action Delays (F4) ─────────────────────────────
BROWSER_MIN_DELAY = float(os.getenv("BROWSER_MIN_DELAY", 0.5))
BROWSER_MAX_DELAY = float(os.getenv("BROWSER_MAX_DELAY", 1.5))

# ── Embedding & Semantic Similarity Configuration (F3 Bonus) ────────────────
EMBEDDING_SCORING_ENABLED = _parse_bool(os.getenv("EMBEDDING_SCORING_ENABLED", "True"), default=True)
EMBEDDING_MODEL_NAME = os.getenv("EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2").strip()
EMBEDDING_WEIGHT = float(os.getenv("EMBEDDING_WEIGHT", "0.3"))
LLM_WEIGHT = float(os.getenv("LLM_WEIGHT", "0.7"))

# ── Scheduler & Timezone ─────────────────────────────────────────────────────
SCHEDULER_ENABLED = _parse_bool(os.getenv("SCHEDULER_ENABLED", "True"), default=True)
SCHEDULE_HOUR = int(os.getenv("SCHEDULE_HOUR", 9))
SCHEDULE_MINUTE = int(os.getenv("SCHEDULE_MINUTE", 0))
SCHEDULE_TIMEZONE = os.getenv("SCHEDULE_TIMEZONE", os.getenv("TIMEZONE", "Asia/Kolkata")).strip()
TIMEZONE = SCHEDULE_TIMEZONE

# ── Dashboard & Security ─────────────────────────────────────────────────────
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", 5000))
DASHBOARD_SECRET_KEY = os.getenv("DASHBOARD_SECRET_KEY", "").strip()
if not DASHBOARD_SECRET_KEY:
    raise RuntimeError(
        "DASHBOARD_SECRET_KEY is missing. Configure it in your .env file."
    )
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "").strip()

# ── External APIs (Graceful Degradation) ──────────────────────────────────────
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "").strip()
SNOV_CLIENT_ID = os.getenv("SNOV_CLIENT_ID", "").strip()
SNOV_CLIENT_SECRET = os.getenv("SNOV_CLIENT_SECRET", "").strip()
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "").strip()
TAVILY_DAILY_LIMIT = int(os.getenv("TAVILY_DAILY_LIMIT", 30))

# ── Memory System ────────────────────────────────────────────────────────────
MEM0_API_KEY = os.getenv("MEM0_API_KEY", "").strip()
MEM0_USER_ID = os.getenv("MEM0_USER_ID", "manoj_careerpilot_001").strip()

# ── Portal Login Credentials ─────────────────────────────────────────────────
LINKEDIN_EMAIL = os.getenv("LINKEDIN_EMAIL", "").strip()
LINKEDIN_PASSWORD = os.getenv("LINKEDIN_PASSWORD", "").strip()
NAUKRI_EMAIL = os.getenv("NAUKRI_EMAIL", "").strip()
NAUKRI_PASSWORD = os.getenv("NAUKRI_PASSWORD", "").strip()
BROWSER_HEADLESS = _parse_bool(os.getenv("BROWSER_HEADLESS", "True"), default=True)

# ── Feature Flags ────────────────────────────────────────────────────────────
USE_GEMINI = bool(GEMINI_API_KEY)
USE_GMAIL = bool(GMAIL_ADDRESS and GMAIL_APP_PASSWORD)
USE_APOLLO = bool(APOLLO_API_KEY)
USE_SNOV = bool(SNOV_CLIENT_ID and SNOV_CLIENT_SECRET)
USE_TAVILY = bool(TAVILY_API_KEY)
USE_MEM0 = bool(MEM0_API_KEY)
USE_LINKEDIN = bool(LINKEDIN_EMAIL and LINKEDIN_PASSWORD)
USE_NAUKRI = bool(NAUKRI_EMAIL and NAUKRI_PASSWORD)

# ── Candidate Profile Management ─────────────────────────────────────────────
_USER_PROFILE_CACHE = None

def load_user_profile(force_reload: bool = False) -> dict:
    """Load and cache the candidate profile from user_profile.json."""
    global _USER_PROFILE_CACHE
    if _USER_PROFILE_CACHE is None or force_reload:
        if Path(PROFILE_PATH).exists():
            try:
                with open(PROFILE_PATH, "r", encoding="utf-8") as f:
                    _USER_PROFILE_CACHE = json.load(f)
            except Exception as e:
                logging.getLogger("CareerPilot.Config").error(f"Failed to read user_profile.json: {e}")
                _USER_PROFILE_CACHE = {}
        else:
            _USER_PROFILE_CACHE = {}
    return _USER_PROFILE_CACHE



def save_user_profile(profile_data: dict) -> bool:
    """Save candidate profile data and refresh configuration safely."""
    global _USER_PROFILE_CACHE, CANDIDATE_PROFILE, CANDIDATE_NAME, TARGET_ROLES
    global TARGET_KEYWORDS, EXCLUDE_KEYWORDS, VERIFIED_SKILLS, PREFERRED_LOCATIONS
    global PERSONAL_EMAIL, PERSONAL_PHONE, RESUME_PATH
    global CANDIDATE_EDUCATION, CANDIDATE_PROJECTS
    global CANDIDATE_EXPERIENCE, CANDIDATE_CERTIFICATIONS

    try:
        if not isinstance(profile_data, dict):
            raise ValueError("Profile data must be a dictionary.")

        personal = profile_data.get("personal", {})
        if not isinstance(personal, dict):
            raise ValueError("Profile personal section must be a dictionary.")

        with open(PROFILE_PATH, "w", encoding="utf-8") as f:
            json.dump(profile_data, f, indent=2, ensure_ascii=False)

        _USER_PROFILE_CACHE = profile_data
        CANDIDATE_PROFILE = profile_data

        CANDIDATE_NAME = personal.get("name", "").strip()
        PERSONAL_EMAIL = (
            os.getenv("PERSONAL_EMAIL", "").strip()
            or personal.get("email", "").strip()
        )
        PERSONAL_PHONE = personal.get("phone", "").strip()

        TARGET_ROLES = profile_data.get("target_roles", [])
        TARGET_KEYWORDS = profile_data.get("target_keywords", [])
        EXCLUDE_KEYWORDS = profile_data.get("exclude_keywords", [])
        VERIFIED_SKILLS = profile_data.get("verified_skills", {})
        PREFERRED_LOCATIONS = profile_data.get("preferred_locations", ["Remote", "India"])

        RESUME_PATH = personal.get("resume_path", RESUME_PATH)

        CANDIDATE_EDUCATION = profile_data.get("education", {})
        CANDIDATE_PROJECTS = profile_data.get("projects", [])
        CANDIDATE_EXPERIENCE = profile_data.get("experience", [])
        CANDIDATE_CERTIFICATIONS = profile_data.get("certifications", [])

        return True

    except Exception as e:
        logging.getLogger("CareerPilot.Config").error(
            "Failed to save user profile: %s", e
        )
        return False



# Initial load of user profile
CANDIDATE_PROFILE = load_user_profile()

_personal = CANDIDATE_PROFILE.get("personal", {})

CANDIDATE_NAME = _personal.get("name", "").strip()

PERSONAL_EMAIL = (
    os.getenv("PERSONAL_EMAIL", "").strip()
    or _personal.get("email", "").strip()
)

PERSONAL_PHONE = _personal.get("phone", "").strip()

RESUME_PATH = _personal.get("resume_path", RESUME_PATH)

TARGET_ROLES = CANDIDATE_PROFILE.get("target_roles", [])
TARGET_KEYWORDS = CANDIDATE_PROFILE.get("target_keywords", [])
EXCLUDE_KEYWORDS = CANDIDATE_PROFILE.get("exclude_keywords", [])
VERIFIED_SKILLS = CANDIDATE_PROFILE.get("verified_skills", {})
PREFERRED_LOCATIONS = CANDIDATE_PROFILE.get("preferred_locations", ["Remote", "India"])

CANDIDATE_EDUCATION = CANDIDATE_PROFILE.get("education", {})
CANDIDATE_PROJECTS = CANDIDATE_PROFILE.get("projects", [])
CANDIDATE_EXPERIENCE = CANDIDATE_PROFILE.get("experience", [])
CANDIDATE_CERTIFICATIONS = CANDIDATE_PROFILE.get("certifications", [])

