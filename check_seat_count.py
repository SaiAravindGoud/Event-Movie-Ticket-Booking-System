
import asyncio

from sqlalchemy import text

from app.database import engine


async def main():
    try:
        async with engine.connect() as connection:
            result = await connection.execute(
                text("SELECT COUNT(*) FROM seats")
            )
            print("Existing seat count:", result.scalar_one())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
