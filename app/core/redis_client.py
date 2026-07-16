import os
import redis
from redis.asyncio import Redis as AsyncRedis

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Sync — used by Celery worker to publish
sync_redis = redis.from_url(REDIS_URL, decode_responses=True)

# Async — used by FastAPI WebSocket to subscribe
async def get_async_redis() -> AsyncRedis:
    return AsyncRedis.from_url(REDIS_URL, decode_responses=True)