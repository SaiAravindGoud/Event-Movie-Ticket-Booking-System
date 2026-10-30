
import os

from dotenv import load_dotenv
from redis.asyncio import Redis

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

redis_client: Redis = Redis.from_url(
    REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
    health_check_interval=30,
)


async def get_redis() -> Redis:
    """Return the shared asynchronous Redis client."""
    return redis_client


async def check_redis_connection() -> bool:
    """Check whether Redis is reachable."""
    try:
        return bool(await redis_client.ping())
    except Exception:
        return False


async def close_redis_connection() -> None:
    """Close the shared Redis connection pool."""
    await redis_client.aclose()
