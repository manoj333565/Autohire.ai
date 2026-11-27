
import pytest

from database.db_manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    """Create an isolated temporary database."""
    return DatabaseManager(
        str(tmp_path / "approval_security_test.db")
    )



def create_pending_item(db):
    """Create a temporary pending approval item."""
    import uuid

    reference_id = f"test-job-{uuid.uuid4().hex}"

    with db._get_conn() as conn:
        cursor = conn.execute(
            """
            INSERT INTO approval_queue (
                item_type,
                reference_id,
                title,
                company,
                details,
                preview_payload,
                score,
                status,
                execution_status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "job_application",
                reference_id,
                "Test Python Developer",
                "Test Company",
                "Temporary approval security test",
                "{}",
                8.5,
                "pending",
                "not_started",
            )
        )

        return cursor.lastrowid



def test_pending_item_can_be_approved(db):
    queue_id = create_pending_item(db)

    result = db.decide_approval(queue_id, "approved")

    assert result is True


def test_approved_item_cannot_be_changed(db):
    queue_id = create_pending_item(db)

    assert db.decide_approval(queue_id, "approved") is True
    assert db.decide_approval(queue_id, "rejected") is False


def test_rejected_item_cannot_be_approved_again(db):
    queue_id = create_pending_item(db)

    assert db.decide_approval(queue_id, "rejected") is True
    assert db.decide_approval(queue_id, "approved") is False


def test_invalid_decision_is_rejected(db):
    queue_id = create_pending_item(db)

    with pytest.raises(ValueError):
        db.decide_approval(queue_id, "invalid_decision")


def test_nonexistent_queue_id_returns_false(db):
    result = db.decide_approval(999999, "approved")

    assert result is False
