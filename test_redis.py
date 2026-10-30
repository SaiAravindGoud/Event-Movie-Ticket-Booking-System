
import asyncio
from app.redis_client import redis_client


async def main():
    try:
        print("Checking Redis event cache keys...\n")

        keys = await redis_client.keys("events:*")

        if not keys:
            print("No event cache keys found.")
        else:
            for key in keys:
                value = await redis_client.get(key)
                ttl = await redis_client.ttl(key)

                print("KEY:", key)
                print("TTL:", ttl, "seconds")
                print("VALUE:", value[:200] if value else None)
                print("-" * 50)

    except Exception as error:
        print("ERROR:", repr(error))

    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
