
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.payment import Payment
from app.models.booking import Booking
from app.models.refund import Refund


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Payment, Booking)
            .join(Booking, Payment.booking_id == Booking.id)
            .where(Payment.id == 7)
        )

        row = result.first()

        if row is None:
            print("Payment ID 7 was not found.")
            return

        payment, booking = row

        refund_result = await db.execute(
            select(Refund).where(Refund.payment_id == payment.id)
        )
        refunds = refund_result.scalars().all()

        print("\nPAYMENT VERIFICATION")
        print("--------------------")
        print("Payment ID:", payment.id)
        print("Booking ID:", booking.id)
        print("Booking status:", booking.status)
        print("Payment amount:", payment.amount)
        print("Payment status:", payment.status)

        print("\nREFUND RECORDS")
        print("--------------")

        for refund in refunds:
            print("Refund ID:", refund.id)
            print("Amount:", refund.amount)
            print("Reason:", refund.reason)
            print("Status:", refund.status)
            print("Reference:", refund.refund_reference)

        if payment.status.lower() == "refunded" and refunds:
            print("\nSUCCESS: Payment is refunded and refund record exists.")
        else:
            print("\nCHECK REQUIRED: Payment/refund status needs investigation.")


if __name__ == "__main__":
    asyncio.run(main())
