
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.booking import Booking
from app.models.payment import Payment


async def main():
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(
                    Booking.show_id,
                    Booking.id,
                    Booking.customer_id,
                    Booking.status,
                    Payment.id.label("payment_id"),
                    Payment.status.label("payment_status"),
                    Payment.amount,
                )
                .outerjoin(Payment, Payment.booking_id == Booking.id)
                .where(Booking.show_id.in_([2, 3]))
                .order_by(Booking.show_id, Booking.id)
            )

            rows = result.all()

            for show_id in [2, 3]:
                print(f"\nBookings for Show ID {show_id}:")
                print("-" * 65)

                show_rows = [
                    row for row in rows
                    if row.show_id == show_id
                ]

                if not show_rows:
                    print("No bookings found.")
                    continue

                for row in show_rows:
                    print(
                        f"Booking ID: {row.id} | "
                        f"Customer: {row.customer_id} | "
                        f"Booking Status: {row.status} | "
                        f"Payment ID: {row.payment_id} | "
                        f"Payment Status: {row.payment_status} | "
                        f"Amount: {row.amount}"
                    )

                print("-" * 65)

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
