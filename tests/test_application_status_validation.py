
import pytest

from database.db_manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    """Create an isolated temporary SQLite database."""
    db_path = tmp_path / "test_application_status.db"
    return DatabaseManager(str(db_path))


def create_test_application(db):
    """Insert a uniquely identifiable test application."""
    import uuid

    test_url = (
        f"https://example.com/jobs/status-test-{uuid.uuid4().hex}"
    )

    job_hash = db.make_job_hash(test_url)

    payload = {
        "url": test_url,
        "title": "Test Python Developer",
        "company": "Test Company",
        "location": "Remote",
        "portal": "remoteok",
        "description": "Temporary database test job.",
    }

    assert db.add_job(payload) is True
    return job_hash


def test_valid_application_status_update(db):
    job_hash = create_test_application(db)

    result = db.update_application_status(
        job_hash,
        "applied",
        notes="Temporary test"
    )

    assert result is True


def test_invalid_application_status_rejected(db):
    job_hash = create_test_application(db)

    with pytest.raises(ValueError):
        db.update_application_status(
            job_hash,
            "invalid_status"
        )


def test_empty_job_hash_rejected(db):
    with pytest.raises(ValueError):
        db.update_application_status(
            "",
            "applied"
        )


def test_nonexistent_application_returns_false(db):
    result = db.update_application_status(
        "nonexistent_test_hash",
        "applied"
    )

    assert result is False


def test_invalid_notes_type_rejected(db):
    job_hash = create_test_application(db)

    with pytest.raises(ValueError):
        db.update_application_status(
            job_hash,
            "applied",
            notes=123
        )
