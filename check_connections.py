
import asyncio
from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from redis import Redis
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL")


async def check_mysql():
    if not DATABASE_URL:
        print("MySQL: FAILED - DATABASE_URL is missing from .env")
        return

    engine = None
    try:
        engine = create_async_engine(DATABASE_URL)
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            print("MySQL connection:", "OK" if result.scalar() == 1 else "FAILED")
    except Exception as exc:
        print("MySQL connection: FAILED")
        print("Error type:", type(exc).__name__)
        print("Check the .env URL, password, and MySQL service.")
    finally:
        if engine is not None:
            await engine.dispose()


def check_redis():
    if not REDIS_URL:
        print("Redis: FAILED - REDIS_URL is missing from .env")
        return

    client = None
    try:
        client = Redis.from_url(REDIS_URL, socket_connect_timeout=5)
        print("Redis connection:", "OK" if client.ping() else "FAILED")
    except Exception as exc:
        print("Redis connection: FAILED")
        print("Error type:", type(exc).__name__)
        print("Check that Redis is running and REDIS_URL is correct.")
    finally:
        if client is not None:
            client.close()


async def main():
    await check_mysql()
    check_redis()


if __name__ == "__main__":
    asyncio.run(main())
