
import pytest

from database.db_manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    """Create an isolated temporary database."""
    return DatabaseManager(
        str(tmp_path / "bulk_approval_security_test.db")
    )


def create_pending_item(db, item_type="job_application"):
    """Create a temporary pending approval item."""
    import uuid

    reference_id = f"test-{item_type}-{uuid.uuid4().hex}"

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
                item_type,
                reference_id,
                "Test Approval Item",
                "Test Company",
                "Temporary bulk approval security test",
                "{}",
                8.5,
                "pending",
                "not_started" if item_type == "job_application" else "unknown",
            )
        )

        return cursor.lastrowid


def get_item_status(db, queue_id):
    """Read the status of a temporary approval item."""
    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM approval_queue WHERE id = ?",
            (queue_id,)
        ).fetchone()

        return row["status"]


def test_empty_selection_is_rejected(db):
    with pytest.raises(ValueError):
        db.bulk_decide_approval(
            decision="approved",
            item_ids=[]
        )


def test_selected_job_application_can_be_approved(db):
    queue_id = create_pending_item(db)

    result = db.bulk_decide_approval(
        decision="approved",
        item_ids=[queue_id]
    )

    assert result == 1
    assert get_item_status(db, queue_id) == "approved"


def test_unselected_application_remains_pending(db):
    selected_id = create_pending_item(db)
    unselected_id = create_pending_item(db)

    result = db.bulk_decide_approval(
        decision="approved",
        item_ids=[selected_id]
    )

    assert result == 1
    assert get_item_status(db, selected_id) == "approved"
    assert get_item_status(db, unselected_id) == "pending"


def test_recruiter_email_cannot_be_bulk_approved(db):
    email_id = create_pending_item(
        db,
        item_type="cold_email"
    )

    result = db.bulk_decide_approval(
        decision="approved",
        item_ids=[email_id]
    )

    assert result == 0
    assert get_item_status(db, email_id) == "pending"


def test_duplicate_ids_are_rejected(db):
    queue_id = create_pending_item(db)

    with pytest.raises(ValueError):
        db.bulk_decide_approval(
            decision="approved",
            item_ids=[queue_id, queue_id]
        )

    assert get_item_status(db, queue_id) == "pending"


@pytest.mark.parametrize(
    "invalid_ids",
    [
        [0],
        [-1],
        ["1"],
        [True],
        [None],
    ]
)
def test_invalid_ids_are_rejected(db, invalid_ids):
    with pytest.raises(ValueError):
        db.bulk_decide_approval(
            decision="approved",
            item_ids=invalid_ids
        )


def test_invalid_decision_is_rejected(db):
    queue_id = create_pending_item(db)

    with pytest.raises(ValueError):
        db.bulk_decide_approval(
            decision="invalid",
            item_ids=[queue_id]
        )


def test_already_decided_item_cannot_be_changed(db):
    queue_id = create_pending_item(db)

    db.decide_approval(queue_id, "approved")

    result = db.bulk_decide_approval(
        decision="rejected",
        item_ids=[queue_id]
    )

    assert result == 0
    assert get_item_status(db, queue_id) == "approved"
