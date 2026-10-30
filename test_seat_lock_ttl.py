
import asyncio

from app.redis_client import redis_client
from app.services.seat_lock_service import seat_lock_key


async def main():
    try:
        show_id = 2
        seat_id = 6
        key = seat_lock_key(show_id, seat_id)

        ttl = await redis_client.ttl(key)

        print(f"Redis key: {key}")
        print(f"Remaining TTL: {ttl} seconds")

        if 0 < ttl <= 600:
            print("PASS: The active seat lock has an expiration.")
        elif ttl == -2:
            print("FAIL: The Redis lock key does not exist.")
        elif ttl == -1:
            print("FAIL: The lock exists without an expiration.")
        else:
            print("CHECK: Unexpected TTL value.")
    finally:
        await redis_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
