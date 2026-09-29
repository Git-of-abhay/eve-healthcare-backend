import os
import random
import uuid

from datetime import datetime

from flask import Flask, jsonify, request
from flask_sqlalchemy import SQLAlchemy

from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    get_jwt_identity,
    jwt_required,
)

from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)

from sqlalchemy.exc import IntegrityError


app = Flask(__name__)



database_url = os.getenv(
    "DATABASE_URL",
    "sqlite:///eve.db"
)

# Some cloud providers still return postgres://
if database_url.startswith("postgres://"):
    database_url = database_url.replace(
        "postgres://",
        "postgresql://",
        1
    )

app.config["SQLALCHEMY_DATABASE_URI"] = database_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

app.config["JWT_SECRET_KEY"] = os.getenv(
    "JWT_SECRET_KEY",
    "eve-development-secret-key"
)


db = SQLAlchemy(app)
jwt = JWTManager(app)


# =========================================================
# MODELS
# =========================================================


class User(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True
    )

    email = db.Column(
        db.String(120),
        unique=True,
        nullable=False
    )

    password_hash = db.Column(
        db.String(300),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


class DiagnosticCentre(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(120),
        nullable=False
    )

    location = db.Column(
        db.String(200),
        nullable=False
    )


class DiagnosticTest(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True
    )

    name = db.Column(
        db.String(120),
        nullable=False
    )

    price = db.Column(
        db.Numeric(10, 2),
        nullable=False
    )

    centre_id = db.Column(
        db.Integer,
        db.ForeignKey("diagnostic_centre.id"),
        nullable=False
    )

    centre = db.relationship(
        "DiagnosticCentre",
        backref="tests"
    )


class Booking(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True
    )

    user_id = db.Column(
        db.Integer,
        db.ForeignKey("user.id"),
        nullable=False
    )

    test_id = db.Column(
        db.Integer,
        db.ForeignKey("diagnostic_test.id"),
        nullable=False
    )

    centre_id = db.Column(
        db.Integer,
        db.ForeignKey("diagnostic_centre.id"),
        nullable=False
    )

    appointment_at = db.Column(
        db.DateTime,
        nullable=False
    )

    amount = db.Column(
        db.Numeric(10, 2),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        default="PENDING",
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


class Payment(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True
    )

    booking_id = db.Column(
        db.Integer,
        db.ForeignKey("booking.id"),
        nullable=False
    )

    provider_payment_id = db.Column(
        db.String(100),
        unique=True,
        nullable=False
    )

    amount = db.Column(
        db.Numeric(10, 2),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        nullable=False
    )

    created_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )


class WebhookEvent(db.Model):
    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # This is the idempotency key.
    event_id = db.Column(
        db.String(100),
        unique=True,
        nullable=False
    )

    payment_id = db.Column(
        db.Integer,
        db.ForeignKey("payment.id"),
        nullable=False
    )

    status = db.Column(
        db.String(30),
        nullable=False
    )

    processed_at = db.Column(
        db.DateTime,
        default=datetime.utcnow
    )


# =========================================================
# HELPERS
# =========================================================


def get_user_id():
    return int(get_jwt_identity())


def booking_json(booking):

    test = db.session.get(
        DiagnosticTest,
        booking.test_id
    )

    centre = db.session.get(
        DiagnosticCentre,
        booking.centre_id
    )

    return {
        "id": booking.id,
        "user_id": booking.user_id,
        "test": {
            "id": test.id,
            "name": test.name
        },
        "centre": {
            "id": centre.id,
            "name": centre.name
        },
        "appointment_at": booking.appointment_at.isoformat(),
        "amount": float(booking.amount),
        "status": booking.status
    }


# =========================================================
# HEALTH
# =========================================================


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "ok"
    }), 200


# =========================================================
# AUTH - REGISTER
# =========================================================


@app.route("/auth/register", methods=["POST"])
def register():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({
            "error": "Email and password required"
        }), 400

    if len(password) < 8:
        return jsonify({
            "error": "Password must contain at least 8 characters"
        }), 400

    existing_user = User.query.filter_by(
        email=email
    ).first()

    if existing_user:
        return jsonify({
            "error": "User already exists"
        }), 409

    user = User(
        email=email,
        password_hash=generate_password_hash(
            password
        )
    )

    db.session.add(user)
    db.session.commit()

    return jsonify({
        "message": "User registered successfully",
        "user_id": user.id,
        "email": user.email
    }), 201


# =========================================================
# AUTH - LOGIN
# =========================================================


@app.route("/auth/login", methods=["POST"])
def login():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    email = data.get("email")
    password = data.get("password")

    if not email or not password:
        return jsonify({
            "error": "Email and password required"
        }), 400

    user = User.query.filter_by(
        email=email
    ).first()

    if not user:
        return jsonify({
            "error": "Invalid credentials"
        }), 401

    if not check_password_hash(
        user.password_hash,
        password
    ):
        return jsonify({
            "error": "Invalid credentials"
        }), 401

    access_token = create_access_token(
        identity=str(user.id)
    )

    return jsonify({
        "message": "Login successful",
        "access_token": access_token
    }), 200





@app.route("/auth/me", methods=["GET"])
@jwt_required()
def me():

    user_id = get_user_id()

    user = db.session.get(
        User,
        user_id
    )

    if not user:
        return jsonify({
            "error": "User not found"
        }), 404

    return jsonify({
        "id": user.id,
        "email": user.email
    }), 200


# =========================================================
# CREATE CENTRE
# =========================================================


@app.route("/centres", methods=["POST"])
@jwt_required()
def create_centre():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    name = data.get("name")
    location = data.get("location")

    if not name or not location:
        return jsonify({
            "error": "Name and location required"
        }), 400

    centre = DiagnosticCentre(
        name=name,
        location=location
    )

    db.session.add(centre)
    db.session.commit()

    return jsonify({
        "message": "Centre created",
        "id": centre.id,
        "name": centre.name,
        "location": centre.location
    }), 201


# =========================================================
# GET CENTRES
# =========================================================


@app.route("/centres", methods=["GET"])
def get_centres():

    centres = DiagnosticCentre.query.all()

    result = []

    for centre in centres:

        tests = []

        for test in centre.tests:
            tests.append({
                "id": test.id,
                "name": test.name,
                "price": float(test.price)
            })

        result.append({
            "id": centre.id,
            "name": centre.name,
            "location": centre.location,
            "tests": tests
        })

    return jsonify({
        "centres": result
    }), 200





@app.route(
    "/centres/<int:centre_id>/tests",
    methods=["POST"]
)
@jwt_required()
def create_test(centre_id):

    centre = db.session.get(
        DiagnosticCentre,
        centre_id
    )

    if not centre:
        return jsonify({
            "error": "Centre not found"
        }), 404

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    name = data.get("name")
    price = data.get("price")

    if not name or price is None:
        return jsonify({
            "error": "Name and price required"
        }), 400

    try:
        price = float(price)

    except (TypeError, ValueError):
        return jsonify({
            "error": "Invalid price"
        }), 400

    if price <= 0:
        return jsonify({
            "error": "Price must be positive"
        }), 400

    diagnostic_test = DiagnosticTest(
        name=name,
        price=price,
        centre_id=centre.id
    )

    db.session.add(diagnostic_test)
    db.session.commit()

    return jsonify({
        "message": "Diagnostic test created",
        "id": diagnostic_test.id,
        "name": diagnostic_test.name,
        "price": float(diagnostic_test.price),
        "centre_id": centre.id
    }), 201


# =========================================================
# GET TESTS FOR CENTRE
# =========================================================


@app.route(
    "/centres/<int:centre_id>/tests",
    methods=["GET"]
)
def centre_tests(centre_id):

    centre = db.session.get(
        DiagnosticCentre,
        centre_id
    )

    if not centre:
        return jsonify({
            "error": "Centre not found"
        }), 404

    return jsonify({
        "centre": centre.name,

        "tests": [
            {
                "id": test.id,
                "name": test.name,
                "price": float(test.price)
            }

            for test in centre.tests
        ]
    }), 200


# =========================================================
# CREATE BOOKING
# =========================================================


@app.route("/bookings", methods=["POST"])
@jwt_required()
def create_booking():

    user_id = get_user_id()

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    test_id = data.get("test_id")
    centre_id = data.get("centre_id")
    appointment_at = data.get("appointment_at")

    if not test_id or not centre_id or not appointment_at:
        return jsonify({
            "error":
            "test_id, centre_id and appointment_at required"
        }), 400

    test = db.session.get(
        DiagnosticTest,
        test_id
    )

    if not test:
        return jsonify({
            "error": "Diagnostic test not found"
        }), 404

    centre = db.session.get(
        DiagnosticCentre,
        centre_id
    )

    if not centre:
        return jsonify({
            "error": "Centre not found"
        }), 404

    if test.centre_id != centre.id:
        return jsonify({
            "error":
            "This diagnostic test is not offered by this centre"
        }), 400

    try:
        appointment = datetime.fromisoformat(
            appointment_at
        )

    except ValueError:
        return jsonify({
            "error":
            "Invalid appointment date. Use ISO format."
        }), 400

    booking = Booking(
        user_id=user_id,
        test_id=test.id,
        centre_id=centre.id,
        appointment_at=appointment,

        # Never trust amount supplied by client.
        amount=test.price,

        status="PENDING"
    )

    db.session.add(booking)
    db.session.commit()

    return jsonify({
        "message": "Booking created",
        "booking": booking_json(booking)
    }), 201


# =========================================================
# MY BOOKINGS
# =========================================================


@app.route("/bookings", methods=["GET"])
@jwt_required()
def my_bookings():

    user_id = get_user_id()

    bookings = Booking.query.filter_by(
        user_id=user_id
    ).all()

    return jsonify({
        "bookings": [
            booking_json(booking)
            for booking in bookings
        ]
    }), 200





@app.route(
    "/bookings/<int:booking_id>",
    methods=["GET"]
)
@jwt_required()
def get_booking(booking_id):

    user_id = get_user_id()

    booking = db.session.get(
        Booking,
        booking_id
    )

    if not booking:
        return jsonify({
            "error": "Booking not found"
        }), 404

    # Authorization
    if booking.user_id != user_id:
        return jsonify({
            "error": "Not authorized"
        }), 403

    return jsonify(
        booking_json(booking)
    ), 200





@app.route(
    "/bookings/<int:booking_id>/cancel",
    methods=["POST"]
)
@jwt_required()
def cancel_booking(booking_id):

    user_id = get_user_id()

    booking = db.session.get(
        Booking,
        booking_id
    )

    if not booking:
        return jsonify({
            "error": "Booking not found"
        }), 404

    if booking.user_id != user_id:
        return jsonify({
            "error": "Not authorized"
        }), 403

    if booking.status == "CANCELLED":
        return jsonify({
            "message": "Booking already cancelled"
        }), 200

    booking.status = "CANCELLED"

    db.session.commit()

    return jsonify({
        "message": "Booking cancelled",
        "booking": booking_json(booking)
    }), 200




@app.route("/payments", methods=["POST"])
@app.route("/payments/", methods=["POST"])
@jwt_required()
def payment():

    user_id = get_user_id()

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    booking_id = data.get("booking_id")

    if not booking_id:
        return jsonify({
            "error": "booking_id required"
        }), 400

    booking = db.session.get(
        Booking,
        booking_id
    )

    if not booking:
        return jsonify({
            "error": "Booking not found"
        }), 404

    if booking.user_id != user_id:
        return jsonify({
            "error": "Not authorized"
        }), 403

    if booking.status == "CANCELLED":
        return jsonify({
            "error":
            "Cannot pay for a cancelled booking"
        }), 409

    if booking.status == "CONFIRMED":
        return jsonify({
            "error":
            "Booking has already been confirmed"
        }), 409

    simulated_status = data.get("status")

    if simulated_status:
        simulated_status = simulated_status.upper()

        if simulated_status not in [
            "SUCCESS",
            "FAILED"
        ]:
            return jsonify({
                "error":
                "Payment status must be SUCCESS or FAILED"
            }), 400

    else:
        simulated_status = random.choice(
            ["SUCCESS", "FAILED"]
        )

    provider_payment_id = str(
        uuid.uuid4()
    )

    payment = Payment(
        booking_id=booking.id,
        provider_payment_id=provider_payment_id,
        amount=booking.amount,
        status=simulated_status
    )

    if simulated_status == "SUCCESS":
        booking.status = "CONFIRMED"

    else:
        booking.status = "FAILED"

    db.session.add(payment)
    db.session.commit()

    return jsonify({
        "message": "Payment processed",
        "payment": {
            "id": payment.id,
            "provider_payment_id":
                payment.provider_payment_id,
            "booking_id": booking.id,
            "amount": float(payment.amount),
            "status": payment.status
        },
        "booking_status": booking.status
    }), 200




@app.route(
    "/payments/webhook",
    methods=["POST"]
)
@app.route(
    "/payments/webhook/",
    methods=["POST"]
)
def payment_webhook():

    data = request.get_json()

    if not data:
        return jsonify({
            "error": "JSON body required"
        }), 400

    event_id = data.get("event_id")

    provider_payment_id = data.get(
        "provider_payment_id"
    )

    status = data.get("status")

    if not event_id:
        return jsonify({
            "error": "event_id required"
        }), 400

    if not provider_payment_id:
        return jsonify({
            "error":
            "provider_payment_id required"
        }), 400

    if not status:
        return jsonify({
            "error": "status required"
        }), 400

    status = status.upper()

    if status not in [
        "SUCCESS",
        "FAILED"
    ]:
        return jsonify({
            "error":
            "status must be SUCCESS or FAILED"
        }), 400

    # ---------------------------------------------
    # IDEMPOTENCY CHECK
    # ---------------------------------------------

    existing_event = WebhookEvent.query.filter_by(
        event_id=event_id
    ).first()

    if existing_event:

        return jsonify({
            "message":
            "Webhook already processed",

            "event_id":
            event_id,

            "idempotent":
            True
        }), 200

    payment = Payment.query.filter_by(
        provider_payment_id=provider_payment_id
    ).first()

    if not payment:
        return jsonify({
            "error": "Payment not found"
        }), 404

    booking = db.session.get(
        Booking,
        payment.booking_id
    )

    event = WebhookEvent(
        event_id=event_id,
        payment_id=payment.id,
        status=status
    )

    payment.status = status

    if booking.status != "CANCELLED":

        if status == "SUCCESS":
            booking.status = "CONFIRMED"

        else:
            booking.status = "FAILED"

    db.session.add(event)

    try:
        db.session.commit()

    except IntegrityError:

 
        db.session.rollback()

        return jsonify({
            "message":
            "Webhook already processed",

            "event_id":
            event_id,

            "idempotent":
            True
        }), 200

    return jsonify({
        "message":
        "Webhook processed successfully",

        "event_id":
        event_id,

        "payment_status":
        payment.status,

        "booking_status":
        booking.status,

        "idempotent":
        False
    }), 200




if __name__ == "__main__":

    with app.app_context():
        db.create_all()

    app.run(
        debug=True,
        host="0.0.0.0",
        port=4000
    )
