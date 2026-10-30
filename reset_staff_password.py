
import asyncio
from getpass import getpass

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import AsyncSessionLocal, engine
from app.models.user import User


async def main():
    email = "venue.staff@example.com"

    new_password = getpass("Enter your new Staff password: ")
    confirm_password = getpass("Confirm your new Staff password: ")

    if len(new_password) < 12:
        print("Password must contain at least 12 characters.")
        return

    if new_password != confirm_password:
        print("Passwords do not match. No changes were made.")
        return

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).where(User.email == email)
            )
            staff = result.scalar_one_or_none()

            if staff is None:
                print("Staff account was not found.")
                return

            if staff.role != "Staff":
                print("Account is not a Staff account. No changes made.")
                return

            staff.hashed_password = hash_password(new_password)
            await db.commit()

            print("Staff password reset successfully.")
            print(f"Email: {staff.email}")
            print(f"Staff ID: {staff.id}")
            print(f"Role: {staff.role}")
            print(f"Venue ID: {staff.venue_id}")

    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
