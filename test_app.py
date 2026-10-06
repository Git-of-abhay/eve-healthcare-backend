import os
import sqlite3

os.environ["JWT_SECRET_KEY"] = "test-secret-key-at-least-32-bytes-long"

import pytest

from app import app, init_db


# Separate database file so tests never touch the real eve.db.
TEST_DB = "test_eve.db"


# Runs around EVERY test (autouse=True).
# Before: delete any old test DB and create fresh, empty tables.
# After:  delete the test DB so the next test starts clean.
@pytest.fixture(autouse=True)
def database():

    app.config["TESTING"] = True
    app.config["DATABASE"] = TEST_DB

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    init_db()

    yield

    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)


# Reads rows straight from the test DB with raw SQL,
# so tests can check what was actually stored.
def query_db(sql, params=()):

    conn = sqlite3.connect(TEST_DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(sql, params).fetchall()

    conn.close()
    return rows


def create_user_and_login(
    client,
    email="abhay@example.com"
):

    client.post(
        "/auth/register",
        json={
            "email": email,
            "password": "StrongPass123"
        }
    )

    response = client.post(
        "/auth/login",
        json={
            "email": email,
            "password": "StrongPass123"
        }
    )

    token = response.get_json()[
        "access_token"
    ]

    return {
        "Authorization":
        f"Bearer {token}"
    }


# Creates a centre, a test at that centre, and a booking.
# Returns the booking JSON.
def create_booking(client, headers, price=700):

    centre = client.post(
        "/centres",
        headers=headers,
        json={
            "name": "EVE Diagnostics",
            "location": "Bengaluru"
        }
    ).get_json()

    diagnostic_test = client.post(
        f"/centres/{centre['id']}/tests",
        headers=headers,
        json={
            "name": "CBC",
            "price": price
        }
    ).get_json()

    response = client.post(
        "/bookings",
        headers=headers,
        json={
            "centre_id": centre["id"],
            "test_id": diagnostic_test["id"],
            "appointment_at": "2026-10-01T10:30:00"
        }
    )

    assert response.status_code == 201

    return response.get_json()["booking"]


def test_register_login():

    client = app.test_client()

    response = client.post(
        "/auth/register",
        json={
            "email": "abhay@example.com",
            "password": "StrongPass123"
        }
    )

    assert response.status_code == 201
    assert response.get_json()["email"] == "abhay@example.com"

    # Password must be stored hashed, never as plain text.
    user = query_db(
        "SELECT password_hash FROM user WHERE email = ?",
        ("abhay@example.com",)
    )[0]

    assert user["password_hash"] != "StrongPass123"

    # Registering the same email again hits the UNIQUE rule.
    duplicate = client.post(
        "/auth/register",
        json={
            "email": "abhay@example.com",
            "password": "StrongPass123"
        }
    )

    assert duplicate.status_code == 409

    response = client.post(
        "/auth/login",
        json={
            "email": "abhay@example.com",
            "password": "StrongPass123"
        }
    )

    assert response.status_code == 200

    token = response.get_json()["access_token"]

    wrong_password = client.post(
        "/auth/login",
        json={
            "email": "abhay@example.com",
            "password": "WrongPass123"
        }
    )

    assert wrong_password.status_code == 401

    # The JWT identifies the user on protected routes.
    me = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )

    assert me.status_code == 200
    assert me.get_json()["email"] == "abhay@example.com"

    assert client.get("/auth/me").status_code == 401


def test_centres_and_tests():

    client = app.test_client()

    headers = create_user_and_login(client)

    centre = client.post(
        "/centres",
        headers=headers,
        json={
            "name": "EVE Diagnostics",
            "location": "Bengaluru"
        }
    )

    assert centre.status_code == 201

    centre_id = centre.get_json()["id"]

    # A centre with no tests must still be listed (LEFT JOIN).
    client.post(
        "/centres",
        headers=headers,
        json={
            "name": "Empty Centre",
            "location": "Mumbai"
        }
    )

    diagnostic_test = client.post(
        f"/centres/{centre_id}/tests",
        headers=headers,
        json={
            "name": "CBC",
            "price": 700
        }
    )

    assert diagnostic_test.status_code == 201
    assert diagnostic_test.get_json()["price"] == 700.0

    missing_centre = client.post(
        "/centres/999/tests",
        headers=headers,
        json={
            "name": "CBC",
            "price": 700
        }
    )

    assert missing_centre.status_code == 404

    centres = client.get("/centres").get_json()["centres"]

    assert len(centres) == 2
    assert centres[0]["tests"][0]["name"] == "CBC"
    assert centres[1]["tests"] == []

    tests = client.get(f"/centres/{centre_id}/tests").get_json()

    assert tests["centre"] == "EVE Diagnostics"
    assert tests["tests"][0]["price"] == 700.0


def test_booking_and_payment():

    client = app.test_client()

    headers = create_user_and_login(
        client
    )

    booking = create_booking(client, headers)

    assert booking["status"] == "PENDING"
    assert booking["amount"] == 700.0

    booking_id = booking["id"]

    payment = client.post(
        "/payments",
        headers=headers,
        json={
            "booking_id": booking_id,
            "status": "SUCCESS"
        }
    )

    assert payment.status_code == 200

    assert (
        payment.get_json()
        ["booking_status"]
        == "CONFIRMED"
    )

    # The payment row and the booking update were committed together.
    stored_payment = query_db(
        "SELECT status, amount FROM payment WHERE booking_id = ?",
        (booking_id,)
    )

    assert len(stored_payment) == 1
    assert stored_payment[0]["status"] == "SUCCESS"

    # A confirmed booking cannot be paid again.
    again = client.post(
        "/payments/",
        headers=headers,
        json={
            "booking_id": booking_id,
            "status": "SUCCESS"
        }
    )

    assert again.status_code == 409

    bookings = client.get(
        "/bookings",
        headers=headers
    ).get_json()["bookings"]

    assert len(bookings) == 1
    assert bookings[0]["status"] == "CONFIRMED"


def test_failed_payment_and_cancel():

    client = app.test_client()

    headers = create_user_and_login(client)

    booking = create_booking(client, headers)

    payment = client.post(
        "/payments",
        headers=headers,
        json={
            "booking_id": booking["id"],
            "status": "FAILED"
        }
    )

    assert payment.status_code == 200
    assert payment.get_json()["booking_status"] == "FAILED"

    cancel = client.post(
        f"/bookings/{booking['id']}/cancel",
        headers=headers
    )

    assert cancel.status_code == 200
    assert cancel.get_json()["booking"]["status"] == "CANCELLED"

    # Cancelled bookings cannot be paid.
    payment = client.post(
        "/payments",
        headers=headers,
        json={
            "booking_id": booking["id"],
            "status": "SUCCESS"
        }
    )

    assert payment.status_code == 409


def test_booking_ownership():

    client = app.test_client()

    owner = create_user_and_login(client)

    booking = create_booking(client, owner)

    other = create_user_and_login(
        client,
        email="other@example.com"
    )

    assert client.get(
        f"/bookings/{booking['id']}",
        headers=other
    ).status_code == 403

    assert client.post(
        f"/bookings/{booking['id']}/cancel",
        headers=other
    ).status_code == 403

    assert client.post(
        "/payments",
        headers=other,
        json={
            "booking_id": booking["id"],
            "status": "SUCCESS"
        }
    ).status_code == 403

    # The other user sees none of the owner's bookings.
    assert client.get(
        "/bookings",
        headers=other
    ).get_json()["bookings"] == []


def test_webhook_idempotency():

    client = app.test_client()

    headers = create_user_and_login(
        client
    )

    booking = create_booking(client, headers, price=1000)

    payment = client.post(
        "/payments",
        headers=headers,
        json={
            "booking_id": booking["id"],

            "status":
                "FAILED"
        }
    ).get_json()

    provider_payment_id = (
        payment["payment"]
        ["provider_payment_id"]
    )

    webhook_body = {
        "event_id": "evt-123",
        "provider_payment_id":
            provider_payment_id,
        "status": "SUCCESS"
    }

    first = client.post(
        "/payments/webhook",
        json=webhook_body
    )

    second = client.post(
        "/payments/webhook/",
        json=webhook_body
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert first.get_json()["idempotent"] is False
    assert first.get_json()["payment_status"] == "SUCCESS"
    assert first.get_json()["booking_status"] == "CONFIRMED"

    assert (
        second.get_json()
        ["idempotent"]
        is True
    )

    # Only one webhook_event row exists for evt-123.
    events = query_db(
        "SELECT * FROM webhook_event WHERE event_id = ?",
        ("evt-123",)
    )

    assert len(events) == 1

    unknown_payment = client.post(
        "/payments/webhook",
        json={
            "event_id": "evt-456",
            "provider_payment_id": "does-not-exist",
            "status": "SUCCESS"
        }
    )

    assert unknown_payment.status_code == 404
