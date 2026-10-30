\# Event and Movie Ticket Booking System



A backend application built with FastAPI for managing events, movie shows, venues, seats, bookings, payments, digital tickets, and reports.



\## Technology Stack



\- Python and FastAPI

\- MySQL

\- SQLAlchemy (Async)

\- Alembic database migrations

\- JWT authentication and role-based authorization

\- Redis for caching, seat locking, and rate limiting

\- WebSockets for real-time updates

\- APScheduler for background jobs

\- SMTP email notifications

\- QR-code digital tickets



\## Features



\- User registration and JWT login

\- Admin, Organizer, and Customer roles

\- Venue, screen, and seat management

\- Event and show management

\- Real-time seat locking

\- Booking and mock payment processing

\- Booking cancellation and refunds

\- Waitlist management

\- QR-code ticket generation and scanning

\- Automated booking expiry and show reminders

\- Email notifications

\- Redis caching and rate limiting

\- Admin dashboard and sales reports



\## Requirements



\- Python 3.12 or compatible version

\- MySQL

\- Redis-compatible server

\- Git



\## Installation



Clone the repository:



```bash

git clone YOUR\_GITHUB\_REPOSITORY\_URL

cd Event-Movie-Ticket-Booking-System

```



Create and activate a virtual environment:



```powershell

python -m venv venv

.\\venv\\Scripts\\Activate.ps1

```



Install dependencies:



```powershell

pip install -r requirements.txt

```



\## Environment Configuration



Create your environment file:



```powershell

Copy-Item .env.example .env

```



Update `.env` with your local MySQL credentials, JWT secrets, Redis connection, and optional SMTP configuration.



Never commit your real `.env` file or credentials.



\## Database Setup



Create the MySQL database:



```sql

CREATE DATABASE event\_ticket\_db;

```



Apply migrations:



```powershell

python -m alembic upgrade head

```



\## Start the Application



Ensure MySQL and Redis are running.



Start FastAPI:



```powershell

python -m uvicorn app.main:app --reload --reload-dir app

```



API documentation:



\- Swagger UI: http://127.0.0.1:8000/docs

\- Health check: http://127.0.0.1:8000/health



\## Background Jobs



APScheduler handles automated tasks such as:



\- Expiring pending bookings

\- Sending show reminders

\- Completing finished shows

\- Completing finished events



\## Project Structure



```text

Event-Movie-Ticket-Booking-System/

├── alembic/

│   └── versions/

├── app/

│   ├── auth/

│   ├── jobs/

│   ├── models/

│   ├── routers/

│   ├── schemas/

│   ├── services/

│   ├── utils/

│   └── websockets/

├── tests/

├── tickets/

├── .env.example

├── .gitignore

├── alembic.ini

├── requirements.txt

└── README.md

```



\## Current Project Status



Core booking, payment, ticketing, cancellation, waitlist, Redis, and reporting functionality has been implemented. Additional bonus features and expanded automated tests are planned.



\## Security



\- Store secrets in `.env`.

\- Use strong, unique JWT and QR secrets.

\- Do not commit credentials or private configuration.

\- Use test credentials and test payment integrations during development.



\## License



This project is intended for educational and portfolio purposes.

