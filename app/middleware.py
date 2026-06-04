import json
import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware


logger = logging.getLogger("store_intel")
logging.basicConfig(level=logging.INFO, format="%(message)s")


class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        trace_id = str(uuid.uuid4())[:8]
        request.state.trace_id = trace_id
        started = time.monotonic()

        response = await call_next(request)

        latency_ms = int((time.monotonic() - started) * 1000)
        event_count = getattr(request.state, "event_count", None)
        store_id = request.path_params.get("id", "-")
        logger.info(
            json.dumps(
                {
                    "trace_id": trace_id,
                    "store_id": store_id,
                    "endpoint": request.url.path,
                    "latency_ms": latency_ms,
                    "event_count": event_count,
                    "status_code": response.status_code,
                }
            )
        )
        response.headers["X-Trace-Id"] = trace_id
        return response
