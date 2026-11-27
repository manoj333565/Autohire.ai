"""
services/filter_service.py — Intelligent Filtering, Deduplication & Skills-Gap Detector (F2)
=============================================================================================
7-Stage Filtering & Deduplication Pipeline for CareerPilot AI:
  Stage 1: SHA-256 URL deduplication against SQLite `applications` table & intra-batch cache
  Stage 2: Title & keyword blacklist (irrelevant roles & tech stacks from DB/config)
  Stage 3: Java-heavy JD rejection rule (rejects JDs with Java >= 3 times without Python/AI context)
  Stage 4: Target-role & technology whitelist check (matches candidate target roles & AI/Python ecosystem)
  Stage 5: Seniority & experience exclusion (excludes 5+ years, senior, lead, director, principal)
  Stage 6: Location preference filtering (allows Remote, Hybrid, India tech hubs, or flexible)
  Stage 7: NLP-based Skills-Gap Detector (evaluates JD tech terms against candidate's verified skills)

Bonus:
  - Generates detailed skills-gap breakdown: matched skills, gap list, coverage percentage
  - Independent exception containment: a failure in any stage logs an alert and gracefully fails-safe
"""

import hashlib
import json
import re
import logging
from typing import List, Dict, Any, Tuple, Optional

logger = logging.getLogger("CareerPilot.FilterService")


# ---------------------------------------------------------------------------
# Default Configuration Constants (Fallbacks when DB is offline)
# ---------------------------------------------------------------------------

DEFAULT_TITLE_BLACKLIST = [
    "video editor", "graphic designer", "php developer", "php engineer",
    ".net developer", ".net engineer", "java developer", "java engineer",
    "java backend", "java backend developer", "java software",
    "android developer", "ios developer", "swift developer", "kotlin developer",
    "ruby on rails", "rails developer", "salesforce", "wordpress developer",
    "sap consultant", "mainframe", "cobol", "erp specialist", "blockchain developer",
    "game developer", "unity developer", "unreal engine", "embedded systems",
    "hardware engineer", "circuit design", "pcb", "electrical engineer",
    "mechanical engineer", "civil engineer", "accountant", "hr manager",
    "content writer", "seo specialist", "social media manager", "copywriter",
    "network engineer", "security analyst", "penetration tester",
    "devops engineer", "cloud engineer",
    "web designer", "ui/ux designer",
]

JAVA_REJECTION_THRESHOLD = 3
JAVA_EXEMPTION_KEYWORDS = [
    "python", "pytorch", "tensorflow", "langchain", "langgraph",
    "machine learning", "deep learning", "ai", "ml", "llm", "nlp",
    "generative ai", "hugging face", "transformers", "scikit-learn",
    "fastapi", "flask", "django",
]

DEFAULT_TARGET_ROLES = [
    "python developer", "data analyst", "data scientist", "machine learning engineer",
    "generative ai engineer", "ai engineer", "nlp engineer", "data science intern",
    "ai/ml intern", "backend python developer", "software engineer", "backend engineer",
    "ai researcher", "ml researcher"
]

DEFAULT_TECH_WHITELIST = [
    "python", "machine learning", "deep learning", "artificial intelligence",
    "natural language processing", "nlp", "computer vision", "data science",
    "data scientist", "ml engineer", "ai engineer", "llm", "large language",
    "generative ai", "genai", "langchain", "langgraph", "pytorch", "tensorflow",
    "scikit", "hugging face", "transformers", "rag", "retrieval augmented",
    "vector database", "embedding", "fastapi", "backend engineer", "backend developer",
    "api developer", "software engineer", "software developer", "full stack",
    "fullstack", "data engineer", "research engineer", "research scientist",
]

SENIORITY_PATTERNS = [
    r"\b(5\+?\s*years?|six\s+years?|7\+?\s*years?|8\+?\s*years?|10\+?\s*years?)\b",
    r"\bsenior\b", r"\bsr\.\s", r"\blead\s+(engineer|developer|scientist)\b",
    r"\bstaff\s+(engineer|developer|scientist)\b",
    r"\bprincipal\s+(engineer|developer|scientist)\b",
    r"\bdirector\s+of\b", r"\bvp\s+of\b", r"\bchief\s+(technology|data|ai)\b",
    r"\bhead\s+of\b",
]

DEFAULT_PREFERRED_LOCATIONS = [
    "remote", "work from home", "wfh", "hybrid", "anywhere", "worldwide",
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "noida",
    "gurgaon", "gurugram", "mumbai", "kolkata", "delhi", "chennai"
]

FALLBACK_CANDIDATE_SKILLS = [
    "python", "rag", "agentic ai", "vector databases", "langchain", "langgraph",
    "hugging face", "transformers", "fastapi", "docker", "pytorch", "tensorflow",
    "machine learning", "deep learning", "nlp", "flask", "sql", "sqlite",
    "git", "api development", "rest api",
]

SKILLS_GAP_PASS_THRESHOLD = 0.25  # 25% minimum overlap with candidate verified skills

KNOWN_TECH_SKILLS = [
    # Languages
    "python", "javascript", "typescript", "c++", "c#", "go", "golang", "rust", "r", "scala", "sql", "bash", "shell",
    # AI / ML / Data Science / NLP
    "machine learning", "deep learning", "artificial intelligence", "nlp", "natural language processing",
    "computer vision", "llm", "large language models", "generative ai", "genai", "rag", "retrieval augmented generation",
    "prompt engineering", "agentic ai", "autonomous agents", "langchain", "langgraph", "llama-index", "llamaindex",
    "hugging face", "transformers", "pytorch", "tensorflow", "keras", "scikit-learn", "scikit", "pandas", "numpy",
    "matplotlib", "seaborn", "opencv", "xgboost", "lightgbm", "random forest", "neural networks", "bert", "gpt",
    "fine-tuning", "lora", "diffusion", "embeddings", "vector embeddings", "shap", "explainable ai",
    # Data & Databases
    "data science", "data analysis", "data analytics", "data engineering", "vector database", "vector databases",
    "pinecone", "chroma", "chromadb", "weaviate", "milvus", "qdrant", "faiss", "postgresql", "postgres",
    "mysql", "sqlite", "mongodb", "redis", "elasticsearch", "neo4j", "snowflake", "bigquery", "power bi", "tableau",
    # Backend & Web
    "fastapi", "flask", "django", "rest api", "restful", "rest", "graphql", "microservices", "backend", "full stack",
    "fullstack", "api development", "html", "css", "react", "node.js", "nodejs", "express",
    # Cloud, DevOps & Tools
    "docker", "kubernetes", "aws", "gcp", "azure", "git", "github", "gitlab", "ci/cd", "linux", "cloud",
    "airflow", "spark", "kafka", "mlflow", "wandb", "dvc", "jupyter"
]


# ---------------------------------------------------------------------------
# Utility Functions
# ---------------------------------------------------------------------------

def compute_url_hash(url: str) -> str:
    """Return SHA-256 hex digest of stripped canonical job URL."""
    if not url:
        return ""
    clean = url.strip().split("?")[0].rstrip("/")
    return hashlib.sha256(clean.encode("utf-8")).hexdigest()


def extract_tech_keywords(text: str) -> List[str]:
    """Extract recognized technology and skill keywords from job text."""
    if not text:
        return []
    text_lower = text.lower()
    found_skills = []
    for skill in KNOWN_TECH_SKILLS:
        pattern = r"\b" + re.escape(skill) + r"\b"
        if re.search(pattern, text_lower):
            found_skills.append(skill)
    return found_skills


# ---------------------------------------------------------------------------
# Stage Filtering Functions
# ---------------------------------------------------------------------------

def stage1_url_deduplication(job: Dict[str, Any], existing_hashes: set) -> Tuple[bool, str]:
    """Stage 1: SHA-256 URL deduplication."""
    url = (job.get("job_url") or job.get("url") or "").strip()
    if not url:
        return False, "REJECT_NO_URL: Job has no URL"
    h = compute_url_hash(url)
    raw_h = hashlib.sha256(url.encode("utf-8")).hexdigest()
    if h in existing_hashes or raw_h in existing_hashes:
        return False, f"REJECT_DUPLICATE: URL already tracked (hash={h[:12]}...)"
    return True, f"PASS_UNIQUE: hash={h[:12]}..."


def stage2_title_blacklist(job: Dict[str, Any], blacklist: Optional[List[str]] = None) -> Tuple[bool, str]:
    """Stage 2: Reject jobs whose title contains a blacklisted keyword."""
    title = (job.get("title") or "").lower()
    kw_list = blacklist if blacklist is not None else DEFAULT_TITLE_BLACKLIST
    for kw in kw_list:
        if kw.lower() in title:
            return False, f"REJECT_BLACKLIST_TITLE: '{kw}' in title"
    return True, "PASS_TITLE_CLEAN"


def stage3_java_heavy_rejection(job: Dict[str, Any]) -> Tuple[bool, str]:
    """Stage 3: Reject JDs dominated by Java without Python/AI ecosystem context."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    combined = f"{title} {desc}"

    java_count = len(re.findall(r"\bjava\b", combined))
    if java_count >= JAVA_REJECTION_THRESHOLD:
        has_exemption = any(kw in combined for kw in JAVA_EXEMPTION_KEYWORDS)
        if not has_exemption:
            return False, f"REJECT_JAVA_HEAVY: 'java' appears {java_count}x without Python/AI context"
    return True, f"PASS_JAVA_CHECK: java_count={java_count}"


def stage4_whitelist_and_role_check(
    job: Dict[str, Any],
    target_roles: Optional[List[str]] = None,
    tech_whitelist: Optional[List[str]] = None
) -> Tuple[bool, str]:
    """Stage 4: Job must match candidate target roles or technology whitelist."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    combined = f"{title} {desc}"

    roles = target_roles or DEFAULT_TARGET_ROLES
    whitelist = tech_whitelist or DEFAULT_TECH_WHITELIST

    # Role title match
    matched_roles = [r for r in roles if r.lower() in title]
    # Whitelist tech match
    matched_tech = [t for t in whitelist if t.lower() in combined]

    if not matched_roles and not matched_tech:
        return False, "REJECT_NO_MATCH: Neither target role nor whitelist tech matched"

    reason_role = f"role={matched_roles[0]}" if matched_roles else ""
    reason_tech = f"tech={matched_tech[:3]}" if matched_tech else ""
    return True, f"PASS_ROLE_TECH: {reason_role} {reason_tech}".strip()


def stage5_seniority_exclusion(job: Dict[str, Any]) -> Tuple[bool, str]:
    """Stage 5: Exclude senior positions, 5+ years experience, and lead roles."""
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    exp_field = (job.get("experience") or "").lower()
    combined = f"{title} {desc} {exp_field}"

    for pattern in SENIORITY_PATTERNS:
        m = re.search(pattern, combined, re.IGNORECASE)
        if m:
            return False, f"REJECT_SENIOR: pattern '{pattern}' matched '{m.group()}'"
    return True, "PASS_SENIORITY_OK"


def stage6_location_preference(
    job: Dict[str, Any],
    preferred_locations: Optional[List[str]] = None
) -> Tuple[bool, str]:
    """
    Stage 6: Location filtering against preferred locations.
    Allows Remote, Hybrid, India tech hubs, or unassigned/worldwide locations.
    """
    loc = (job.get("location") or "").lower().strip()
    if not loc or loc in ["not specified", "unspecified", "worldwide", "anywhere"]:
        return True, "PASS_LOCATION: Flexible / Worldwide"

    loc_list = preferred_locations or DEFAULT_PREFERRED_LOCATIONS
    for ploc in loc_list:
        if ploc.lower() in loc:
            return True, f"PASS_LOCATION: Matched preferred location '{ploc}'"

    # Also check if title or description specifies remote
    title_desc = f"{job.get('title', '')} {job.get('description', '')}".lower()
    if "remote" in title_desc or "work from home" in title_desc:
        return True, "PASS_LOCATION: Remote/WFH mentioned in job description"

    return False, f"REJECT_LOCATION: '{loc}' does not match preferred locations"


def stage7_skills_gap_detector(
    job: Dict[str, Any],
    candidate_skills: List[str]
) -> Tuple[bool, str]:
    """
    Stage 7: NLP-Based Skills-Gap Detector.
    Extracts tech keywords from JD and checks coverage against candidate's verified skills.
    """
    title = (job.get("title") or "").lower()
    desc = (job.get("description") or "").lower()
    combined = f"{title} {desc}"

    jd_tokens = set(extract_tech_keywords(combined))
    candidate_set = {s.lower().strip() for s in candidate_skills}

    if not jd_tokens:
        # Check if candidate core skills appear in text
        direct_matches = [s for s in candidate_set if re.search(r"\b" + re.escape(s) + r"\b", combined)]
        if direct_matches:
            return True, f"PASS_SKILLS_GAP: Direct candidate skill matches found ({len(direct_matches)} skills)"
        return True, "PASS_SKILLS_GAP: General engineering role"

    matched_skills = set()
    for token in jd_tokens:
        for cskill in candidate_set:
            if token in cskill or cskill in token:
                matched_skills.add(token)
                break

    coverage = len(matched_skills) / len(jd_tokens)
    matched_top = sorted(matched_skills)[:5]

    # Qualified if candidate matches at least 25% of tech tokens or has >= 2 verified matched skills
    if coverage >= SKILLS_GAP_PASS_THRESHOLD or len(matched_skills) >= 2:
        return True, (
            f"PASS_SKILLS_GAP: coverage={coverage:.0%} "
            f"({len(matched_skills)}/{len(jd_tokens)}) matched={matched_top}"
        )
    else:
        return False, (
            f"REJECT_SKILLS_GAP: coverage={coverage:.0%} "
            f"({len(matched_skills)}/{len(jd_tokens)}) below {SKILLS_GAP_PASS_THRESHOLD:.0%} threshold"
        )


# ---------------------------------------------------------------------------
# FilterService Orchestrator Class
# ---------------------------------------------------------------------------

class FilterService:
    """
    Autonomous 7-stage job filtering, deduplication, and skills-gap analysis pipeline.
    """

    def __init__(self, db_manager=None):
        self.db = db_manager
        self._existing_hashes: set = set()
        self._candidate_skills: List[str] = []
        self._target_roles: List[str] = []
        self._blacklist_keywords: List[str] = []
        self._preferred_locations: List[str] = []
        self._stats: Dict[str, int] = {
            "total_input": 0,
            "passed": 0,
            "rejected_stage1": 0,
            "rejected_stage2": 0,
            "rejected_stage3": 0,
            "rejected_stage4": 0,
            "rejected_stage5": 0,
            "rejected_stage6": 0,
            "rejected_stage7": 0,
        }

    def _load_data_from_db(self):
        """Dynamically load deduplication hashes, candidate preferences, and verified skills from SQLite."""
        if self.db is None:
            if not self._candidate_skills:
                self._candidate_skills = list(FALLBACK_CANDIDATE_SKILLS)
            if not self._target_roles:
                self._target_roles = list(DEFAULT_TARGET_ROLES)
            if not self._blacklist_keywords:
                self._blacklist_keywords = list(DEFAULT_TITLE_BLACKLIST)
            if not self._preferred_locations:
                self._preferred_locations = list(DEFAULT_PREFERRED_LOCATIONS)
            return

        try:
            conn = self.db._get_conn()

            # 1. Load existing URL hashes from applications table
            rows = conn.execute("SELECT job_url FROM applications WHERE job_url IS NOT NULL").fetchall()
            self._existing_hashes = set()
            for r in rows:
                if r["job_url"]:
                    u = r["job_url"].strip()
                    self._existing_hashes.add(compute_url_hash(u))
                    clean_u = u.split("?")[0].rstrip("/")
                    self._existing_hashes.add(compute_url_hash(clean_u))
                    self._existing_hashes.add(hashlib.sha256(u.encode("utf-8")).hexdigest())
            logger.info(f"[FilterService] Loaded {len(self._existing_hashes)} deduplication hashes from DB")

            # 2. Load verified candidate skills from candidate_skills table
            skill_rows = conn.execute("SELECT skill_name FROM candidate_skills WHERE verified = 1").fetchall()
            if skill_rows:
                self._candidate_skills = [r["skill_name"] for r in skill_rows]
            else:
                self._candidate_skills = list(FALLBACK_CANDIDATE_SKILLS)
            logger.info(f"[FilterService] Loaded {len(self._candidate_skills)} verified candidate skills")

            # 3. Load preferences from candidate_preferences table
            pref_row = conn.execute("SELECT * FROM candidate_preferences LIMIT 1").fetchone()
            if pref_row:
                pref_dict = dict(pref_row)
                if pref_dict.get("target_roles"):
                    try:
                        self._target_roles = json.loads(pref_dict["target_roles"])
                    except Exception:
                        self._target_roles = list(DEFAULT_TARGET_ROLES)
                else:
                    self._target_roles = list(DEFAULT_TARGET_ROLES)

                if pref_dict.get("exclude_keywords"):
                    try:
                        db_exclude = json.loads(pref_dict["exclude_keywords"])
                        self._blacklist_keywords = list(set(DEFAULT_TITLE_BLACKLIST + [k.lower() for k in db_exclude]))
                    except Exception:
                        self._blacklist_keywords = list(DEFAULT_TITLE_BLACKLIST)
                else:
                    self._blacklist_keywords = list(DEFAULT_TITLE_BLACKLIST)

                if pref_dict.get("preferred_locations"):
                    try:
                        self._preferred_locations = json.loads(pref_dict["preferred_locations"])
                    except Exception:
                        self._preferred_locations = list(DEFAULT_PREFERRED_LOCATIONS)
                else:
                    self._preferred_locations = list(DEFAULT_PREFERRED_LOCATIONS)

                logger.info(f"[FilterService] Loaded candidate preferences: {len(self._target_roles)} roles, "
                            f"{len(self._preferred_locations)} locations")
            else:
                self._target_roles = list(DEFAULT_TARGET_ROLES)
                self._blacklist_keywords = list(DEFAULT_TITLE_BLACKLIST)
                self._preferred_locations = list(DEFAULT_PREFERRED_LOCATIONS)

        except Exception as e:
            logger.warning(f"[FilterService] Could not load DB preferences: {e}. Using resilient defaults.")
            self._existing_hashes = set()
            self._candidate_skills = list(FALLBACK_CANDIDATE_SKILLS)
            self._target_roles = list(DEFAULT_TARGET_ROLES)
            self._blacklist_keywords = list(DEFAULT_TITLE_BLACKLIST)
            self._preferred_locations = list(DEFAULT_PREFERRED_LOCATIONS)

    def run_pipeline(
        self,
        jobs: List[Dict[str, Any]],
        add_new_hashes: bool = True
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Execute the 7-stage filter pipeline on the provided list of normalized jobs.

        Returns:
            Tuple of (passed_jobs, rejected_jobs)
        """
        self._load_data_from_db()
        self._stats = {k: 0 for k in self._stats}
        self._stats["total_input"] = len(jobs)

        passed: List[Dict[str, Any]] = []
        rejected: List[Dict[str, Any]] = []

        stages = [
            (1, lambda j: stage1_url_deduplication(j, self._existing_hashes)),
            (2, lambda j: stage2_title_blacklist(j, self._blacklist_keywords)),
            (3, stage3_java_heavy_rejection),
            (4, lambda j: stage4_whitelist_and_role_check(j, self._target_roles, DEFAULT_TECH_WHITELIST)),
            (5, stage5_seniority_exclusion),
            (6, lambda j: stage6_location_preference(j, self._preferred_locations)),
            (7, lambda j: stage7_skills_gap_detector(j, self._candidate_skills)),
        ]

        for job in jobs:
            job_title = job.get("title", "Untitled Job")
            job_url = (job.get("job_url") or job.get("url") or "").strip()
            passes_all = True
            final_reason = ""

            for stage_num, stage_fn in stages:
                try:
                    ok, reason = stage_fn(job)
                except Exception as e:
                    logger.error(f"[FilterService] Stage {stage_num} exception for '{job_title}': {e}")
                    ok, reason = True, f"STAGE_{stage_num}_EXCEPTION_PASSED"

                if not ok:
                    self._stats[f"rejected_stage{stage_num}"] += 1
                    rej_job = dict(job)
                    rej_title = job.get("job_title") or job.get("title", "Untitled Job")
                    rej_comp = job.get("company_name") or job.get("company", "Unknown")
                    rej_src = job.get("source") or job.get("portal", "direct")
                    rej_job["job_title"] = rej_title
                    rej_job["company"] = rej_comp
                    rej_job["source"] = rej_src
                    rej_job["rejected_at_stage"] = stage_num
                    rej_job["rejection_reason"] = reason
                    rejected.append(rej_job)
                    logger.info(
                        f"[FilterService] Rejection at S{stage_num}: '{rej_title}' at '{rej_comp}' "
                        f"({rej_src}) -> {reason}"
                    )
                    passes_all = False
                    break
                else:
                    final_reason = reason

            if passes_all:
                passed_job = dict(job)
                # Compute and attach skills gap report
                passed_job["skills_gap_report"] = self.get_skills_gap_report(job)
                passed.append(passed_job)
                self._stats["passed"] += 1
                if add_new_hashes and job_url:
                    self._existing_hashes.add(compute_url_hash(job_url))

        logger.info(
            f"[FilterService] Processed {self._stats['total_input']} jobs -> {len(passed)} passed, {len(rejected)} rejected "
            f"(S1={self._stats['rejected_stage1']}, S2={self._stats['rejected_stage2']}, "
            f"S3={self._stats['rejected_stage3']}, S4={self._stats['rejected_stage4']}, "
            f"S5={self._stats['rejected_stage5']}, S6={self._stats['rejected_stage6']}, "
            f"S7={self._stats['rejected_stage7']})"
        )
        return passed, rejected

    def get_skills_gap_report(self, job: Dict[str, Any]) -> Dict[str, Any]:
        """Generate a structured skills-gap analysis report for a single job."""
        if not self._candidate_skills:
            self._load_data_from_db()

        title = (job.get("title") or "").lower()
        desc = (job.get("description") or "").lower()
        combined = f"{title} {desc}"

        jd_tokens = set(extract_tech_keywords(combined))
        candidate_set = {s.lower().strip() for s in self._candidate_skills}

        matched = set()
        missing = set()

        for token in jd_tokens:
            is_matched = any(token in cskill or cskill in token for cskill in candidate_set)
            if is_matched:
                matched.add(token)
            else:
                missing.add(token)

        coverage = len(matched) / len(jd_tokens) if jd_tokens else 1.0

        passes = coverage >= SKILLS_GAP_PASS_THRESHOLD or len(matched) >= 2
        return {
            "job_title": job.get("title"),
            "total_jd_keywords": len(jd_tokens),
            "matched_skills": sorted(matched),
            "missing_skills": sorted(missing)[:15],
            "coverage_pct": round(coverage * 100, 1),
            "passes_threshold": passes,
        }

    def get_stats(self) -> Dict[str, int]:
        """Return statistics of the most recent pipeline execution."""
        return dict(self._stats)
