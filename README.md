# EVE Healthcare Backend Assignment

Backend service for diagnostic test bookings and simulated payments, built as part of the EVE Healthcare SDE Intern assignment.

The project focuses on clean REST APIs, authentication, database modelling, authorization, payment-state handling, webhook idempotency, validation, and automated tests.

---

## Tech Stack

- Python
- Flask
- Python's built-in `sqlite3` module with raw SQL (no ORM)
- SQLite database
- Flask-JWT-Extended
- Werkzeug password hashing
- Pytest

---

## Features

- User signup
- User login
- Password hashing
- JWT-based authentication
- Diagnostic centre management
- Diagnostic test management
- Test pricing
- Diagnostic test bookings
- Booking ownership authorization
- Booking cancellation
- Simulated payment processing
- Payment success and failure states
- Idempotent payment webhook
- Request validation
- Automated tests

---

# Project Setup

## 1. Clone the repository

```bash
git clone https://github.com/Git-of-abhay/eve-healthcare-backend.git
cd eve-healthcare-backend
```

## 2. Create a virtual environment

```bash
python -m venv venv
```

Activate it:

### Linux / macOS

```bash
source venv/bin/activate
```

### Windows

```bash
venv\Scripts\activate
```

## 3. Install dependencies

```bash
pip install -r requirements.txt
```

## 4. Optional environment variables

The application works locally without additional configuration.

Environment variables can be provided to override the defaults:

```bash
export DATABASE_PATH="eve.db"
export JWT_SECRET_KEY="your-secure-secret-key"
```

If `DATABASE_PATH` is not provided, the SQLite database file `eve.db` is used.

Tables are created with raw `CREATE TABLE IF NOT EXISTS` SQL when the app starts with `python app.py`. They can also be created manually:

```bash
flask --app app init-db
```

The JWT secret included in the source is only a local-development fallback and should be replaced using an environment variable in production.

---

# Running the Application

```bash
python app.py
```

The API runs at:

```text
http://127.0.0.1:4000
```

Check that the server is running:

```bash
curl http://127.0.0.1:4000/health
```

Expected response:

```json
{
  "status": "ok"
}
```

---

# Authentication

Protected endpoints expect a JWT in the request header:

```text
Authorization: Bearer <access_token>
```

## Register

```http
POST /auth/register
```

Example:

```bash
curl -X POST http://127.0.0.1:4000/auth/register \
-H "Content-Type: application/json" \
-d '{
  "email": "user@example.com",
  "password": "StrongPass123"
}'
```

Example response:

```json
{
  "message": "User registered successfully",
  "user_id": 1,
  "email": "user@example.com"
}
```

---

## Login

```http
POST /auth/login
```

Example:

```bash
curl -X POST http://127.0.0.1:4000/auth/login \
-H "Content-Type: application/json" \
-d '{
  "email": "user@example.com",
  "password": "StrongPass123"
}'
```

Successful login returns a JWT access token.

```json
{
  "message": "Login successful",
  "access_token": "<JWT>"
}
```

---

## Current User

```http
GET /auth/me
```

Requires authentication.

---

# Diagnostic Centres

## Create Centre

```http
POST /centres
```

Requires authentication.

Example body:

```json
{
  "name": "EVE Diagnostics",
  "location": "Bengaluru"
}
```

---

## Get Centres

```http
GET /centres
```

Returns centres and the diagnostic tests offered by them.

---

# Diagnostic Tests

## Create Test

```http
POST /centres/<centre_id>/tests
```

Requires authentication.

Example:

```json
{
  "name": "Complete Blood Count",
  "price": 700
}
```

---

## Get Tests for a Centre

```http
GET /centres/<centre_id>/tests
```

---

# Booking System

Authenticated users can create and manage diagnostic test bookings.

A booking contains:

- User
- Diagnostic test
- Diagnostic centre
- Appointment date/time
- Amount
- Booking status

Supported booking states:

```text
PENDING
CONFIRMED
FAILED
CANCELLED
```

---

## Create Booking

```http
POST /bookings
```

Requires authentication.

Example:

```json
{
  "centre_id": 1,
  "test_id": 1,
  "appointment_at": "2026-10-01T10:30:00"
}
```

The booking amount is **not accepted from the client**.

Instead, the backend reads the price directly from the selected diagnostic test in the database.

This prevents users from modifying the payment amount themselves.

---

## Get My Bookings

```http
GET /bookings
```

Requires authentication.

Only bookings belonging to the authenticated user are returned.

---

## Get One Booking

```http
GET /bookings/<booking_id>
```

Requires authentication.

The backend verifies that the booking belongs to the authenticated user.

Attempting to access another user's booking returns:

```text
403 Not Authorized
```

---

## Cancel Booking

```http
POST /bookings/<booking_id>/cancel
```

Requires authentication.

Only the owner of the booking can cancel it.

---

# Simulated Payments

No real payment gateway is integrated.

The API contains a simulated payment service as required by the assignment.

## Process Payment

```http
POST /payments
```

or

```http
POST /payments/
```

Requires authentication.

Example:

```json
{
  "booking_id": 1,
  "status": "SUCCESS"
}
```

The test status may be:

```text
SUCCESS
FAILED
```

For testing, if no status is supplied, the backend randomly simulates either payment success or failure.

### On successful payment

```text
Payment → SUCCESS
Booking → CONFIRMED
```

### On failed payment

```text
Payment → FAILED
Booking → FAILED
```

A user cannot pay for another user's booking.

Cancelled bookings cannot be paid.

Confirmed bookings cannot be paid again.

---

# Payment Webhook

The project includes a simulated payment-provider webhook.

```http
POST /payments/webhook
```

or

```http
POST /payments/webhook/
```

Example request:

```json
{
  "event_id": "evt-123",
  "provider_payment_id": "payment-uuid",
  "status": "SUCCESS"
}
```

---

# Webhook Idempotency

Webhook processing is idempotent.

Each webhook event contains a unique:

```text
event_id
```

Processed event IDs are stored in the `webhook_event` table.

The `event_id` column has a database-level unique constraint.

Before processing a webhook, the application checks whether the event has already been handled.

If the same event is delivered again:

- no duplicate payment is created
- no duplicate booking is created
- the booking state is not incorrectly applied multiple times
- the endpoint safely returns success

The webhook is processed inside an explicit SQLite transaction (`BEGIN IMMEDIATE` … `COMMIT`, with `ROLLBACK` on failure), and the unique database constraint also provides protection if duplicate webhook requests arrive close together.

Example duplicate response:

```json
{
  "message": "Webhook already processed",
  "event_id": "evt-123",
  "idempotent": true
}
```

---

# Database Design

Tables are defined with raw SQL in `app.py` (`PRIMARY KEY`, `FOREIGN KEY`, `UNIQUE`, `NOT NULL`, `CHECK`). Foreign key enforcement is enabled on every connection with `PRAGMA foreign_keys = ON`.

The main tables are:

```text
User
 │
 │
Booking
 ├──────── DiagnosticTest
 │              │
 │              │
 │       DiagnosticCentre
 │
 └──────── Payment
              │
              │
        WebhookEvent
```

---

## User

Fields:

```text
id
email
password_hash
created_at
```

Emails are unique.

Passwords are hashed before being stored.

---

## DiagnosticCentre

Fields:

```text
id
name
location
```

---

## DiagnosticTest

Fields:

```text
id
name
price
centre_id
```

Each diagnostic test belongs to a centre.

---

## Booking

Fields:

```text
id
user_id
test_id
centre_id
appointment_at
amount
status
created_at
```

---

## Payment

Fields:

```text
id
booking_id
provider_payment_id
amount
status
created_at
updated_at
```

`provider_payment_id` is unique.

---

## WebhookEvent

Fields:

```text
id
event_id
payment_id
status
processed_at
```

`event_id` is unique and acts as the webhook idempotency key.

---

# Validation and Edge Cases

The implementation handles several common edge cases:

- Missing JSON body
- Missing email or password
- Password shorter than 8 characters
- Duplicate user registration
- Invalid login credentials
- Invalid centre ID
- Invalid diagnostic test ID
- Diagnostic test not offered by the selected centre
- Invalid appointment date format
- Attempt to access another user's booking
- Attempt to cancel another user's booking
- Invalid booking ID
- Invalid payment status
- Failed payments
- Payment against cancelled bookings
- Repeated payment on confirmed bookings
- Invalid payment IDs in webhooks
- Duplicate webhook events

---

# Security Decisions

## Password Storage

Passwords are hashed using Werkzeug's password hashing utilities.

Plaintext passwords are never stored in the database.

## JWT Authentication

Successful login returns a signed JWT.

Protected endpoints verify the JWT before processing the request.

## Authorization

Authentication alone is not considered sufficient.

Booking operations additionally verify:

```text
booking.user_id == authenticated_user_id
```

This prevents authenticated users from accessing or modifying another user's bookings.

## Server-Side Pricing

The backend never trusts a payment amount supplied by the client.

The booking amount comes from the diagnostic test price stored in the database.

---

# Tests

Run:

```bash
python -m pytest -v
```

Tests use a separate `test_eve.db` SQLite file that is recreated before and deleted after every test.

The automated test suite currently covers:

1. User registration, login and JWT-protected `/auth/me`
2. Centre and diagnostic test creation/listing
3. Booking and successful payment flow
4. Failed payment and booking cancellation
5. Booking ownership authorization
6. Payment webhook idempotency

Expected result:

```text
6 passed
```

---

# Main API Endpoints

| Method | Endpoint | Authentication | Purpose |
|---|---|---|---|
| GET | `/health` | No | Health check |
| POST | `/auth/register` | No | Register user |
| POST | `/auth/login` | No | Login and receive JWT |
| GET | `/auth/me` | Yes | Get logged-in user |
| GET | `/centres` | No | List diagnostic centres |
| POST | `/centres` | Yes | Create diagnostic centre |
| GET | `/centres/<id>/tests` | No | List centre tests |
| POST | `/centres/<id>/tests` | Yes | Create diagnostic test |
| GET | `/bookings` | Yes | Get user's bookings |
| POST | `/bookings` | Yes | Create booking |
| GET | `/bookings/<id>` | Yes | Get booking |
| POST | `/bookings/<id>/cancel` | Yes | Cancel booking |
| POST | `/payments` | Yes | Simulate payment |
| POST | `/payments/webhook` | No | Process payment webhook |

---

# Assumptions

- Diagnostic centre and test creation require authentication.
- A separate admin-role system was not required for this assignment.
- Payment processing is simulated and does not contact an external payment provider.
- A webhook provider generates a unique `event_id` for every logical payment event.
- Webhook payment statuses are limited to `SUCCESS` and `FAILED`.
- SQLite is sufficient for local evaluation.
- PostgreSQL would be preferred for a production environment (not currently supported).
- Appointment date/time is supplied in ISO format.
- Payments should not revive a booking that has already been cancelled.

---

# What I Would Improve With More Time

With more development time, I would add:

- Role-based authorization for administrators
- Versioned database migrations (numbered SQL migration files)
- PostgreSQL support for production
- Refresh tokens and token revocation
- Redis caching
- Rate limiting
- Structured logging
- Swagger / OpenAPI documentation
- Pagination
- Docker and docker-compose
- Celery/background workers
- Webhook retry processing
- More unit and integration tests
- Production WSGI server configuration
- CI pipeline for automated testing

---

# Repository

GitHub:

```text
https://github.com/Git-of-abhay/eve-healthcare-backend
```

---

