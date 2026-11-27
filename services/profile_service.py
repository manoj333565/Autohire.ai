"""
services/profile_service.py — Dynamic Candidate Profile Management Service
Handles:
- Profile CRUD (Personal details, education, contact, resume)
- Granular Skill Management across 3 verified proficiency tiers (Learning, Intermediate, Proficient)
- Candidate Preferences (Target roles, locations, salary, matching threshold)
- Profile History Audit Trail (logging all changes to SQLite with safe transaction reuse)
- Resume Upload & Parsing (validation, text extraction, path updates)
- AI Scoring Context Generation (guaranteeing strict distinction between verified skills and resume qualifications)
"""
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional
import sqlite3

try:
    import PyPDF2 as pypdf
except ImportError:
    try:
        import pypdf
    except ImportError:
        pypdf = None

from database.db_manager import DatabaseManager
import config as cfg
from services.resume_parser import ResumeParser, ResumeParseError

logger = logging.getLogger("CareerPilot.ProfileService")

VALID_PROFICIENCIES = ("Learning", "Intermediate", "Proficient")


class ProfileService:
    """Enterprise domain service for dynamic candidate profile management."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager(cfg.DB_PATH)
        self.resume_parser = ResumeParser()

    # =========================================================================
    # PROFILE RETRIEVAL & AUDIT
    # =========================================================================

    def get_full_profile(self, profile_id: int = 1) -> Dict[str, Any]:
        """Fetch complete candidate profile with structured skills and preferences."""
        profile = self.db.get_candidate_profile(profile_id)
        if not profile:
            raise ValueError(f"Candidate profile with ID {profile_id} not found.")

        # Organize skills into distinct proficiency buckets and pending review
        skills_by_tier = {
            "Proficient": [],
            "Intermediate": [],
            "Learning": []
        }
        pending_review_skills = []

        all_skills = profile.get("skills", [])
        for s in all_skills:
            prof = s.get("proficiency", "Learning")
            is_verified = bool(s.get("verified", 1))
            skill_item = {
                "id": s["id"],
                "skill_name": s["skill_name"],
                "category": s.get("category", "General"),
                "proficiency": prof,
                "verified": is_verified,
                "source": s.get("source", "manual_declaration"),
                "created_at": s.get("created_at")
            }
            if is_verified and prof in skills_by_tier:
                skills_by_tier[prof].append(skill_item)
            else:
                pending_review_skills.append(skill_item)

        profile["skills_by_tier"] = skills_by_tier
        profile["pending_review_skills"] = pending_review_skills

        # Structured Education Object
        profile["education"] = {
            "degree": profile.get("education_degree", "B.Tech in Computer Science and Engineering"),
            "college": profile.get("education_college", "Satya Sai Engineering College Balasore, Odisha"),
            "year": profile.get("education_year", "2026"),
            "cgpa": profile.get("education_cgpa", "8.01/10")
        }

        # Load extra resume qualifications and background if available
        user_json = cfg.load_user_profile()
        profile["resume_qualifications"] = user_json.get("resume_qualifications", {})
        profile["experience"] = user_json.get("experience", [])
        profile["projects"] = user_json.get("projects", [])
        profile["certifications"] = user_json.get("certifications", [])
        profile["honors_awards"] = user_json.get("honors_awards", [])

        # Resume metadata
        profile["resume_metadata"] = self.get_resume_metadata(profile_id)

        return profile

    def log_change(
        self,
        profile_id: int,
        change_type: str,
        field_name: str,
        old_val: Any,
        new_val: Any,
        conn: Optional[sqlite3.Connection] = None
    ):
        """Record an audit entry in the profile_history table, safely reusing existing transaction if provided."""
        sql = """
            INSERT INTO profile_history (profile_id, change_type, field_name, old_value, new_value)
            VALUES (?, ?, ?, ?, ?)
        """
        params = (
            profile_id,
            change_type,
            field_name,
            str(old_val) if old_val is not None else "",
            str(new_val) if new_val is not None else ""
        )

        if conn is not None:
            conn.execute(sql, params)
        else:
            with self.db._get_conn() as c:
                c.execute(sql, params)

    def get_profile_history(self, profile_id: int = 1, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieve audit history log for the candidate profile."""
        with self.db._get_conn() as conn:
            rows = conn.execute("""
                SELECT id, profile_id, change_type, field_name, old_value, new_value, created_at
                FROM profile_history
                WHERE profile_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (profile_id, limit)).fetchall()
            return [dict(r) for r in rows]

    # =========================================================================
    # PERSONAL & EDUCATION DETAILS
    # =========================================================================

    def update_personal_details(self, profile_id: int, updates: Dict[str, Any]) -> bool:
        """Update candidate personal details with change audit tracking."""
        current = self.db.get_candidate_profile(profile_id)
        if not current:
            return False

        allowed_fields = [
            "name", "email", "phone", "location",
            "linkedin_url", "github_url", "portfolio_url", "summary"
        ]

        with self.db._get_conn() as conn:
            valid_updates = {}
            for f in allowed_fields:
                if f in updates:
                    new_val = str(updates[f]).strip()
                    old_val = str(current.get(f) or "").strip()
                    if new_val != old_val:
                        valid_updates[f] = new_val
                        self.log_change(profile_id, "UPDATE_PERSONAL", f, old_val, new_val, conn=conn)

            if valid_updates:
                set_clauses = [f"{k} = ?" for k in valid_updates.keys()]
                set_clauses.append("updated_at = CURRENT_TIMESTAMP")
                params = list(valid_updates.values()) + [profile_id]
                conn.execute(f"UPDATE candidate_profile SET {', '.join(set_clauses)} WHERE id = ?", params)
            return True

    def update_education(
        self,
        profile_id: int,
        degree: str,
        college: str,
        year: str,
        cgpa: str
    ) -> bool:
        """Update education credentials."""
        current = self.db.get_candidate_profile(profile_id)
        updates = {
            "education_degree": degree.strip(),
            "education_college": college.strip(),
            "education_year": str(year).strip(),
            "education_cgpa": str(cgpa).strip()
        }

        with self.db._get_conn() as conn:
            for k, v in updates.items():
                old_val = current.get(k) or ""
                if v != old_val:
                    self.log_change(profile_id, "UPDATE_EDUCATION", k, old_val, v, conn=conn)

            set_clauses = [f"{k} = ?" for k in updates.keys()]
            set_clauses.append("updated_at = CURRENT_TIMESTAMP")
            params = list(updates.values()) + [profile_id]
            conn.execute(f"UPDATE candidate_profile SET {', '.join(set_clauses)} WHERE id = ?", params)
            return True

    # =========================================================================
    # SKILL MANAGEMENT (Proficient, Intermediate, Learning)
    # =========================================================================

    def add_skill(
        self,
        profile_id: int,
        skill_name: str,
        proficiency: str,
        category: str = "Technical"
    ) -> bool:
        """Add a new verified skill declaration."""
        normalized_name = skill_name.strip()
        if not normalized_name:
            raise ValueError("Skill name cannot be empty.")

        normalized_prof = proficiency.strip().capitalize()
        if normalized_prof not in VALID_PROFICIENCIES:
            raise ValueError(f"Invalid proficiency level: '{proficiency}'. Must be one of: {VALID_PROFICIENCIES}")

        with self.db._get_conn() as conn:
            conn.execute("""
                INSERT INTO candidate_skills (profile_id, skill_name, proficiency, category, verified, source)
                VALUES (?, ?, ?, ?, 1, 'manual_declaration')
                ON CONFLICT(profile_id, skill_name) DO UPDATE SET
                    proficiency = excluded.proficiency,
                    category = excluded.category
            """, (profile_id, normalized_name, normalized_prof, category))
            self.log_change(profile_id, "ADD_SKILL", normalized_name, None, f"{normalized_prof} ({category})", conn=conn)
            logger.info(f"Added skill '{normalized_name}' ({normalized_prof}) for candidate {profile_id}")
            return True

    def update_skill_proficiency(self, skill_id: int, new_proficiency: str) -> bool:
        """Change the proficiency tier of an existing skill."""
        normalized_prof = new_proficiency.strip().capitalize()
        if normalized_prof not in VALID_PROFICIENCIES:
            raise ValueError(f"Invalid proficiency level: '{new_proficiency}'. Must be one of: {VALID_PROFICIENCIES}")

        with self.db._get_conn() as conn:
            row = conn.execute("SELECT profile_id, skill_name, proficiency FROM candidate_skills WHERE id = ?", (skill_id,)).fetchone()
            if not row:
                raise ValueError(f"Skill with ID {skill_id} not found.")

            profile_id = row["profile_id"]
            skill_name = row["skill_name"]
            old_prof = row["proficiency"]

            if old_prof == normalized_prof:
                return True

            conn.execute("""
                UPDATE candidate_skills
                SET proficiency = ?
                WHERE id = ?
            """, (normalized_prof, skill_id))

            self.log_change(profile_id, "UPDATE_SKILL_PROFICIENCY", skill_name, old_prof, normalized_prof, conn=conn)
            logger.info(f"Updated skill '{skill_name}' proficiency from '{old_prof}' to '{normalized_prof}'")
            return True

    def delete_skill(self, skill_id: int) -> bool:
        """Remove a skill declaration."""
        with self.db._get_conn() as conn:
            row = conn.execute("SELECT profile_id, skill_name, proficiency FROM candidate_skills WHERE id = ?", (skill_id,)).fetchone()
            if not row:
                return False

            profile_id = row["profile_id"]
            skill_name = row["skill_name"]
            old_prof = row["proficiency"]

            cursor = conn.execute("DELETE FROM candidate_skills WHERE id = ?", (skill_id,))
            if cursor.rowcount > 0:
                self.log_change(profile_id, "DELETE_SKILL", skill_name, old_prof, None, conn=conn)
                logger.info(f"Deleted skill '{skill_name}' for candidate {profile_id}")
                return True
            return False

    remove_skill = delete_skill


    # =========================================================================
    # PREFERENCES & TARGET ROLES
    # =========================================================================

    def add_target_role(self, profile_id: int, role_name: str) -> bool:
        """Append a target role to candidate preferences."""
        role_cleaned = role_name.strip()
        if not role_cleaned:
            return False

        profile = self.get_full_profile(profile_id)
        current_roles = profile["preferences"]["target_roles"]
        if role_cleaned not in current_roles:
            current_roles.append(role_cleaned)
            prefs = profile["preferences"]
            prefs["target_roles"] = current_roles
            self.db.update_candidate_preferences(profile_id, prefs)
            self.log_change(profile_id, "ADD_TARGET_ROLE", "target_roles", None, role_cleaned)
            return True
        return True

    def remove_target_role(self, profile_id: int, role_name: str) -> bool:
        """Remove a target role from preferences."""
        profile = self.get_full_profile(profile_id)
        current_roles = profile["preferences"]["target_roles"]
        if role_name in current_roles:
            current_roles.remove(role_name)
            prefs = profile["preferences"]
            prefs["target_roles"] = current_roles
            self.db.update_candidate_preferences(profile_id, prefs)
            self.log_change(profile_id, "REMOVE_TARGET_ROLE", "target_roles", role_name, None)
            return True
        return False

    def update_preferences(self, profile_id: int, preferences_dict: Dict[str, Any]) -> bool:
        """Update job search preferences (locations, matching threshold, salary)."""
        current_profile = self.get_full_profile(profile_id)
        current_prefs = current_profile["preferences"]

        for k, v in preferences_dict.items():
            if k in current_prefs and current_prefs[k] != v:
                self.log_change(profile_id, "UPDATE_PREFERENCE", k, current_prefs[k], v)
                current_prefs[k] = v

        return self.db.update_candidate_preferences(profile_id, current_prefs)

    # =========================================================================
    # RESUME MANAGEMENT & INTELLIGENCE
    # =========================================================================

    def get_resume_metadata(self, profile_id: int = 1) -> Dict[str, Any]:
        """Fetch status and file metadata for candidate resume."""
        profile = self.db.get_candidate_profile(profile_id) or {}
        resume_rel_path = profile.get("resume_path", "data/resume.pdf")
        resume_abs_path = (Path(cfg.BASE_DIR) / resume_rel_path).resolve()

        if not resume_abs_path.exists():
            return {
                "exists": False,
                "file_path": str(resume_abs_path),
                "parse_status": "FILE_MISSING",
                "message": "Resume file not found on disk."
            }

        file_size = resume_abs_path.stat().st_size
        mtime = datetime.fromtimestamp(resume_abs_path.stat().st_mtime).isoformat()

        return {
            "exists": True,
            "file_name": resume_abs_path.name,
            "relative_path": resume_rel_path,
            "absolute_path": str(resume_abs_path),
            "file_size_bytes": file_size,
            "file_size_kb": round(file_size / 1024, 1),
            "last_modified": mtime,
            "parse_status": "VERIFIED_ON_DISK"
        }

    def parse_resume_file(self, source_pdf_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Deep extract resume content using the advanced ResumeParser.
        Handles missing, corrupted, or zero-byte PDFs cleanly.
        """
        target_path = source_pdf_path or cfg.RESUME_PATH
        return self.resume_parser.parse(target_path)

    def update_resume_file(self, profile_id: int, source_pdf_path: str) -> Dict[str, Any]:
        """Validate, extract, and associate a new resume PDF."""
        src_path = Path(source_pdf_path)
        if not src_path.exists():
            raise FileNotFoundError(f"Source resume file not found at: {source_pdf_path}")

        # Parse and validate with ResumeParser
        parsed_result = self.resume_parser.parse(str(src_path))
        page_count = parsed_result["metadata"]["page_count"]
        char_count = parsed_result["metadata"]["char_count"]
        extracted_text = parsed_result["raw_text"]

        dest_path = Path(cfg.DATA_DIR) / "resume.pdf"
        # Only copy if source and destination are different
        if src_path.resolve() != dest_path.resolve():
            import shutil
            shutil.copyfile(src_path, dest_path)

        # Update candidate_profile path
        rel_path = str(dest_path.relative_to(cfg.BASE_DIR)).replace("\\", "/")
        self.db.update_candidate_profile(profile_id, {"resume_path": rel_path})
        self.log_change(profile_id, "UPDATE_RESUME", "resume_path", None, rel_path)

        logger.info(f"Resume updated successfully for candidate {profile_id}: {page_count} pages, {char_count} characters.")
        return {
            "resume_path": rel_path,
            "page_count": page_count,
            "char_count": char_count,
            "preview_snippet": extracted_text[:300],
            "parsed_data": parsed_result
        }

    def sync_profile_with_resume(
        self,
        profile_id: int = 1,
        confirm_overwrite: bool = False,
        source_pdf_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Synchronize candidate profile and database with actual resume content.
        
        SAFETY GATE:
        - If confirm_overwrite is False: returns the extracted data and diff without modifying verified data.
        - If confirm_overwrite is True: commits validated changes with audit logging to profile_history.
        """
        parsed = self.parse_resume_file(source_pdf_path)
        current = self.get_full_profile(profile_id)

        # Detect diffs
        proposed_updates = {}
        diff_summary = []

        cand_info = parsed.get("candidate_info", {})
        edu_info = parsed.get("education", {})

        # Personal details comparison
        for f in ["name", "email", "phone", "location", "linkedin_url", "github_url"]:
            val = cand_info.get(f)
            curr_val = current.get(f) or ""
            if val and val.strip().lower() != curr_val.strip().lower():
                proposed_updates[f] = val
                diff_summary.append({
                    "field": f,
                    "current": curr_val,
                    "extracted": val
                })

        # Education comparison
        edu_map = {
            "education_degree": edu_info.get("degree"),
            "education_college": edu_info.get("college"),
            "education_year": edu_info.get("year"),
            "education_cgpa": edu_info.get("cgpa")
        }
        for k, v in edu_map.items():
            curr_v = current.get(k) or ""
            if v and v.strip().lower() != curr_v.strip().lower():
                proposed_updates[k] = v
                diff_summary.append({
                    "field": k,
                    "current": curr_v,
                    "extracted": v
                })

        # Check for new skills from resume
        extracted_skills = parsed.get("skills", {}).get("all_extracted_skills", [])
        existing_skills = [s["skill_name"].lower() for s in current.get("skills", [])]
        new_skills = [s for s in extracted_skills if s.lower() not in existing_skills]

        if not confirm_overwrite:
            return {
                "status": "REQUIRES_CONFIRMATION",
                "requires_confirmation": True,
                "message": (
                    f"Resume parsed ({parsed['metadata']['page_count']} pages, {len(extracted_skills)} skills detected). "
                    f"User confirmation required to apply {len(diff_summary)} profile changes and {len(new_skills)} unverified skills."
                ),
                "extracted_data": parsed,
                "diff_summary": diff_summary,
                "new_skills_pending_review": new_skills,
                "proposed_updates": proposed_updates
            }

        # User has confirmed overwrite! Apply changes safely with audit trail.
        applied_fields = []
        if proposed_updates:
            self.update_personal_details(profile_id, proposed_updates)
            if any(k.startswith("education_") for k in proposed_updates):
                self.update_education(
                    profile_id,
                    degree=proposed_updates.get("education_degree", current.get("education_degree", "")),
                    college=proposed_updates.get("education_college", current.get("education_college", "")),
                    year=proposed_updates.get("education_year", current.get("education_year", "")),
                    cgpa=proposed_updates.get("education_cgpa", current.get("education_cgpa", ""))
                )
            applied_fields = list(proposed_updates.keys())

        # For newly discovered skills, stage them as pending review (verified=0)
        # Never automatically give verified proficiency without user review!
        with self.db._get_conn() as conn:
            for s in new_skills:
                conn.execute("""
                    INSERT OR IGNORE INTO candidate_skills (profile_id, skill_name, proficiency, category, verified, source)
                    VALUES (?, ?, 'Learning', 'Resume Extracted', 0, 'resume_extracted_pending_review')
                """, (profile_id, s))
                self.log_change(profile_id, "STAGE_SKILL_FOR_REVIEW", s, None, "Pending Review", conn=conn)

        # Synchronize projects & certifications into user_profile.json
        user_json = cfg.load_user_profile()
        user_json["projects"] = parsed.get("projects", user_json.get("projects", []))
        user_json["certifications"] = parsed.get("certifications", user_json.get("certifications", []))
        user_json["honors_awards"] = parsed.get("achievements", user_json.get("honors_awards", []))
        user_json["experience"] = parsed.get("experience", user_json.get("experience", []))
        cfg.save_user_profile(user_json)

        return {
            "status": "SYNCHRONIZED",
            "requires_confirmation": False,
            "message": "Candidate profile successfully synchronized with verified resume content.",
            "updated_fields": applied_fields,
            "new_skills_staged_for_review": new_skills
        }

    def confirm_skill_proficiency(
        self,
        profile_id: int,
        skill_name: str,
        proficiency: str,
        category: str = "Technical"
    ) -> bool:
        """
        User confirms or promotes a skill into a verified proficiency tier.
        Guarantees verified=1 and updates or inserts into candidate_skills.
        """
        norm_name = skill_name.strip()
        norm_prof = proficiency.strip().capitalize()
        if norm_prof not in VALID_PROFICIENCIES:
            raise ValueError(f"Invalid proficiency level: '{proficiency}'. Must be one of: {VALID_PROFICIENCIES}")

        with self.db._get_conn() as conn:
            conn.execute("""
                INSERT INTO candidate_skills (profile_id, skill_name, proficiency, category, verified, source)
                VALUES (?, ?, ?, ?, 1, 'user_verified')
                ON CONFLICT(profile_id, skill_name) DO UPDATE SET
                    proficiency = excluded.proficiency,
                    category = excluded.category,
                    verified = 1,
                    source = 'user_verified'
            """, (profile_id, norm_name, norm_prof, category))
            self.log_change(profile_id, "CONFIRM_SKILL", norm_name, None, f"{norm_prof} ({category})", conn=conn)
            logger.info(f"Confirmed skill '{norm_name}' as {norm_prof} for candidate {profile_id}")
            return True

    # =========================================================================
    # AI SCORING CONTEXT GENERATOR
    # =========================================================================

    def get_candidate_context_for_scoring(self, profile_id: int = 1) -> Dict[str, Any]:
        """
        Construct structured context for the Gemini job-scoring prompt.
        CRITICAL REQUIREMENT:
        - Keeps verified skill levels (Proficient, Intermediate, Learning) strictly distinct
          from historical resume keywords.
        - The AI prompt evaluates candidate fit based on verified proficiency declaration weights.
        """
        profile = self.get_full_profile(profile_id)
        skills_by_tier = profile.get("skills_by_tier", {})
        prefs = profile.get("preferences", {})

        return {
            "candidate_identity": {
                "name": profile.get("name"),
                "degree": profile.get("education_degree"),
                "college": profile.get("education_college"),
                "year": profile.get("education_year"),
                "cgpa": profile.get("education_cgpa"),
                "location": profile.get("location")
            },
            "verified_skills_by_proficiency": {
                "Proficient": [s["skill_name"] for s in skills_by_tier.get("Proficient", [])],
                "Intermediate": [s["skill_name"] for s in skills_by_tier.get("Intermediate", [])],
                "Learning": [s["skill_name"] for s in skills_by_tier.get("Learning", [])]
            },
            "historical_resume_skills": profile.get("resume_qualifications", {}),
            "experience_summary": profile.get("experience", []),
            "key_projects": profile.get("projects", []),
            "certifications": profile.get("certifications", []),
            "target_preferences": {
                "target_roles": prefs.get("target_roles", []),
                "target_keywords": prefs.get("target_keywords", []),
                "exclude_keywords": prefs.get("exclude_keywords", []),
                "preferred_locations": prefs.get("preferred_locations", []),
                "matching_threshold": prefs.get("matching_threshold", 7.0),
                "min_salary_lpa": prefs.get("min_salary_lpa", 3.6),
                "allow_remote": prefs.get("allow_remote", True),
                "allow_internships": prefs.get("allow_internships", True)
            },
            "scoring_instructions": (
                "Evaluate the job description primarily against candidate's verified skills: "
                "give full weight (1.0) to Proficient skills (RAG, Agentic AI, Vector DBs), "
                "moderate weight (0.7) to Intermediate skills (LangChain, LangGraph, FastAPI, Transformers), "
                "and baseline weight (0.3) to Learning skills (Docker, PyTorch, TensorFlow). "
                "Do NOT assume proficiency in skills only mentioned historically in resume."
            )
        }

    get_scoring_context = get_candidate_context_for_scoring

