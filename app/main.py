
from contextlib import asynccontextmanager

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from sqlalchemy import text

from app.database import engine
from app.models import *  # noqa: F403
from app.redis_client import (
    check_redis_connection,
    close_redis_connection,
)
from app.routers.auth import router as auth_router
from app.routers.venues import router as venues_router
from app.routers.screens import router as screens_router
from app.routers.seats import router as seats_router
from app.routers.admin import router as admin_router
from app.routers.events import router as events_router
from app.routers.shows import router as shows_router
from app.routers.seat_locks import router as seat_locks_router
from app.routers.bookings import router as bookings_router
from app.routers.tickets import router as tickets_router
from app.routers.waitlist import router as waitlist_router
from app.routers.notifications import router as notifications_router
from app.routers.reports import router as reports_router

from app.services.booking_expiry_service import run_booking_expiry_job
from app.services.scheduled_jobs_service import (
    run_event_completion_job,
    run_show_completion_job,
    run_show_reminder_job,
)


scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services and start scheduled background jobs."""

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    if not await check_redis_connection():
        await engine.dispose()
        raise RuntimeError(
            "Redis is unavailable. Ensure Redis is running "
            "on the configured REDIS_URL."
        )

    common_job_settings = {
        "replace_existing": True,
        "max_instances": 1,
        "coalesce": True,
        "misfire_grace_time": 300,
    }

    scheduler.add_job(
        run_booking_expiry_job,
        trigger="interval",
        minutes=1,
        id="expire_pending_bookings",
        **common_job_settings,
    )

    scheduler.add_job(
        run_show_reminder_job,
        trigger="interval",
        minutes=30,
        id="send_show_reminders",
        **common_job_settings,
    )

    scheduler.add_job(
        run_show_completion_job,
        trigger="interval",
        minutes=15,
        id="complete_finished_shows",
        **common_job_settings,
    )

    scheduler.add_job(
        run_event_completion_job,
        trigger="cron",
        hour=0,
        minute=0,
        id="complete_finished_events",
        **common_job_settings,
    )

    scheduler.start()

    print("APScheduler started successfully.")
    print("Registered jobs:")

    for job in scheduler.get_jobs():
        print(f" - {job.id}: next run at {job.next_run_time}")

    try:
        yield
    finally:
        scheduler.shutdown(wait=False)
        await close_redis_connection()
        await engine.dispose()


app = FastAPI(
    title="Event and Movie Ticket Booking System",
    description=(
        "Backend API for event and movie discovery, seat reservations, "
        "bookings, payments, and digital tickets."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(auth_router)
app.include_router(venues_router)
app.include_router(screens_router)
app.include_router(seats_router)
app.include_router(admin_router)
app.include_router(events_router)
app.include_router(shows_router)
app.include_router(seat_locks_router)
app.include_router(bookings_router)
app.include_router(tickets_router)
app.include_router(waitlist_router)
app.include_router(notifications_router)
app.include_router(reports_router)


@app.get("/", tags=["Health"])
async def root():
    return {
        "message": "Event and Movie Ticket Booking System API",
        "status": "running",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

    redis_healthy = await check_redis_connection()

    return {
        "status": "healthy" if redis_healthy else "degraded",
        "database": "connected",
        "redis": "connected" if redis_healthy else "disconnected",
    }
