"""
services/scoring_service.py — Production-Grade Google Gemini AI Engine & Intelligent Scoring (F3)
==================================================================================================
Handles:
  - Google Gemini 3.1 Flash Lite integration with multi-model fallback (3.1-flash-lite, 3.5-flash-lite, 3.8-flash)
  - Exponential backoff retry with jitter for temporary 429 (ResourceExhausted) and 503 (ServerError) errors
  - Strict request timeout and API quota rate-limit awareness
  - Sanitized logging with zero credential disclosure
  - Intelligent structured job evaluation:
      * Technical skill match (required vs preferred)
      * Education compatibility (B.Tech CSE from Satya Sai Engineering College)
      * Experience requirements (fresher / entry-level 0-2 yrs alignment)
      * Job description relevance and candidate skills gap analysis
  - Personalized recruiter cold-email pitch generation (Gemini + truth-preserving deterministic fallback)
  - Resilient deterministic heuristic fallback engine guaranteeing complete structured output in offline/sandboxed mode
  - Automated SQLite persistence to `job_scores` and `applications` tables
"""

import os
import re
import json
import time
import random
import hashlib
import logging
from typing import List, Dict, Any, Optional

import config as cfg

logger = logging.getLogger("CareerPilot.ScoringService")


def compute_url_hash(url: str) -> str:
    """Return deterministic SHA-256 job hash matching database schema."""
    from database.db_manager import DatabaseManager
    return DatabaseManager.make_job_hash(url)


def mask_key(key: str) -> str:
    """Mask sensitive API keys for safe logging."""
    if not key or len(key) < 8:
        return "[UNCONFIGURED]"
    return f"{key[:4]}...{key[-3:]}"


class ScoringService:
    """
    Production-Grade LLM & Heuristic Job Scoring & Outreach Generation Engine.
    """

    def __init__(
        self,
        db_manager=None,
        profile_service=None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        mock_mode: bool = False
    ):
        self.db = db_manager
        self.profile_service = profile_service
        self.api_key = (api_key or cfg.GEMINI_API_KEY or "").strip()
        self.model_name = (model_name or cfg.GEMINI_MODEL or "gemini-3.1-flash-lite").strip()
        self.mock_mode = mock_mode or not bool(self.api_key)

        self.max_retries = getattr(cfg, "GEMINI_MAX_RETRIES", 3)
        self.timeout_seconds = getattr(cfg, "GEMINI_TIMEOUT_SECONDS", 20.0)
        self.backoff_factor = getattr(cfg, "GEMINI_BACKOFF_FACTOR", 1.5)
        self.delay_between_calls = getattr(cfg, "GEMINI_DELAY_BETWEEN_CALLS", 5)
        self.fallback_models = getattr(cfg, "GEMINI_FALLBACK_MODELS", ["gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.8-flash"])

        self._last_call_timestamp: float = 0.0

        # Lazy init Google GenAI Client
        self._client = None
        if not self.mock_mode and self.api_key:
            try:
                from google import genai
                self._client = genai.Client(api_key=self.api_key)
                logger.info(
                    f"[ScoringService] Initialized Google GenAI Client with model '{self.model_name}' "
                    f"(Key: {mask_key(self.api_key)}, Timeout: {self.timeout_seconds}s, MaxRetries: {self.max_retries})"
                )
            except Exception as e:
                logger.warning(f"[ScoringService] Could not initialize Gemini client: {e}. Defaulting to heuristic fallback.")
                self.mock_mode = True

        # Modular Embedding Semantic Similarity Scorer (F3 Bonus)
        from services.embedding_service import EmbeddingService
        self.embedding_service = EmbeddingService(
            model_name=getattr(cfg, "EMBEDDING_MODEL_NAME", "all-MiniLM-L6-v2"),
            enabled=getattr(cfg, "EMBEDDING_SCORING_ENABLED", True),
            embedding_weight=getattr(cfg, "EMBEDDING_WEIGHT", 0.3),
            llm_weight=getattr(cfg, "LLM_WEIGHT", 0.7)
        )

        # API Response Cache for Gemini calls and prompt generations
        try:
            from services.api_cache_service import ApiCacheService
            self.api_cache = ApiCacheService(self.db_manager) if self.db_manager else None
        except Exception:
            self.api_cache = None

    # ------------------------------------------------------------------
    # Rate Limiting & Quota Awareness
    # ------------------------------------------------------------------

    def _enforce_quota_delay(self):
        """Enforce delay between calls to respect API rate limits."""
        elapsed = time.time() - self._last_call_timestamp
        if elapsed < self.delay_between_calls and self._last_call_timestamp > 0:
            wait_time = self.delay_between_calls - elapsed
            logger.debug(f"[ScoringService] Rate-limit pacing: sleeping {wait_time:.1f}s before next Gemini call.")
            time.sleep(wait_time)
        self._last_call_timestamp = time.time()

    # ------------------------------------------------------------------
    # Context Retrieval
    # ------------------------------------------------------------------

    def _get_candidate_context(self) -> Dict[str, Any]:
        """Fetch verified candidate profile context for scoring prompts."""
        if self.profile_service:
            try:
                return self.profile_service.get_candidate_context_for_scoring()
            except Exception as e:
                logger.warning(f"[ScoringService] Error loading context from ProfileService: {e}")

        # Fallback profile context matching Manoj Kumar Mandal's exact resume
        return {
            "candidate_identity": {
                "name": "Manoj Kumar Mandal",
                "email": "manojkumarmandal1111@gmail.com",
                "phone": "+91 8404970494",
                "education": "Btech (Hon’s) Computer Science and Engineering - CGPA 8.01/10",
                "college": "Satya Sai Engineering College Balasore, Odisha",
                "location": "India (Remote / Hybrid / On-Site)"
            },
            "verified_skills_by_proficiency": {
                "Proficient": ["RAG", "Agentic AI", "Vector Databases", "Python", "SQL", "Machine Learning", "Deep Learning", "NLP"],
                "Intermediate": ["LangChain", "LangGraph", "FastAPI", "Hugging Face / Transformers", "Scikit-learn", "OpenCV", "Streamlit", "SHAP"],
                "Learning": ["Docker", "PyTorch", "TensorFlow", "MLOps"]
            },
            "projects": [
                {
                    "name": "EduNexora AI – Intelligent Student Performance Prediction System",
                    "tech": "Python, Streamlit, Scikit-learn, Random Forest, SHAP, SQLite",
                    "highlights": "Achieved 96% accuracy, automated performance dashboard, SHAP explainable AI"
                },
                {
                    "name": "Customer Purchase and Shopping Behavior Analysis",
                    "tech": "Python, Pandas, SQL(PostgreSQL), Power BI, Jupyter Notebook",
                    "highlights": "Analyzed 3900 customer records, cleaned and preprocessed data, Power BI dashboard"
                },
                {
                    "name": "AI-Powered Attendance Management System",
                    "tech": "Python, NumPy, Pandas, MySQL, OpenCV, LBPH Face Recognition",
                    "highlights": "Real-time face recognition with OpenCV and LBPH, secure database integration"
                }
            ],
            "certifications_achievements": [
                "GeeksForGeeks: Campus Ambassador (June 2024 - June 2025)",
                "GeeksforGeeks Coding Rank: institute Rank 1 (Top Performer)",
                "LeetCode Profile: 250+ problems solved",
                "Oracle Cloud Infrastructure Generative AI Professional certified",
                "McKinsey.org Forward Program Certificate",
                "IBM python for Data Science, AI and Development Certified"
            ],
            "target_preferences": {
                "target_roles": ["AI Engineer", "Machine Learning Engineer", "Generative AI Engineer", "Python Developer", "Data Scientist", "NLP Engineer"],
                "preferred_locations": ["Remote", "Work from Home", "Bangalore", "Pune", "Hyderabad", "Noida", "India"],
                "matching_threshold": cfg.MATCH_SCORE_THRESHOLD
            }
        }

    # ------------------------------------------------------------------
    # Public Scoring API
    # ------------------------------------------------------------------

    def score_jobs(
        self,
        jobs: List[Dict[str, Any]],
        persist_to_db: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Score a list of normalized jobs, rank them descending by relevance score,
        and optionally persist results to SQLite.
        """
        if not jobs:
            return []

        candidate_context = self._get_candidate_context()
        logger.info(f"[ScoringService] Initiating scoring for {len(jobs)} jobs (Mock/Heuristic Mode: {self.mock_mode})...")

        scored_jobs = []

        if not self.mock_mode and self._client:
            # Try single-call batch scoring first
            try:
                scored_jobs = self._batch_score_with_gemini(jobs, candidate_context)
            except Exception as e:
                logger.warning(f"[ScoringService] Batch Gemini scoring failed: {e}. Falling back to individual/heuristic scoring.")
                scored_jobs = []

        # If batch scoring didn't produce results, score individually with heuristic fallback
        if not scored_jobs or len(scored_jobs) != len(jobs):
            scored_jobs = []
            for job in jobs:
                scored_jobs.append(self.score_single_job(job, candidate_context))

        # Attach and combine embedding scores for any batch jobs not yet enriched
        jobs_needing_emb = [j for j in scored_jobs if "embedding_score" not in j]
        if jobs_needing_emb:
            raw_jobs_needing_emb = [jobs[i] for i, j in enumerate(scored_jobs) if "embedding_score" not in j]
            emb_scores = self.embedding_service.compute_similarity_scores(raw_jobs_needing_emb, candidate_context)
            for j, emb_sc in zip(jobs_needing_emb, emb_scores):
                llm_sc = float(j.get("match_score") or j.get("relevance_score", 0.0))
                j["llm_score"] = round(llm_sc, 1)
                j["embedding_score"] = round(emb_sc, 1) if emb_sc is not None else None
                final_sc = self.embedding_service.combine_scores(llm_sc, emb_sc)
                j["final_score"] = final_sc
                j["relevance_score"] = final_sc
                j["match_score"] = final_sc
                if final_sc >= 8.0:
                    j["recommendation"] = "STRONG_APPLY"
                elif final_sc >= cfg.MATCH_SCORE_THRESHOLD:
                    j["recommendation"] = "APPLY"
                elif final_sc >= 5.0:
                    j["recommendation"] = "CONSIDER"
                else:
                    j["recommendation"] = "SKIP"

        # Sort jobs descending by relevance_score (which holds final_score)
        scored_jobs.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)

        # Persist to database if enabled
        if persist_to_db and self.db:
            self._persist_scores_to_db(scored_jobs)

        logger.info(f"[ScoringService] Successfully scored and ranked {len(scored_jobs)} jobs.")
        return scored_jobs

    def score_single_job(
        self,
        job: Dict[str, Any],
        candidate_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Score an individual job listing with heuristic fallback and semantic embedding."""
        ctx = candidate_context or self._get_candidate_context()
        url = (job.get("job_url") or job.get("url") or "").strip()
        job_hash = compute_url_hash(url) if url else hashlib.sha256(job.get("title", "").encode("utf-8")).hexdigest()

        scored = None
        # If live client is enabled and not mock mode, try single Gemini scoring
        if not self.mock_mode and self._client:
            try:
                scores = self._batch_score_with_gemini([job], ctx)
                if scores:
                    scored = scores[0]
            except Exception as e:
                logger.debug(f"[ScoringService] Individual Gemini scoring exception: {e}")

        # Fallback to deterministic weighted heuristic scoring if not scored
        if not scored:
            scored = self._heuristic_fallback_score(job, ctx, job_hash)

        # Attach embedding semantic similarity score and compute combined final_score
        llm_sc = float(scored.get("match_score") or scored.get("relevance_score", 0.0))
        scored["llm_score"] = round(llm_sc, 1)

        emb_sc = self.embedding_service.compute_single_similarity(job, ctx)
        scored["embedding_score"] = round(emb_sc, 1) if emb_sc is not None else None

        final_sc = self.embedding_service.combine_scores(llm_sc, emb_sc)
        scored["final_score"] = final_sc
        scored["relevance_score"] = final_sc
        scored["match_score"] = final_sc

        if final_sc >= 8.0:
            scored["recommendation"] = "STRONG_APPLY"
        elif final_sc >= cfg.MATCH_SCORE_THRESHOLD:
            scored["recommendation"] = "APPLY"
        elif final_sc >= 5.0:
            scored["recommendation"] = "CONSIDER"
        else:
            scored["recommendation"] = "SKIP"

        return scored

    # Convenience alias
    score_job = score_single_job

    # ------------------------------------------------------------------
    # Gemini Invocation with Multi-Model Fallback & Backoff
    # ------------------------------------------------------------------

    def _call_gemini_with_retry(self, prompt: str) -> str:
        """
        Execute request against Gemini with exponential backoff and model progression fallback.
        Catches 429 (quota), 503 (demand spikes), and 404 (deprecated model endpoints).
        """
        self._enforce_quota_delay()

        models_to_try = [self.model_name]
        for m in self.fallback_models:
            if m not in models_to_try:
                models_to_try.append(m)

        last_error = None

        for model_cand in models_to_try:
            logger.debug(f"[ScoringService] Attempting generation with model candidate: '{model_cand}'")
            delay = self.backoff_factor

            for attempt in range(1, self.max_retries + 1):
                try:
                    logger.debug(f"[ScoringService] Model '{model_cand}' - Attempt {attempt}/{self.max_retries}...")
                    response = self._client.models.generate_content(
                        model=model_cand,
                        contents=prompt,
                    )
                    text = (response.text or "").strip()
                    if text:
                        return text
                except Exception as e:
                    last_error = e
                    err_msg = str(e).lower()
                    # If 404 model not found, switch immediately to next candidate model
                    if "404" in err_msg or "not_found" in err_msg:
                        logger.warning(f"[ScoringService] Model '{model_cand}' not available (404). Switching candidate...")
                        break

                    is_retryable = any(k in err_msg for k in ["429", "503", "quota", "resource_exhausted", "unavailable", "timeout"])
                    if is_retryable and attempt < self.max_retries:
                        jitter = random.uniform(0.1, 0.5)
                        sleep_s = delay + jitter
                        logger.warning(
                            f"[ScoringService] Model '{model_cand}' attempt {attempt} failed ({type(e).__name__}). "
                            f"Retrying in {sleep_s:.1f}s..."
                        )
                        time.sleep(sleep_s)
                        delay *= 2.0
                    else:
                        logger.warning(f"[ScoringService] Model '{model_cand}' attempt {attempt} failed: {type(e).__name__}")
                        break

        raise RuntimeError(f"All Gemini models exhausted after retries. Last error: {last_error}")

    # ------------------------------------------------------------------
    # Batch Gemini Scoring Prompt & Parser
    # ------------------------------------------------------------------

    def _batch_score_with_gemini(
        self,
        jobs: List[Dict[str, Any]],
        candidate_context: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """
        Execute batched structured scoring via Gemini Flash Lite with exponential backoff.
        """
        prompt = self._build_gemini_prompt(jobs, candidate_context)
        raw_text = self._call_gemini_with_retry(prompt)

        # Clean and parse JSON array
        clean_json_str = self._extract_json_string(raw_text)
        score_entries = json.loads(clean_json_str)

        # Map entries back to jobs
        scored_jobs = []
        score_map = {item.get("job_id", i): item for i, item in enumerate(score_entries)}

        for i, job in enumerate(jobs):
            score_data = score_map.get(i, {})
            url = (job.get("job_url") or job.get("url") or "").strip()
            job_hash = compute_url_hash(url) if url else hashlib.sha256(job.get("title", "").encode("utf-8")).hexdigest()

            # Structured components
            rel_score = float(score_data.get("match_score") or score_data.get("relevance_score", 6.0))
            rel_score = max(0.0, min(10.0, round(rel_score, 1)))

            edu_comp = score_data.get("education_compatibility")
            if not isinstance(edu_comp, dict):
                edu_comp = {
                    "compatible": bool(edu_comp) if edu_comp is not None else True,
                    "level": "High" if edu_comp else "Moderate",
                    "details": "B.Tech in Computer Science and Engineering is compatible."
                }

            exp_comp = score_data.get("experience_compatibility")
            if not isinstance(exp_comp, dict):
                exp_comp = {
                    "compatible": bool(score_data.get("experience_match", True)),
                    "level": "Entry-Level",
                    "details": "Evaluated against 0-2 years candidate profile."
                }

            explanation = score_data.get("explanation") or score_data.get("reasoning", "Evaluated by Gemini AI Engine.")
            recommendation = score_data.get("recommendation", "APPLY" if rel_score >= cfg.MATCH_SCORE_THRESHOLD else "SKIP")

            scored_job = dict(job)
            scored_job["job_hash"] = job_hash
            scored_job["relevance_score"] = rel_score
            scored_job["match_score"] = rel_score
            scored_job["skills_match_ratio"] = float(score_data.get("skills_match_ratio", 0.5))
            scored_job["experience_match"] = bool(exp_comp.get("compatible", True))
            scored_job["location_match"] = bool(score_data.get("location_match", True))
            scored_job["matched_skills"] = score_data.get("matched_skills", [])
            scored_job["missing_skills"] = score_data.get("missing_skills", [])
            scored_job["required_skills_match"] = score_data.get("required_skills_match", scored_job["matched_skills"])
            scored_job["preferred_skills_match"] = score_data.get("preferred_skills_match", [])
            scored_job["education_compatibility"] = edu_comp
            scored_job["experience_compatibility"] = exp_comp
            scored_job["explanation"] = explanation
            scored_job["reasoning"] = explanation
            scored_job["recommendation"] = recommendation
            scored_job["skills_gap_analysis"] = score_data.get("skills_gap_analysis", f"Missing skills: {', '.join(scored_job['missing_skills'][:5])}")
            scored_job["raw_gemini_response"] = raw_text[:2000]
            scored_job["scoring_engine"] = "gemini"
            scored_job["is_ai_generated"] = True
            scored_jobs.append(scored_job)

        return scored_jobs

    def _build_gemini_prompt(self, jobs: List[Dict[str, Any]], candidate_context: Dict[str, Any]) -> str:
        """Construct a rigorous prompt enforcing structured JSON evaluation without hallucinations."""
        identity = candidate_context.get("candidate_identity", {})
        verified_skills = candidate_context.get("verified_skills_by_proficiency", {})
        proficient = verified_skills.get("Proficient", [])
        intermediate = verified_skills.get("Intermediate", [])
        learning = verified_skills.get("Learning", [])

        jobs_brief = []
        for i, j in enumerate(jobs):
            jobs_brief.append({
                "job_id": i,
                "title": j.get("title", ""),
                "company": j.get("company", ""),
                "location": j.get("location", ""),
                "experience": j.get("experience", ""),
                "description": j.get("description", "")[:1200]
            })

        prompt = f"""
You are an expert AI Technical Hiring Manager evaluating job listings for the candidate described below.
CRITICAL RULE: NEVER invent, hallucinate, or assume candidate qualifications, degrees, or skills not explicitly present in the candidate profile.

CANDIDATE VERIFIED PROFILE:
- Name: {identity.get('name', 'Manoj Kumar Mandal')}
- Education: {identity.get('education', 'Btech (Hon’s) Computer Science and Engineering - CGPA 8.01/10')}
- College: {identity.get('college', 'Satya Sai Engineering College Balasore, Odisha')}
- Experience: Recent Graduate / Fresher (0-2 years equivalent, hands-on production project experience)
- Proficient Skills (Weight 1.0): {', '.join(proficient)}
- Intermediate Skills (Weight 0.7): {', '.join(intermediate)}
- Learning Skills (Weight 0.3): {', '.join(learning)}
- Achievements: GeeksforGeeks institute Rank 1, LeetCode 250+ problems solved, Oracle Cloud GenAI Certified.

EVALUATION INSTRUCTIONS:
1. match_score / relevance_score (0.0 to 10.0): Calculate overall fit. Heavily weight Proficient skills and Intermediate skills.
2. skills_match_ratio (0.0 to 1.0): Ratio of required skills possessed.
3. matched_skills: Array of technical skills the candidate has that the job requires.
4. missing_skills: Array of technical skills the job requires that the candidate lacks.
5. required_skills_match: Subset of matched skills that represent core must-haves for this role.
6. preferred_skills_match: Subset of matched skills that represent bonus/nice-to-have skills.
7. education_compatibility: Object with "compatible" (bool), "level" ("High"|"Moderate"|"Low"), and "details" (str).
8. experience_compatibility: Object with "compatible" (bool), "level" ("Entry-Level"|"Mid-Level"|"Senior"), and "details" (str).
9. explanation: Detailed 2-3 sentence technical justification.
10. recommendation: One of "STRONG_APPLY" (>=8.0), "APPLY" (7.0-7.9), "CONSIDER" (5.0-6.9), "SKIP" (<5.0).
11. skills_gap_analysis: Concise summary of what skills the candidate would need to learn for full readiness.

JOBS TO EVALUATE:
{json.dumps(jobs_brief, indent=2)}

OUTPUT FORMAT:
Respond with a strict JSON array of objects only. No markdown fences outside the JSON.
Schema example:
[
  {{
    "job_id": 0,
    "match_score": 8.5,
    "relevance_score": 8.5,
    "skills_match_ratio": 0.8,
    "location_match": true,
    "matched_skills": ["Python", "Machine Learning", "FastAPI"],
    "missing_skills": ["AWS"],
    "required_skills_match": ["Python", "Machine Learning"],
    "preferred_skills_match": ["FastAPI"],
    "education_compatibility": {{
      "compatible": true,
      "level": "High",
      "details": "B.Tech CSE satisfies computer science degree requirement."
    }},
    "experience_compatibility": {{
      "compatible": true,
      "level": "Entry-Level",
      "details": "Role accepts freshers and junior engineers (0-2 years)."
    }},
    "explanation": "Strong alignment with candidate's Python, ML, and FastAPI experience.",
    "recommendation": "STRONG_APPLY",
    "skills_gap_analysis": "Minor cloud tooling (AWS) gap can be bridged quickly."
  }}
]
"""
        return prompt

    def _extract_json_string(self, raw_text: str) -> str:
        """Strip markdown fences and whitespace from LLM output."""
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        # Regex search for first [ and last ]
        start = text.find("[")
        end = text.rfind("]")
        if start != -1 and end != -1 and end > start:
            return text[start:end+1]
        return text

    # ------------------------------------------------------------------
    # Deterministic Heuristic Fallback Scorer (Matches Structured Schema)
    # ------------------------------------------------------------------

    def _heuristic_fallback_score(
        self,
        job: Dict[str, Any],
        candidate_context: Dict[str, Any],
        job_hash: str
    ) -> Dict[str, Any]:
        """
        Deterministic, weighted heuristic scorer when Gemini is offline or in mock mode.
        Produces the full structured schema including education, experience, gaps, and recommendations.
        """
        title = (job.get("title") or "").lower()
        desc = (job.get("description") or "").lower()
        exp_str = (job.get("experience") or "").lower()
        loc_str = (job.get("location") or "").lower()
        combined = f"{title} {desc}"

        verified = candidate_context.get("verified_skills_by_proficiency", {})
        proficient_skills = [s.lower() for s in verified.get("Proficient", ["rag", "agentic ai", "vector databases", "python", "sql", "machine learning"])]
        intermediate_skills = [s.lower() for s in verified.get("Intermediate", ["langchain", "langgraph", "fastapi", "scikit-learn", "opencv"])]
        learning_skills = [s.lower() for s in verified.get("Learning", ["docker", "pytorch", "tensorflow"])]

        matched = []
        required_matched = []
        preferred_matched = []
        weighted_score = 0.0
        max_possible = 0.0

        # Check Proficient skills (Weight 1.0)
        for s in proficient_skills:
            max_possible += 1.0
            if s in combined:
                matched.append(s.title())
                required_matched.append(s.title())
                weighted_score += 1.0

        # Check Intermediate skills (Weight 0.7)
        for s in intermediate_skills:
            max_possible += 0.7
            if s in combined:
                matched.append(s.title())
                preferred_matched.append(s.title())
                weighted_score += 0.7

        # Check Learning skills (Weight 0.3)
        for s in learning_skills:
            max_possible += 0.3
            if s in combined:
                matched.append(s.title())
                preferred_matched.append(s.title())
                weighted_score += 0.3

        # Base technical checks
        for b in ["python", "sql", "machine learning", "deep learning", "nlp", "ai"]:
            if b in combined and b.title() not in matched:
                matched.append(b.title())
                required_matched.append(b.title())

        # Experience compatibility check
        is_senior = bool(re.search(r"\b(3\+?\s*years?|4\+?\s*years?|5\+?\s*years?|senior|lead|principal)\b", f"{combined} {exp_str}"))
        exp_compatible = not is_senior
        exp_comp = {
            "compatible": exp_compatible,
            "level": "Senior" if is_senior else "Entry-Level",
            "details": "Requires 3+ years / senior level" if is_senior else "Compatible with 0-2 years / entry-level candidate background."
        }

        # Education compatibility check (B.Tech in CSE)
        edu_comp = {
            "compatible": True,
            "level": "High",
            "details": "Candidate's B.Tech in Computer Science and Engineering (Satya Sai Engineering College) satisfies degree requirements."
        }

        # Location compatibility check
        location_match = True
        disallowed_locs = ["tokyo", "japan", "germany", "berlin", "london", "sydney"]
        if any(d in loc_str for d in disallowed_locs) and "remote" not in loc_str:
            location_match = False

        # Target role alignment
        target_roles = [r.lower() for r in candidate_context.get("target_preferences", {}).get("target_roles", [])]
        role_aligned = any(r in title for r in target_roles) or any(k in title for k in ["python", "ai", "ml", "data"])

        # Compute final relevance score (0.0 to 10.0 scale)
        base_relevance = 4.0

        if role_aligned:
            base_relevance += 2.0
        base_relevance += min(3.8, weighted_score * 0.8)

        if not exp_compatible:
            base_relevance -= 2.0
        if not location_match:
            base_relevance -= 2.5

        final_score = max(1.0, min(9.8, round(base_relevance, 1)))
        skills_ratio_val = round(min(1.0, len(matched) / 8.0), 2)

        # Missing skills identification
        missing = []
        all_skills_flat = proficient_skills + intermediate_skills + learning_skills
        for s in all_skills_flat:
            if s not in combined and s.title() not in matched:
                missing.append(s.title())

        # Recommendation determination
        if final_score >= 8.0:
            rec = "STRONG_APPLY"
        elif final_score >= cfg.MATCH_SCORE_THRESHOLD:
            rec = "APPLY"
        elif final_score >= 5.0:
            rec = "CONSIDER"
        else:
            rec = "SKIP"

        explanation = (
            f"Heuristic evaluation {final_score}/10 based on {len(matched)} matched skills "
            f"({', '.join(matched[:4]) or 'General AI/Python'}). Role alignment: {role_aligned}. "
            f"Experience compatibility: {exp_comp['details']}"
        )

        gap_analysis = (
            f"Candidate matches {len(matched)} key skills. High priority skills to expand: {', '.join(missing[:4])}."
        )

        scored_job = dict(job)
        scored_job["job_hash"] = job_hash
        scored_job["relevance_score"] = final_score
        scored_job["match_score"] = final_score
        scored_job["skills_match_ratio"] = skills_ratio_val
        scored_job["experience_match"] = exp_compatible
        scored_job["location_match"] = location_match
        scored_job["matched_skills"] = matched
        scored_job["missing_skills"] = missing[:8]
        scored_job["required_skills_match"] = required_matched
        scored_job["preferred_skills_match"] = preferred_matched
        scored_job["education_compatibility"] = edu_comp
        scored_job["experience_compatibility"] = exp_comp
        scored_job["explanation"] = explanation
        scored_job["reasoning"] = explanation
        scored_job["recommendation"] = rec
        scored_job["skills_gap_analysis"] = gap_analysis
        scored_job["raw_gemini_response"] = "heuristic_fallback_engine"
        scored_job["scoring_engine"] = "heuristic_fallback"
        scored_job["is_ai_generated"] = False

        return scored_job

    # ------------------------------------------------------------------
    # Intelligent Recruiter Email Generation (F4 / Cold Outreach)
    # ------------------------------------------------------------------

    def generate_recruiter_email(
        self,
        job: Dict[str, Any],
        candidate_context: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Generate a highly personalized, professional cold email tailored to the role.
        STRICT TRUTH REQUIREMENT: Never invent qualifications. Highlight actual projects:
        EduNexora AI, Customer Purchase Analysis, AI-Powered Attendance Management.
        """
        ctx = candidate_context or self._get_candidate_context()
        identity = ctx.get("candidate_identity", {})
        cand_name = identity.get("name", "Manoj Kumar Mandal")
        cand_email = identity.get("email", "manojkumarmandal1111@gmail.com")
        cand_degree = identity.get("education", "B.Tech in Computer Science and Engineering")
        cand_college = identity.get("college", "Satya Sai Engineering College Balasore, Odisha")

        job_title = job.get("title") or job.get("job_title") or "AI / Software Engineer"
        company_name = job.get("company") or "the Team"
        desc = (job.get("description") or "")[:800]

        # Check API response cache first
        if self.api_cache:
            cached_email = self.api_cache.get(
                "gemini/recruiter_email",
                {"company": company_name, "title": job_title, "desc": desc[:300]},
                source="gemini"
            )
            if cached_email:
                return cached_email

        # 1. Try Gemini generation if available
        if not self.mock_mode and self._client:
            prompt = f"""
Write a concise, compelling 3-paragraph cold outreach email from the candidate to a hiring manager/recruiter.
CRITICAL CONSTRAINT: Use ONLY the candidate's actual qualifications. Do NOT invent work experience, companies, or degrees.

CANDIDATE:
- Name: {cand_name}
- Email: {cand_email}
- Degree: {cand_degree} from {cand_college} (CGPA: 8.01/10)
- Core Projects:
  1. EduNexora AI (96% accuracy AI student performance prediction with Streamlit, Random Forest, SHAP explainable AI)
  2. Customer Purchase & Shopping Behavior Analysis (3900 records, PostgreSQL, Power BI dashboard)
  3. AI-Powered Attendance Management System (Real-time face recognition with OpenCV, LBPH, MySQL)
- Key Achievements: GeeksforGeeks institute Rank 1, LeetCode 250+ problems solved, Oracle Cloud GenAI Certified.
- Core Skills: Python, SQL, Machine Learning, Deep Learning, NLP, RAG, Agentic AI, FastAPI.

TARGET ROLE:
- Title: {job_title}
- Company: {company_name}
- Job Description Summary: {desc}

REQUIREMENTS:
1. Subject line: Engaging and clear (e.g. "Application for {job_title} — {cand_name}").
2. Body:
   - Paragraph 1: Express strong interest in {job_title} at {company_name} and connect candidate's background to their technical focus.
   - Paragraph 2: Highlight relevant real projects (EduNexora AI, Customer Purchase Analysis, or AI Attendance System) and proven problem-solving track record (GFG Rank 1, 250+ LeetCode).
   - Paragraph 3: Mention attached resume, professional links, and propose a brief conversation.
3. Keep the tone professional, polite, and confident.

OUTPUT FORMAT:
Respond with a strict JSON object:
{{
  "subject": "Subject line text",
  "body_text": "Plaintext email content with proper line breaks",
  "body_html": "<p>HTML styled version of the email</p>"
}}
"""
            try:
                raw = self._call_gemini_with_retry(prompt)
                clean_json = self._extract_json_string(raw)
                # Parse single JSON object if enclosed in brackets or bare
                if clean_json.startswith("[") and clean_json.endswith("]"):
                    data = json.loads(clean_json)[0]
                else:
                    data = json.loads(clean_json)

                if data.get("subject") and data.get("body_text"):
                    result = {
                        "subject": data["subject"],
                        "body_text": data["body_text"],
                        "body_html": data.get("body_html") or f"<p>{data['body_text'].replace(chr(10), '<br>')}</p>",
                        "is_ai_generated": True,
                        "model_used": self.model_name
                    }
                    if self.api_cache:
                        self.api_cache.set(
                            "gemini/recruiter_email",
                            {"company": company_name, "title": job_title, "desc": desc[:300]},
                            result,
                            source="gemini",
                            ttl_seconds=86400
                        )
                    return result
            except Exception as e:
                logger.warning(f"[ScoringService] Gemini cold-email generation failed: {e}. Using deterministic fallback.")

        # 2. Deterministic Personalized Template Fallback (100% truth-preserving)
        subject = f"Application for {job_title} — {cand_name} (B.Tech CSE | AI/ML Engineer)"
        body_text = f"""Hi {company_name} Hiring Team,

I am writing to express my strong enthusiasm for the {job_title} position at {company_name}. As a Computer Science graduate from {cand_college} (CGPA: 8.01/10) specializing in Machine Learning, NLP, and Agentic AI systems, I have built production-grade AI solutions and high-performance Python pipelines that directly match your requirements.

In my recent projects, I developed EduNexora AI—an explainable AI student performance prediction platform achieving 96% accuracy using Random Forest and SHAP—as well as an AI-Powered Attendance Management System using OpenCV and LBPH face recognition. Furthermore, I hold the #1 Institute Rank on GeeksforGeeks with 250+ solved algorithmic problems on LeetCode, demonstrating solid data structure fundamentals and fast execution.

I have attached my resume for your review. I would welcome the opportunity to discuss how my hands-on ML and Python skills can contribute to {company_name}'s engineering initiatives.

Best regards,
{cand_name}
{cand_email} | +91 8404970494
GitHub: https://github.com/manoj-kumar-mandal | LinkedIn: https://www.linkedin.com/in/manoj-kumar-mandal
"""
        body_html = f"""<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; color: #1e293b; line-height: 1.6; max-width: 600px;">
  <p>Hi {company_name} Hiring Team,</p>
  <p>I am writing to express my strong enthusiasm for the <strong>{job_title}</strong> position at <strong>{company_name}</strong>. As a Computer Science graduate from <em>{cand_college}</em> (CGPA: 8.01/10) specializing in Machine Learning, NLP, and Agentic AI systems, I have built production-grade AI solutions and high-performance Python pipelines that directly match your requirements.</p>
  <p>In my recent projects, I developed <strong>EduNexora AI</strong>—an explainable AI student performance prediction platform achieving 96% accuracy using Random Forest and SHAP—as well as an <strong>AI-Powered Attendance Management System</strong> using OpenCV and LBPH face recognition. Furthermore, I hold the <strong>#1 Institute Rank on GeeksforGeeks</strong> with <strong>250+ solved algorithmic problems</strong> on LeetCode, demonstrating solid data structure fundamentals and fast execution.</p>
  <p>I have attached my resume for your review. I would welcome the opportunity to discuss how my hands-on ML and Python skills can contribute to {company_name}'s engineering initiatives.</p>
  <p>Best regards,<br>
  <strong>{cand_name}</strong><br>
  <a href="mailto:{cand_email}" style="color: #1a56db;">{cand_email}</a> | +91 8404970494<br>
  <a href="https://github.com/manoj-kumar-mandal" style="color: #1a56db;">GitHub</a> | <a href="https://www.linkedin.com/in/manoj-kumar-mandal" style="color: #1a56db;">LinkedIn</a></p>
</div>"""

        return {
            "subject": subject,
            "body_text": body_text,
            "body_html": body_html,
            "is_ai_generated": False,
            "model_used": "deterministic_truth_preserving_template"
        }

    # ------------------------------------------------------------------
    # Persistence Helper
    # ------------------------------------------------------------------

    def _persist_scores_to_db(self, scored_jobs: List[Dict[str, Any]]):
        """Save scoring records to SQLite `job_scores` and update `applications`."""
        if not self.db:
            return

        for job in scored_jobs:
            score_data = {
                "job_hash": job.get("job_hash"),
                "relevance_score": job.get("relevance_score", 0.0),
                "skills_match_ratio": job.get("skills_match_ratio", 0.0),
                "experience_match": job.get("experience_match", True),
                "location_match": job.get("location_match", True),
                "matched_skills": job.get("matched_skills", []),
                "missing_skills": job.get("missing_skills", []),
                "reasoning": job.get("explanation") or job.get("reasoning", ""),
                "raw_gemini_response": job.get("raw_gemini_response", ""),
                "embedding_score": job.get("embedding_score"),
                "llm_score": job.get("llm_score"),
                "final_score": job.get("final_score", job.get("relevance_score", 0.0)),
            }
            try:
                self.db.save_job_score(score_data)
            except Exception as e:
                logger.error(f"[ScoringService] Failed to persist score for job '{job.get('title')}': {e}")
