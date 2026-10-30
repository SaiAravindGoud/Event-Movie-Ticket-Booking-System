
import asyncio
import getpass

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import AsyncSessionLocal, engine
from app.models.user import User


async def main():
    try:
        print("Create an Admin account")
        print("-----------------------")

        full_name = input("Admin full name: ").strip()
        email = input("Admin email: ").strip().lower()
        password = getpass.getpass("Admin password: ")
        confirm_password = getpass.getpass("Confirm password: ")

        if not full_name or not email:
            print("Name and email are required.")
            return

        if len(password) < 8 or len(password) > 72:
            print("Password must contain 8 to 72 characters.")
            return

        if password != confirm_password:
            print("Passwords do not match.")
            return

        if not any(c.isalpha() for c in password) or not any(
            c.isdigit() for c in password
        ):
            print("Password must contain at least one letter and one digit.")
            return

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(User).where(User.email == email)
            )

            if result.scalar_one_or_none() is not None:
                print("An account with this email already exists.")
                return

            admin = User(
                full_name=full_name,
                email=email,
                hashed_password=hash_password(password),
                role="Admin",
                is_active=True,
            )

            db.add(admin)
            await db.commit()
            await db.refresh(admin)

            print("\nAdmin account created successfully.")
            print("Admin ID:", admin.id)
            print("Email:", admin.email)
            print("Role:", admin.role)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
