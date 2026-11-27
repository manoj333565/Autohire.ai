
import pytest

from database.db_manager import DatabaseManager


@pytest.fixture
def db(tmp_path):
    """Use an isolated temporary SQLite database."""
    return DatabaseManager(
        str(tmp_path / "approval_execution_test.db")
    )


def create_approved_job(db):
    """Create and approve a temporary job application."""
    import uuid

    url = f"https://example.com/jobs/{uuid.uuid4().hex}"
    job_hash = db.make_job_hash(url)

    job = {
        "url": url,
        "title": "Test AI Engineer",
        "company": "Test Company",
        "location": "Remote",
        "portal": "remoteok",
        "description": "Temporary execution claim test.",
    }

    assert db.add_job(job) is True

    queue_id = db.enqueue_for_approval(
        item_type="job_application",
        reference_id=job_hash,
        title=job["title"],
        company=job["company"],
        details="Temporary test",
        preview_payload=job,
        score=9.0,
    )

    assert db.decide_approval(queue_id, "approved") is True

    return queue_id, job_hash


def test_claim_table_exists(db):
    with db._get_conn() as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert "approval_execution_claims" in tables


def test_claim_only_once(db):
    queue_id, job_hash = create_approved_job(db)

    first_token = db.claim_approval_execution(
        queue_id, job_hash
    )
    second_token = db.claim_approval_execution(
        queue_id, job_hash
    )

    assert first_token
    assert second_token is None


def test_invalid_token_cannot_finalize(db):
    queue_id, job_hash = create_approved_job(db)

    token = db.claim_approval_execution(queue_id, job_hash)

    assert token

    result = db.update_approval_execution_status(
        queue_id,
        "applied",
        claim_token="invalid-token",
    )

    assert result is False


def test_valid_token_finalizes_once(db):
    queue_id, job_hash = create_approved_job(db)

    token = db.claim_approval_execution(queue_id, job_hash)

    assert token

    result = db.update_approval_execution_status(
        queue_id,
        "manual_required",
        claim_token=token,
    )

    assert result is True

    repeated = db.update_approval_execution_status(
        queue_id,
        "applied",
        claim_token=token,
    )

    assert repeated is False

    items = db.get_approval_queue(
        status="approved",
        item_type="job_application",
        execution_status="manual_required",
    )

    assert any(item["id"] == queue_id for item in items)


def test_cold_email_cannot_be_claimed(db):
    queue_id = db.enqueue_for_approval(
        item_type="cold_email",
        reference_id="test-email-reference",
        title="Test email",
        company="Test company",
        details="Email approval test",
        preview_payload={},
        score=5.0,
    )

    assert db.decide_approval(queue_id, "approved") is True

    token = db.claim_approval_execution(
        queue_id, "test-email-reference"
    )

    assert token is None

def test_concurrent_workers_cannot_claim_same_job(db):
    from concurrent.futures import ThreadPoolExecutor

    queue_id, job_hash = create_approved_job(db)

    def attempt_claim():
        return db.claim_approval_execution(queue_id, job_hash)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(attempt_claim),
            executor.submit(attempt_claim),
        ]

        results = [future.result() for future in futures]

    successful_claims = [
        token for token in results if token is not None
    ]

    assert len(successful_claims) == 1
