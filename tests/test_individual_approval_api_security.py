
import re
import uuid

import pytest

import dashboard.app as dashboard_module
from database.db_manager import DatabaseManager


@pytest.fixture
def api_setup(tmp_path, monkeypatch):
    """Create an isolated dashboard API and temporary SQLite database."""

    test_db = DatabaseManager(
        str(tmp_path / "individual_approval_test.db")
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

    login_page = client.get("/login")
    assert login_page.status_code == 200

    match = re.search(
        rb'<meta name="csrf-token" content="([^"]+)"',
        login_page.data
    )
    assert match, "CSRF token was not found."

    csrf_token = match.group(1).decode("utf-8")

    with client.session_transaction() as session:
        session["authenticated"] = True
        session["user"] = "Temporary Test User"

    return {
        "client": client,
        "db": test_db,
        "csrf_token": csrf_token,
    }


def create_pending_item(db, item_type="job_application"):
    """Insert a temporary pending approval item."""

    reference_id = f"individual-test-{uuid.uuid4().hex}"

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
    """Read the status of a temporary approval item."""

    with db._get_conn() as conn:
        row = conn.execute(
            "SELECT status FROM approval_queue WHERE id = ?",
            (item_id,)
        ).fetchone()

        return row["status"] if row else None


def post_decision(client, csrf_token, item_id, payload):
    """Send an individual approval request with valid CSRF."""

    return client.post(
        f"/api/approval-queue/{item_id}/decide",
        json=payload,
        headers={"X-CSRFToken": csrf_token},
    )


def test_unauthenticated_request_is_rejected(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    with client.session_transaction() as session:
        session.pop("authenticated", None)
        session.pop("user", None)

    response = post_decision(
        client,
        token,
        1,
        {"decision": "approved"}
    )

    assert response.status_code == 401


def test_missing_csrf_token_is_rejected(api_setup):
    client = api_setup["client"]

    response = client.post(
        "/api/approval-queue/1/decide",
        json={"decision": "approved"}
    )

    assert response.status_code == 400


def test_valid_pending_item_can_be_approved(api_setup):
    client = api_setup["client"]
    db = api_setup["db"]
    token = api_setup["csrf_token"]

    item_id = create_pending_item(db)

    response = post_decision(
        client,
        token,
        item_id,
        {"decision": "approved", "notes": "Test approval"}
    )

    assert response.status_code == 200
    assert response.json["decision"] == "approved"
    assert get_status(db, item_id) == "approved"


def test_valid_pending_item_can_be_rejected(api_setup):
    client = api_setup["client"]
    db = api_setup["db"]
    token = api_setup["csrf_token"]

    item_id = create_pending_item(db)

    response = post_decision(
        client,
        token,
        item_id,
        {"decision": "rejected"}
    )

    assert response.status_code == 200
    assert response.json["decision"] == "rejected"
    assert get_status(db, item_id) == "rejected"


def test_invalid_decision_is_rejected(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    response = post_decision(
        client,
        token,
        1,
        {"decision": "delete"}
    )

    assert response.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"decision": 123},
        {"decision": None},
        {"decision": ["approved"]},
        {"decision": "approved", "notes": 123},
        {"decision": "approved", "notes": None},
    ]
)
def test_invalid_payload_values_are_rejected(api_setup, payload):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    response = post_decision(client, token, 1, payload)

    assert response.status_code == 400


def test_invalid_json_body_is_rejected(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    response = client.post(
        "/api/approval-queue/1/decide",
        data="not-valid-json",
        content_type="application/json",
        headers={"X-CSRFToken": token},
    )

    assert response.status_code == 400


def test_zero_item_id_is_rejected(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    response = post_decision(
        client,
        token,
        0,
        {"decision": "approved"}
    )

    assert response.status_code == 400


def test_nonexistent_item_returns_conflict(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    response = post_decision(
        client,
        token,
        999999,
        {"decision": "approved"}
    )

    assert response.status_code == 409


def test_already_approved_item_cannot_be_changed(api_setup):
    client = api_setup["client"]
    db = api_setup["db"]
    token = api_setup["csrf_token"]

    item_id = create_pending_item(db)

    first_response = post_decision(
        client,
        token,
        item_id,
        {"decision": "approved"}
    )

    assert first_response.status_code == 200

    second_response = post_decision(
        client,
        token,
        item_id,
        {"decision": "rejected"}
    )

    assert second_response.status_code == 409
    assert get_status(db, item_id) == "approved"


def test_already_rejected_item_cannot_be_approved(api_setup):
    client = api_setup["client"]
    db = api_setup["db"]
    token = api_setup["csrf_token"]

    item_id = create_pending_item(db)

    first_response = post_decision(
        client,
        token,
        item_id,
        {"decision": "rejected"}
    )

    assert first_response.status_code == 200

    second_response = post_decision(
        client,
        token,
        item_id,
        {"decision": "approved"}
    )

    assert second_response.status_code == 409
    assert get_status(db, item_id) == "rejected"


def test_notes_over_2000_characters_are_rejected(api_setup):
    client = api_setup["client"]
    token = api_setup["csrf_token"]

    response = post_decision(
        client,
        token,
        1,
        {
            "decision": "approved",
            "notes": "A" * 2001
        }
    )

    assert response.status_code == 400
