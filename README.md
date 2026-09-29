# EVE Healthcare Backend Assignment

Backend service for diagnostic test bookings and simulated
payments.

## Tech Stack

- Python
- Flask
- Flask-SQLAlchemy
- JWT authentication
- SQLite for local development
- PostgreSQL supported using DATABASE_URL
- Pytest

## Features

- User registration
- User login
- JWT-based authentication
- Diagnostic centres
- Diagnostic tests and pricing
- Test bookings
- Booking authorization
- Simulated payments
- Payment success/failure handling
- Booking cancellation
- Idempotent payment webhook
- Request validation
- Automated tests

## Installation

Create a virtual environment:

```bash
python -m venv venv
source venv/bin/activate