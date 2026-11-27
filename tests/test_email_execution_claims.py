
import pytest
from concurrent.futures import ThreadPoolExecutor
from database.db_manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    return DatabaseManager(
        str(tmp_path / "email_execution_test.db")
    )


def create_approved_email(db):
    queue_id = db.enqueue_for_approval(
        item_type="cold_email",
        reference_id="test-job-email-001",
        title="Test Outreach",
        company="Test Company",
        details="Temporary email claim test",
        preview_payload={
            "recipient_email": "test@example.com",
            "subject": "Test",
        },
        score=5.0,
    )

    assert db.decide_approval(queue_id, "approved") is True
    return queue_id


def test_email_claim_table_exists(db):
    with db._get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert "email_execution_claims" in tables


def test_email_can_be_claimed_only_once(db):
    queue_id = create_approved_email(db)

    first = db.claim_email_execution(queue_id)
    second = db.claim_email_execution(queue_id)

    assert first is not None
    assert second is None


def test_email_claim_requires_approval(db):
    queue_id = db.enqueue_for_approval(
        item_type="cold_email",
        reference_id="pending-email",
        title="Pending email",
        company="Test Company",
        details="Pending approval test",
        preview_payload={},
    )

    assert db.claim_email_execution(queue_id) is None


def test_email_claim_finalizes_only_once(db):
    queue_id = create_approved_email(db)
    token = db.claim_email_execution(queue_id)

    assert token is not None

    assert db.finalize_email_execution(
        queue_id, token, "sent"
    ) is True

    assert db.finalize_email_execution(
        queue_id, token, "uncertain"
    ) is False



def test_concurrent_workers_cannot_claim_same_email(db):
    queue_id = create_approved_email(db)

    def attempt_claim():
        return db.claim_email_execution(queue_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_claim),
            executor.submit(attempt_claim),
        ]

        results = [future.result() for future in futures]

    successful = [
        token for token in results if token is not None
    ]

    assert len(successful) == 1
