
import logging

from fastapi import HTTPException, status
from redis.asyncio import Redis


logger = logging.getLogger(__name__)


RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])

if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end

local ttl = redis.call('TTL', KEYS[1])

return {current, ttl}
"""


async def enforce_rate_limit(
    redis: Redis,
    key: str,
    limit: int = 5,
    window_seconds: int = 60,
) -> None:
    """
    Enforce a fixed-window Redis rate limit.

    Redis errors fail open so temporary Redis problems
    do not unnecessarily prevent users from using the API.
    """

    try:
        result = await redis.eval(
            RATE_LIMIT_SCRIPT,
            1,
            key,
            window_seconds,
        )

        request_count = int(result[0])
        remaining_seconds = max(int(result[1]), 1)

        if request_count > limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=(
                    f"Rate limit exceeded. Maximum "
                    f"{limit} attempts per {window_seconds} seconds."
                ),
                headers={
                    "Retry-After": str(remaining_seconds),
                },
            )

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "Redis rate-limit check failed for key %s",
            key,
        )
