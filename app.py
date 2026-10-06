# =========================================================
# EVE Healthcare Backend - Flask + raw SQLite (no ORM)
# Flow: HTTP request -> route -> validation -> get_db() -> raw SQL -> SQLite -> JSON
# =========================================================

import os
import random
import sqlite3
import uuid

from datetime import datetime

from flask import Flask, g, jsonify, request

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


# =========================================================
# PART 1 - Flask and SQLite setup
# =========================================================

# We create the Flask app; every route below is attached to it.
app = Flask(__name__)

# SQLite keeps the whole database in one file.
# In tests we point this at "test_eve.db" instead.
app.config["DATABASE"] = os.getenv(
    "DATABASE_PATH",
    "eve.db"
)

# We sign JWT tokens with this secret (fallback is for local dev only).
app.config["JWT_SECRET_KEY"] = os.getenv(
    "JWT_SECRET_KEY",
    "eve-development-secret-key"
)

# We plug JWT support (create + verify tokens) into Flask.
jwt = JWTManager(app)


# =========================================================
# PART 2 - Database connection
# =========================================================

# We open one SQLite connection per request and keep it on Flask's "g".
# Every route calls get_db() and gets that same connection.
def get_db():

    if "db" not in g:

        # We open the DB file (SQLite creates it if missing).
        g.db = sqlite3.connect(app.config["DATABASE"])

        # Now we can read columns by name: row["email"] instead of row[1].
        g.db.row_factory = sqlite3.Row

        # SQLite ignores FOREIGN KEYs unless we turn them on, per connection.
        g.db.execute("PRAGMA foreign_keys = ON")

    return g.db


# Flask calls this when the request ends, so we close the connection here.
# Anything we didn't commit is thrown away on close.
@app.teardown_appcontext
def close_db(exception):

    conn = g.pop("db", None)

    if conn is not None:
        conn.close()


# =========================================================
# PART 3 - Creating relational tables
# =========================================================

# We define every table in plain SQL (PRIMARY KEY, FOREIGN KEY, UNIQUE, NOT NULL).
# SQLite has no DECIMAL, so we store money as REAL.
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS user (
    id            INTEGER PRIMARY KEY,
    email         TEXT    NOT NULL UNIQUE,
    password_hash TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS diagnostic_centre (
    id       INTEGER PRIMARY KEY,
    name     TEXT    NOT NULL,
    location TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS diagnostic_test (
    id        INTEGER PRIMARY KEY,
    name      TEXT    NOT NULL,
    price     REAL    NOT NULL CHECK (price > 0),
    centre_id INTEGER NOT NULL,
    FOREIGN KEY (centre_id) REFERENCES diagnostic_centre (id)
);

CREATE TABLE IF NOT EXISTS booking (
    id             INTEGER PRIMARY KEY,
    user_id        INTEGER NOT NULL,
    test_id        INTEGER NOT NULL,
    centre_id      INTEGER NOT NULL,
    appointment_at TEXT    NOT NULL,
    amount         REAL    NOT NULL,
    status         TEXT    NOT NULL DEFAULT 'PENDING'
                   CHECK (status IN ('PENDING', 'CONFIRMED', 'FAILED', 'CANCELLED')),
    created_at     TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id)   REFERENCES user (id),
    FOREIGN KEY (test_id)   REFERENCES diagnostic_test (id),
    FOREIGN KEY (centre_id) REFERENCES diagnostic_centre (id)
);

CREATE TABLE IF NOT EXISTS payment (
    id                  INTEGER PRIMARY KEY,
    booking_id          INTEGER NOT NULL,
    provider_payment_id TEXT    NOT NULL UNIQUE,
    amount              REAL    NOT NULL,
    status              TEXT    NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    created_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (booking_id) REFERENCES booking (id)
);

-- event_id is UNIQUE: this is the webhook idempotency key.
CREATE TABLE IF NOT EXISTS webhook_event (
    id           INTEGER PRIMARY KEY,
    event_id     TEXT    NOT NULL UNIQUE,
    payment_id   INTEGER NOT NULL,
    status       TEXT    NOT NULL CHECK (status IN ('SUCCESS', 'FAILED')),
    processed_at TEXT    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (payment_id) REFERENCES payment (id)
);

-- We index columns we often search by, so lookups stay fast.
CREATE INDEX IF NOT EXISTS idx_booking_user_id     ON booking (user_id);
CREATE INDEX IF NOT EXISTS idx_test_centre_id      ON diagnostic_test (centre_id);
CREATE INDEX IF NOT EXISTS idx_payment_booking_id  ON payment (booking_id);
"""


# We create all tables on startup (and before each test).
# "IF NOT EXISTS" makes it safe to run again.
def init_db():

    conn = sqlite3.connect(app.config["DATABASE"])

    # We run all the ";"-separated statements in one go.
    conn.executescript(SCHEMA_SQL)

    conn.commit()
    conn.close()


# We can also create tables from the terminal: flask --app app init-db
@app.cli.command("init-db")
def init_db_command():

    init_db()
    print("Initialized the database.")


# =========================================================
# PART 4 - Helper functions
# =========================================================

# We read the logged-in user's id from the JWT.
# We stored it as a string, so we turn it back into an int.
def get_user_id():
    return int(get_jwt_identity())


# We load one booking plus its test and centre names in one JOIN query.
# We get None back if the booking doesn't exist.
def get_booking_details(conn, booking_id):

    # JOIN lets us get booking + test + centre information together.
    # b/t/c are table aliases; AS renames columns so they don't clash.
    return conn.execute(
        """
        SELECT b.id, b.user_id, b.appointment_at, b.amount, b.status,
               t.id AS test_id,   t.name AS test_name,
               c.id AS centre_id, c.name AS centre_name
        FROM booking b
        JOIN diagnostic_test   t ON t.id = b.test_id
        JOIN diagnostic_centre c ON c.id = b.centre_id
        WHERE b.id = ?
        """,
        (booking_id,)
    ).fetchone()


# We turn a booking row into the JSON shape every booking route returns.
def booking_json(row):

    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "test": {
            "id": row["test_id"],
            "name": row["test_name"]
        },
        "centre": {
            "id": row["centre_id"],
            "name": row["centre_name"]
        },
        "appointment_at": row["appointment_at"],
        "amount": float(row["amount"]),
        "status": row["status"]
    }


# GET /health: we just confirm the server is up, no DB involved.
@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "status": "ok"
    }), 200


# =========================================================
# PART 5 - Registration
# =========================================================

# POST /auth/register: we validate input, hash the password and INSERT the user.
# Shows SELECT, INSERT, commit() and the UNIQUE email rule.
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

    conn = get_db()

    # We SELECT to check if the email is taken.
    # ? is a placeholder: we pass values separately, which blocks SQL injection.
    existing_user = conn.execute(
        "SELECT id FROM user WHERE email = ?",
        (email,)
    ).fetchone()

    if existing_user:
        return jsonify({
            "error": "User already exists"
        }), 409

    # We never store the plain password, only a one-way hash.
    password_hash = generate_password_hash(password)

    try:
        # We INSERT a new row into the user table.
        cursor = conn.execute(
            """
            INSERT INTO user (email, password_hash)
            VALUES (?, ?)
            """,
            (email, password_hash)
        )

        # commit() permanently saves our changes.
        conn.commit()

    except sqlite3.IntegrityError:
        # Two same-email requests at once can both pass our SELECT; UNIQUE stops the 2nd.
        # rollback() cancels our failed transaction.
        conn.rollback()

        return jsonify({
            "error": "User already exists"
        }), 409

    return jsonify({
        "message": "User registered successfully",
        # lastrowid is the id SQLite gave the row we just inserted.
        "user_id": cursor.lastrowid,
        "email": email
    }), 201


# =========================================================
# PART 6 - Login and JWT
# =========================================================

# POST /auth/login: we find the user with SQL, check the password,
# and hand back a JWT if it matches.
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

    conn = get_db()

    # fetchone() gives us the first matching row, or None.
    user = conn.execute(
        "SELECT id, password_hash FROM user WHERE email = ?",
        (email,)
    ).fetchone()

    # We return the same error for both cases so nobody can probe which emails exist.
    if not user:
        return jsonify({
            "error": "Invalid credentials"
        }), 401

    # We hash the given password the same way and compare it to the stored hash.
    if not check_password_hash(
        user["password_hash"],
        password
    ):
        return jsonify({
            "error": "Invalid credentials"
        }), 401

    # We sign a token holding the user id; the client sends it back
    # in the Authorization header on later requests.
    access_token = create_access_token(
        identity=str(user["id"])
    )

    return jsonify({
        "message": "Login successful",
        "access_token": access_token
    }), 200


# GET /auth/me: we return the logged-in user.
# @jwt_required() rejects us with 401 if the token is missing or bad.
@app.route("/auth/me", methods=["GET"])
@jwt_required()
def me():

    user_id = get_user_id()

    conn = get_db()

    # We select only what we need and never send password_hash out.
    user = conn.execute(
        "SELECT id, email FROM user WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not user:
        return jsonify({
            "error": "User not found"
        }), 404

    return jsonify({
        "id": user["id"],
        "email": user["email"]
    }), 200


# =========================================================
# PART 7 - Centres and diagnostic tests
# =========================================================

# POST /centres: we create a diagnostic centre with a simple INSERT.
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

    conn = get_db()

    cursor = conn.execute(
        """
        INSERT INTO diagnostic_centre (name, location)
        VALUES (?, ?)
        """,
        (name, location)
    )

    conn.commit()

    return jsonify({
        "message": "Centre created",
        "id": cursor.lastrowid,
        "name": name,
        "location": location
    }), 201


# GET /centres: we list every centre with its tests.
# Shows LEFT JOIN, which keeps centres that have no tests yet.
@app.route("/centres", methods=["GET"])
def get_centres():

    conn = get_db()

    # A plain JOIN would drop centres with no tests; LEFT JOIN keeps them (tests = NULL).
    # One query instead of one per centre.
    rows = conn.execute(
        """
        SELECT c.id       AS centre_id,
               c.name     AS centre_name,
               c.location AS centre_location,
               t.id       AS test_id,
               t.name     AS test_name,
               t.price    AS test_price
        FROM diagnostic_centre c
        LEFT JOIN diagnostic_test t ON t.centre_id = c.id
        ORDER BY c.id, t.id
        """
    ).fetchall()

    # We get one row per (centre, test) pair, so we group them by centre.
    centres_by_id = {}

    for row in rows:

        centre_id = row["centre_id"]

        if centre_id not in centres_by_id:
            centres_by_id[centre_id] = {
                "id": centre_id,
                "name": row["centre_name"],
                "location": row["centre_location"],
                "tests": []
            }

        # test_id is None when the centre has no tests.
        if row["test_id"] is not None:
            centres_by_id[centre_id]["tests"].append({
                "id": row["test_id"],
                "name": row["test_name"],
                "price": float(row["test_price"])
            })

    return jsonify({
        "centres": list(centres_by_id.values())
    }), 200


# POST /centres/<id>/tests: we add a priced test to an existing centre.
# We check the parent row exists before inserting the child row.
@app.route(
    "/centres/<int:centre_id>/tests",
    methods=["POST"]
)
@jwt_required()
def create_test(centre_id):

    conn = get_db()

    centre = conn.execute(
        "SELECT id FROM diagnostic_centre WHERE id = ?",
        (centre_id,)
    ).fetchone()

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

    # centre_id is a FOREIGN KEY, so SQLite rejects ids that don't exist.
    cursor = conn.execute(
        """
        INSERT INTO diagnostic_test (name, price, centre_id)
        VALUES (?, ?, ?)
        """,
        (name, price, centre_id)
    )

    conn.commit()

    return jsonify({
        "message": "Diagnostic test created",
        "id": cursor.lastrowid,
        "name": name,
        "price": price,
        "centre_id": centre_id
    }), 201


# GET /centres/<id>/tests: we list one centre's tests by filtering on centre_id.
@app.route(
    "/centres/<int:centre_id>/tests",
    methods=["GET"]
)
def centre_tests(centre_id):

    conn = get_db()

    centre = conn.execute(
        "SELECT id, name FROM diagnostic_centre WHERE id = ?",
        (centre_id,)
    ).fetchone()

    if not centre:
        return jsonify({
            "error": "Centre not found"
        }), 404

    # fetchall() gives us a list of every matching row.
    tests = conn.execute(
        """
        SELECT id, name, price
        FROM diagnostic_test
        WHERE centre_id = ?
        ORDER BY id
        """,
        (centre_id,)
    ).fetchall()

    return jsonify({
        "centre": centre["name"],

        "tests": [
            {
                "id": test["id"],
                "name": test["name"],
                "price": float(test["price"])
            }

            for test in tests
        ]
    }), 200


# =========================================================
# PART 8 - Bookings
# =========================================================

# POST /bookings: we create a PENDING booking for the logged-in user.
# We take the amount from the DB price, never from the client.
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

    conn = get_db()

    test = conn.execute(
        "SELECT id, price, centre_id FROM diagnostic_test WHERE id = ?",
        (test_id,)
    ).fetchone()

    if not test:
        return jsonify({
            "error": "Diagnostic test not found"
        }), 404

    centre = conn.execute(
        "SELECT id FROM diagnostic_centre WHERE id = ?",
        (centre_id,)
    ).fetchone()

    if not centre:
        return jsonify({
            "error": "Centre not found"
        }), 404

    if test["centre_id"] != centre["id"]:
        return jsonify({
            "error":
            "This diagnostic test is not offered by this centre"
        }), 400

    try:
        appointment = datetime.fromisoformat(
            appointment_at
        )

    except (TypeError, ValueError):
        return jsonify({
            "error":
            "Invalid appointment date. Use ISO format."
        }), 400

    # SQLite has no DATETIME, so we store ISO text like "2026-10-01T10:30:00".
    cursor = conn.execute(
        """
        INSERT INTO booking (user_id, test_id, centre_id, appointment_at, amount, status)
        VALUES (?, ?, ?, ?, ?, 'PENDING')
        """,
        (
            user_id,
            test["id"],
            centre["id"],
            appointment.isoformat(),

            # We never trust an amount sent by the client.
            test["price"],
        )
    )

    conn.commit()

    booking = get_booking_details(conn, cursor.lastrowid)

    return jsonify({
        "message": "Booking created",
        "booking": booking_json(booking)
    }), 201


# GET /bookings: we return only the logged-in user's bookings (JOIN + WHERE).
@app.route("/bookings", methods=["GET"])
@jwt_required()
def my_bookings():

    user_id = get_user_id()

    conn = get_db()

    # Same JOIN as get_booking_details(), but we filter by user instead of id.
    rows = conn.execute(
        """
        SELECT b.id, b.user_id, b.appointment_at, b.amount, b.status,
               t.id AS test_id,   t.name AS test_name,
               c.id AS centre_id, c.name AS centre_name
        FROM booking b
        JOIN diagnostic_test   t ON t.id = b.test_id
        JOIN diagnostic_centre c ON c.id = b.centre_id
        WHERE b.user_id = ?
        ORDER BY b.id
        """,
        (user_id,)
    ).fetchall()

    return jsonify({
        "bookings": [
            booking_json(row)
            for row in rows
        ]
    }), 200


# GET /bookings/<id>: we return one booking, only if the user owns it.
# Being logged in isn't enough; we also check ownership.
@app.route(
    "/bookings/<int:booking_id>",
    methods=["GET"]
)
@jwt_required()
def get_booking(booking_id):

    user_id = get_user_id()

    conn = get_db()

    booking = get_booking_details(conn, booking_id)

    if not booking:
        return jsonify({
            "error": "Booking not found"
        }), 404

    # Authorization: we only show users their own bookings.
    if booking["user_id"] != user_id:
        return jsonify({
            "error": "Not authorized"
        }), 403

    return jsonify(
        booking_json(booking)
    ), 200


# POST /bookings/<id>/cancel: we mark the user's own booking as CANCELLED.
@app.route(
    "/bookings/<int:booking_id>/cancel",
    methods=["POST"]
)
@jwt_required()
def cancel_booking(booking_id):

    user_id = get_user_id()

    conn = get_db()

    booking = conn.execute(
        "SELECT id, user_id, status FROM booking WHERE id = ?",
        (booking_id,)
    ).fetchone()

    if not booking:
        return jsonify({
            "error": "Booking not found"
        }), 404

    if booking["user_id"] != user_id:
        return jsonify({
            "error": "Not authorized"
        }), 403

    if booking["status"] == "CANCELLED":
        return jsonify({
            "message": "Booking already cancelled"
        }), 200

    # We UPDATE the existing row; without WHERE we'd change EVERY booking.
    conn.execute(
        "UPDATE booking SET status = 'CANCELLED' WHERE id = ?",
        (booking_id,)
    )

    conn.commit()

    return jsonify({
        "message": "Booking cancelled",
        "booking": booking_json(get_booking_details(conn, booking_id))
    }), 200


# =========================================================
# PART 9 - Payments and transactions
# =========================================================

# POST /payments: we INSERT a payment and UPDATE the booking in ONE transaction,
# so both are saved or neither is (atomicity).
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

    conn = get_db()

    try:
        # ---------------------------------------------
        # BEGIN
        # ---------------------------------------------
        # We start a transaction and grab the write lock now, so a second
        # payment for the same booking waits here until we COMMIT.
        conn.execute("BEGIN IMMEDIATE")

        # ---------------------------------------------
        # SELECT - read the current state
        # ---------------------------------------------
        booking = conn.execute(
            "SELECT id, user_id, amount, status FROM booking WHERE id = ?",
            (booking_id,)
        ).fetchone()

        if not booking:
            # We wrote nothing, but we still end the transaction.
            conn.rollback()
            return jsonify({
                "error": "Booking not found"
            }), 404

        if booking["user_id"] != user_id:
            conn.rollback()
            return jsonify({
                "error": "Not authorized"
            }), 403

        if booking["status"] == "CANCELLED":
            conn.rollback()
            return jsonify({
                "error":
                "Cannot pay for a cancelled booking"
            }), 409

        if booking["status"] == "CONFIRMED":
            conn.rollback()
            return jsonify({
                "error":
                "Booking has already been confirmed"
            }), 409

        # The client can force SUCCESS/FAILED for testing; otherwise we pick randomly.
        simulated_status = data.get("status")

        if simulated_status:
            simulated_status = str(simulated_status).upper()

            if simulated_status not in [
                "SUCCESS",
                "FAILED"
            ]:
                conn.rollback()
                return jsonify({
                    "error":
                    "Payment status must be SUCCESS or FAILED"
                }), 400

        else:
            simulated_status = random.choice(
                ["SUCCESS", "FAILED"]
            )

        # We fake the gateway's payment id with a random UUID.
        provider_payment_id = str(
            uuid.uuid4()
        )

        if simulated_status == "SUCCESS":
            new_booking_status = "CONFIRMED"
        else:
            new_booking_status = "FAILED"

        # ---------------------------------------------
        # INSERT - record the payment
        # ---------------------------------------------
        cursor = conn.execute(
            """
            INSERT INTO payment (booking_id, provider_payment_id, amount, status)
            VALUES (?, ?, ?, ?)
            """,
            (
                booking["id"],
                provider_payment_id,
                booking["amount"],
                simulated_status,
            )
        )

        payment_id = cursor.lastrowid

        # ---------------------------------------------
        # UPDATE - change the booking status
        # ---------------------------------------------
        conn.execute(
            "UPDATE booking SET status = ? WHERE id = ?",
            (new_booking_status, booking["id"])
        )

        # ---------------------------------------------
        # COMMIT
        # ---------------------------------------------
        # commit() saves our INSERT and UPDATE together.
        conn.commit()

    except sqlite3.Error:
        # ---------------------------------------------
        # ROLLBACK
        # ---------------------------------------------
        # Something failed half-way, so we rollback() and undo both writes.
        conn.rollback()

        return jsonify({
            "error": "Payment could not be processed"
        }), 500

    return jsonify({
        "message": "Payment processed",
        "payment": {
            "id": payment_id,
            "provider_payment_id":
                provider_payment_id,
            "booking_id": booking["id"],
            "amount": float(booking["amount"]),
            "status": simulated_status
        },
        "booking_status": new_booking_status
    }), 200


# =========================================================
# PART 10 - Webhooks and idempotency
# =========================================================

# POST /payments/webhook: the provider tells us a payment's final status.
# Providers resend events, so we make it idempotent with a UNIQUE event_id.
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

    status = str(status).upper()

    if status not in [
        "SUCCESS",
        "FAILED"
    ]:
        return jsonify({
            "error":
            "status must be SUCCESS or FAILED"
        }), 400

    # We send this back whenever we spot a duplicate event.
    already_processed = {
        "message":
        "Webhook already processed",

        "event_id":
        event_id,

        "idempotent":
        True
    }

    conn = get_db()

    try:
        # ---------------------------------------------
        # BEGIN
        # ---------------------------------------------
        # We grab the write lock so our "seen this event?" check and INSERT
        # happen with no other writer in between.
        conn.execute("BEGIN IMMEDIATE")

        # ---------------------------------------------
        # SELECT - idempotency check
        # ---------------------------------------------
        existing_event = conn.execute(
            "SELECT id FROM webhook_event WHERE event_id = ?",
            (event_id,)
        ).fetchone()

        if existing_event:
            conn.rollback()
            return jsonify(already_processed), 200

        # ---------------------------------------------
        # SELECT - load the payment and its booking
        # ---------------------------------------------
        payment = conn.execute(
            "SELECT id, booking_id FROM payment WHERE provider_payment_id = ?",
            (provider_payment_id,)
        ).fetchone()

        if not payment:
            conn.rollback()
            return jsonify({
                "error": "Payment not found"
            }), 404

        booking = conn.execute(
            "SELECT id, status FROM booking WHERE id = ?",
            (payment["booking_id"],)
        ).fetchone()

        # ---------------------------------------------
        # INSERT - remember this event_id
        # ---------------------------------------------
        # If this event_id already exists, UNIQUE makes our INSERT fail (caught below).
        conn.execute(
            """
            INSERT INTO webhook_event (event_id, payment_id, status)
            VALUES (?, ?, ?)
            """,
            (event_id, payment["id"], status)
        )

        # ---------------------------------------------
        # UPDATE - apply the new payment status
        # ---------------------------------------------
        conn.execute(
            """
            UPDATE payment
            SET status = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (status, payment["id"])
        )

        # We never revive a booking the user already cancelled.
        new_booking_status = booking["status"]

        if booking["status"] != "CANCELLED":

            if status == "SUCCESS":
                new_booking_status = "CONFIRMED"
            else:
                new_booking_status = "FAILED"

            conn.execute(
                "UPDATE booking SET status = ? WHERE id = ?",
                (new_booking_status, booking["id"])
            )

        # ---------------------------------------------
        # COMMIT
        # ---------------------------------------------
        # We save the event, payment and booking changes together.
        conn.commit()

    except sqlite3.IntegrityError:
        # ---------------------------------------------
        # ROLLBACK - duplicate event_id
        # ---------------------------------------------
        # UNIQUE caught a duplicate, so we undo everything and report "already processed".
        conn.rollback()

        return jsonify(already_processed), 200

    except sqlite3.Error:
        # Any other DB failure: we undo everything.
        conn.rollback()

        return jsonify({
            "error": "Webhook could not be processed"
        }), 500

    return jsonify({
        "message":
        "Webhook processed successfully",

        "event_id":
        event_id,

        "payment_status":
        status,

        "booking_status":
        new_booking_status,

        "idempotent":
        False
    }), 200


# When we run "python app.py", we create the tables and start the dev server.
if __name__ == "__main__":

    init_db()

    app.run(
        debug=True,
        host="0.0.0.0",
        port=4000
    )
