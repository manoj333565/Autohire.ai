
import re

import pytest

import dashboard.app as dashboard_module
from database.db_manager import DatabaseManager


@pytest.fixture
def api_setup(tmp_path, monkeypatch):
    """Create an isolated Flask API and temporary SQLite database."""

    test_db = DatabaseManager(
        str(tmp_path / "bulk_api_security_test.db")
    )

    monkeypatch.setattr(
        dashboard_module,
        "get_db",
        lambda: test_db
    )

    dashboard_module.app.config.update(
        TESTING=True,
        SECRET_KEY="temporary-test-secret-key",
        WTF_CSRF_ENABLED=True,
    )

    client = dashboard_module.app.test_client()

    # Generate a valid CSRF token using the login page.
    login_page = client.get("/login")
    assert login_page.status_code == 200

    match = re.search(
        rb'<meta name="csrf-token" content="([^"]+)"',
        login_page.data
    )
    assert match, "CSRF token meta tag was not found."

    csrf_token = match.group(1).decode("utf-8")

    # Authenticate the test session.
    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user"] = "Temporary Test User"

    yield {
        "client": client,
        "db": test_db,
        "csrf_token": csrf_token,
    }


def create_pending_item(db, item_type="job_application"):
    """Insert a temporary pending queue record."""

    import uuid

    reference_id = f"api-test-{uuid.uuid4().hex}"

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
                "Temporary Test Application",
                "Test Company",
                "API security test",
                "{}",
                8.5,
                "pending",
                "not_started" if item_type == "job_application" else "unknown",
            )
        )

        return cursor.lastrowid


def get_status(db, item_id):
    """Read a temporary queue record's status."""

    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM approval_queue WHERE id = ?",
            (item_id,)
        ).fetchone()

        return row["status"]


def post_bulk(client, csrf_token, payload):
    """Send a bulk API request with valid CSRF protection."""

    return client.post(
        "/api/approval-queue/bulk-decide",
        json=payload,
        headers={"X-CSRFToken": csrf_token},
    )



def test_unauthenticated_request_is_rejected(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    # Remove authentication but preserve the CSRF session token.
    with client.session_transaction() as session:
        session.pop("authenticated", None)
        session.pop("user", None)

    response = post_bulk(
        client,
        token,
        {
            "decision": "approved",
            "item_ids": [1],
        }
    )

    assert response.status_code == 401



def test_missing_csrf_token_is_rejected(api_setup):
    client = api_setup["client"]

    response = client.post(
        "/api/approval-queue/bulk-decide",
        json={
            "decision": "approved",
            "item_ids": [1],
        }
    )

    assert response.status_code == 400


def test_empty_selection_is_rejected(api_setup):
    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "approved",
            "item_ids": [],
        }
    )

    assert response.status_code == 400


def test_selected_application_can_be_approved(api_setup):
    db = api_setup["db"]
    item_id = create_pending_item(db)

    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "approved",
            "item_ids": [item_id],
        }
    )

    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 1
    assert get_status(db, item_id) == "approved"


def test_unselected_application_remains_pending(api_setup):
    db = api_setup["db"]

    selected_id = create_pending_item(db)
    unselected_id = create_pending_item(db)

    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "approved",
            "item_ids": [selected_id],
        }
    )

    assert response.status_code == 200
    assert get_status(db, selected_id) == "approved"
    assert get_status(db, unselected_id) == "pending"


def test_recruiter_email_cannot_be_bulk_approved(api_setup):
    db = api_setup["db"]

    email_id = create_pending_item(
        db,
        item_type="cold_email"
    )

    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "approved",
            "item_ids": [email_id],
        }
    )

    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 0
    assert get_status(db, email_id) == "pending"


def test_mixed_selection_only_updates_job_applications(api_setup):
    db = api_setup["db"]

    job_id = create_pending_item(db, "job_application")
    email_id = create_pending_item(db, "cold_email")

    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "approved",
            "item_ids": [job_id, email_id],
        }
    )

    assert response.status_code == 200
    assert response.get_json()["updated_count"] == 1

    assert get_status(db, job_id) == "approved"
    assert get_status(db, email_id) == "pending"


@pytest.mark.parametrize(
    "item_ids",
    [
        [0],
        [-1],
        ["1"],
        [True],
        [None],
        [1, 1],
    ]
)
def test_invalid_item_ids_are_rejected(api_setup, item_ids):
    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "approved",
            "item_ids": item_ids,
        }
    )

    assert response.status_code == 400


def test_invalid_decision_is_rejected(api_setup):
    item_id = create_pending_item(api_setup["db"])

    response = post_bulk(
        api_setup["client"],
        api_setup["csrf_token"],
        {
            "decision": "invalid",
            "item_ids": [item_id],
        }
    )

    assert response.status_code == 400
    assert get_status(api_setup["db"], item_id) == "pending"
