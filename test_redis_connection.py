
import asyncio

from redis.asyncio import Redis


async def main():
    client = Redis.from_url(
        "redis://127.0.0.1:6379/0",
        decode_responses=True,
        socket_connect_timeout=5,
    )

    try:
        result = await client.ping()
        print("Redis connection successful:", result)

        await client.set("event_ticket_test", "working", ex=60)
        value = await client.get("event_ticket_test")
        print("Redis read/write test:", value)
    except Exception as exc:
        print("Redis connection failed:", type(exc).__name__, str(exc))
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
