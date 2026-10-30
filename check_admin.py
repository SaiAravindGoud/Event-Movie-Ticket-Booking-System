
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.user import User


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(User.id, User.full_name, User.email, User.role, User.is_active)
            .where(User.role == "Admin")
        )

        admins = result.all()

        if not admins:
            print("No Admin account exists.")
            return

        for admin in admins:
            print("Admin ID:", admin.id)
            print("Name:", admin.full_name)
            print("Email:", admin.email)
            print("Role:", admin.role)
            print("Active:", admin.is_active)


if __name__ == "__main__":
    asyncio.run(main())
