import time
import uuid
from collections import defaultdict

from fastapi import FastAPI, Request, Response, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Allow the grading page/browser to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Retry-After"],
)

TOTAL_ORDERS = 43
RATE_LIMIT = 17
WINDOW_SECONDS = 10

# Store created orders so the same Idempotency-Key returns the same order
idempotency_store = {}

# Store request timestamps separately for every client ID
client_requests = defaultdict(list)


@app.post("/orders")
def create_order(request: Request, response: Response):
    idempotency_key = request.headers.get("Idempotency-Key")

    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    # If this key was used before, return the same order instead of creating another
    if idempotency_key in idempotency_store:
        return idempotency_store[idempotency_key]

    order = {
        "id": str(uuid.uuid4()),
        "idempotency_key": idempotency_key,
        "status": "created",
        "created_at": time.time(),
    }

    idempotency_store[idempotency_key] = order

    return order


@app.get("/orders")
def list_orders(
    response: Response,
    limit: int = Query(default=10, ge=1),
    cursor: str | None = Query(default=None),
):
    # Cursor is simply the ID of the next order to return.
    # We hide that detail by encoding it as base64.
    import base64

    start = 1

    if cursor:
        try:
            decoded = base64.b64decode(cursor).decode()
            start = int(decoded)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid cursor")

    end = min(start + limit - 1, TOTAL_ORDERS)

    items = [
        {
            "id": order_id,
            "item": f"Order {order_id}",
            "price": order_id * 10,
        }
        for order_id in range(start, end + 1)
    ]

    next_cursor = None

    if end < TOTAL_ORDERS:
        next_id = end + 1
        next_cursor = base64.b64encode(str(next_id).encode()).decode()

    return {
        "items": items,
        "next_cursor": next_cursor,
    }


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_id = request.headers.get("X-Client-Id")

    # If no client ID is sent, use a shared fallback bucket
    client_id = client_id or "anonymous"

    now = time.time()

    # Remove timestamps older than the 10-second window
    client_requests[client_id] = [
        timestamp for timestamp in client_requests[client_id]
        if now - timestamp < WINDOW_SECONDS
    ]

    if len(client_requests[client_id]) >= RATE_LIMIT:
        oldest_request = client_requests[client_id][0]
        retry_after = max(1, int(WINDOW_SECONDS - (now - oldest_request)) + 1)

        return Response(
            content='{"detail": "Rate limit exceeded"}',
            status_code=429,
            media_type="application/json",
            headers={"Retry-After": str(retry_after)},
        )

    # Record this request
    client_requests[client_id].append(now)

    return await call_next(request)


@app.get("/")
def root():
    return {"status": "Orders API is running"}
