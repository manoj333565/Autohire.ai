"""
tests/test_profile_service.py — Automated Unit & Integration Tests for Dynamic Candidate Profile
Validates:
- Complete profile retrieval and structured skill tiers
- Skill CRUD and proficiency constraint enforcement
- Target roles & preferences updates
- Audit logging into profile_history
- Resume parsing and metadata extraction
- AI scoring context generator separation (Verified skills vs Resume qualifications)
"""
import sys
from pathlib import Path

# Add project root to path
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import config as cfg
from database.db_manager import DatabaseManager
from services.profile_service import ProfileService


def test_full_profile_retrieval():
    """Verify profile retrieval and 3-tier skill categorization."""
    service = ProfileService()
    profile = service.get_full_profile(profile_id=1)

    assert profile["name"] == "Manoj Kumar Mandal"
    assert profile["email"] == "manojkumarmandal1111@gmail.com"
    assert "skills_by_tier" in profile

    tiers = profile["skills_by_tier"]
    proficient_skills = [s["skill_name"] for s in tiers["Proficient"]]
    intermediate_skills = [s["skill_name"] for s in tiers["Intermediate"]]
    learning_skills = [s["skill_name"] for s in tiers["Learning"]]

    assert "RAG" in proficient_skills
    assert "Agentic AI" in proficient_skills
    assert "Vector Databases" in proficient_skills

    assert "LangChain" in intermediate_skills
    assert "LangGraph" in intermediate_skills
    assert "FastAPI" in intermediate_skills

    assert "Docker" in learning_skills
    assert "PyTorch" in learning_skills
    assert "TensorFlow" in learning_skills


def test_skill_management_and_constraints():
    """Verify skill addition, proficiency updates, deletion, and invalid tier rejection."""
    service = ProfileService()

    # 1. Add skill
    added = service.add_skill(1, "Qdrant Vector DB", "Proficient", "AI Infrastructure")
    assert added is True

    # Check skill exists
    profile = service.get_full_profile(1)
    proficient_names = [s["skill_name"] for s in profile["skills_by_tier"]["Proficient"]]
    assert "Qdrant Vector DB" in proficient_names

    # 2. Update skill proficiency
    skill_obj = next(s for s in profile["skills"] if s["skill_name"] == "Qdrant Vector DB")
    updated = service.update_skill_proficiency(skill_obj["id"], "Intermediate")
    assert updated is True

    # 3. Verify constraint rejection
    try:
        service.add_skill(1, "FakeSkill", "Master")
        assert False, "Should have raised ValueError on invalid proficiency"
    except ValueError:
        pass

    # 4. Delete skill
    deleted = service.delete_skill(skill_obj["id"])
    assert deleted is True

    profile_after = service.get_full_profile(1)
    all_names = [s["skill_name"] for s in profile_after["skills"]]
    assert "Qdrant Vector DB" not in all_names


def test_target_roles_and_preferences():
    """Verify target role addition/removal and preferences updating."""
    service = ProfileService()

    # Add target role
    new_role = "Autonomous Multi-Agent Architect"
    service.add_target_role(1, new_role)
    prof = service.get_full_profile(1)
    assert new_role in prof["preferences"]["target_roles"]

    # Remove target role
    service.remove_target_role(1, new_role)
    prof_after = service.get_full_profile(1)
    assert new_role not in prof_after["preferences"]["target_roles"]

    # Update preferences
    service.update_preferences(1, {"matching_threshold": 8.0, "allow_remote": True})
    prof_updated = service.get_full_profile(1)
    assert prof_updated["preferences"]["matching_threshold"] == 8.0


def test_profile_history_audit():
    """Verify that modifications write audit records into profile_history."""
    import uuid
    service = ProfileService()
    test_loc = f"Bengaluru / Remote, India ({uuid.uuid4().hex[:4]})"
    service.update_personal_details(1, {"location": test_loc})

    history = service.get_profile_history(profile_id=1, limit=10)
    assert len(history) > 0
    assert any(h["change_type"] == "UPDATE_PERSONAL" for h in history)


def test_resume_parsing_and_metadata():
    """Verify resume PDF verification and text extraction."""
    service = ProfileService()
    result = service.update_resume_file(1, cfg.RESUME_PATH)

    assert result["page_count"] > 0
    assert result["char_count"] > 100
    assert "manoj" in result["preview_snippet"].lower()


def test_ai_scoring_context_separation():
    """Verify clean separation between verified skills and historical resume text in AI context."""
    service = ProfileService()
    context = service.get_candidate_context_for_scoring(profile_id=1)

    assert "verified_skills_by_proficiency" in context
    assert "historical_resume_skills" in context
    assert "target_preferences" in context
    assert "scoring_instructions" in context

    verified = context["verified_skills_by_proficiency"]
    assert "RAG" in verified["Proficient"]
    assert "Agentic AI" in verified["Proficient"]
    assert "LangGraph" in verified["Intermediate"]
    assert "PyTorch" in verified["Learning"]

    # Ensure scoring instructions explicitly instruct LLM to weight verified declarations
    assert "Proficient" in context["scoring_instructions"]


if __name__ == "__main__":
    print("Running Milestone 3 Candidate Profile Service Tests...")
    test_full_profile_retrieval()
    print("  ✅ test_full_profile_retrieval passed")
    test_skill_management_and_constraints()
    print("  ✅ test_skill_management_and_constraints passed")
    test_target_roles_and_preferences()
    print("  ✅ test_target_roles_and_preferences passed")
    test_profile_history_audit()
    print("  ✅ test_profile_history_audit passed")
    test_resume_parsing_and_metadata()
    print("  ✅ test_resume_parsing_and_metadata passed")
    test_ai_scoring_context_separation()
    print("  ✅ test_ai_scoring_context_separation passed")
    print("\n🎉 ALL CANDIDATE PROFILE TESTS PASSED SUCCESSFULLY!")
