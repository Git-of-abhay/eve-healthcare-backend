import os

os.environ["DATABASE_URL"] = "sqlite:///test_eve.db"
os.environ["JWT_SECRET_KEY"] = "test-secret"

import pytest

from app import app, db


@pytest.fixture(autouse=True)
def database():

    app.config["TESTING"] = True

    with app.app_context():
        db.drop_all()
        db.create_all()

    yield

    with app.app_context():
        db.session.remove()
        db.drop_all()


def create_user_and_login(client):

    client.post(
        "/auth/register",
        json={
            "email": "abhay@example.com",
            "password": "StrongPass123"
        }
    )

    response = client.post(
        "/auth/login",
        json={
            "email": "abhay@example.com",
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

    response = client.post(
        "/auth/login",
        json={
            "email": "abhay@example.com",
            "password": "StrongPass123"
        }
    )

    assert response.status_code == 200

    assert "access_token" in (
        response.get_json()
    )


def test_booking_and_payment():

    client = app.test_client()

    headers = create_user_and_login(
        client
    )

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
            "price": 700
        }
    ).get_json()

    booking = client.post(
        "/bookings",
        headers=headers,
        json={
            "centre_id":
                centre["id"],

            "test_id":
                diagnostic_test["id"],

            "appointment_at":
                "2026-10-01T10:30:00"
        }
    )

    assert booking.status_code == 201

    booking_id = (
        booking.get_json()
        ["booking"]
        ["id"]
    )

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


def test_webhook_idempotency():

    client = app.test_client()

    headers = create_user_and_login(
        client
    )

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
            "name": "Blood Test",
            "price": 1000
        }
    ).get_json()

    booking = client.post(
        "/bookings",
        headers=headers,
        json={
            "centre_id": centre["id"],
            "test_id": diagnostic_test["id"],
            "appointment_at":
                "2026-10-02T11:00:00"
        }
    ).get_json()

    payment = client.post(
        "/payments",
        headers=headers,
        json={
            "booking_id":
                booking["booking"]["id"],

            "status":
                "SUCCESS"
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
        "/payments/webhook",
        json=webhook_body
    )

    assert first.status_code == 200
    assert second.status_code == 200

    assert (
        second.get_json()
        ["idempotent"]
        is True
    )