"""Async client for the ProdSeller reseller API."""

import asyncio
import logging

import aiohttp

from config import PRODSELLER_API_BASE_URL, PRODSELLER_API_KEY

logger = logging.getLogger(__name__)


class ProdSellerError(Exception):
    """An API or transport error returned by ProdSeller."""

    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


def is_configured() -> bool:
    return bool(PRODSELLER_API_BASE_URL and PRODSELLER_API_KEY)


async def _request(
    method: str,
    path: str,
    *,
    payload: dict | None = None,
    idempotency_key: str | None = None,
) -> dict:
    if not is_configured():
        raise ProdSellerError("ProdSeller API is not configured.", status=0)

    headers = {
        "X-API-Key": PRODSELLER_API_KEY,
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:100]

    url = f"{PRODSELLER_API_BASE_URL}/{path.lstrip('/')}"
    timeout = aiohttp.ClientTimeout(total=20)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers, json=payload) as response:
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {"error": (await response.text())[:200]}
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ProdSellerError(
            "ProdSeller is temporarily unreachable. Please try again.",
            retryable=True,
        ) from exc

    if response.status >= 400:
        error_value = data.get("error") if isinstance(data, dict) else None
        message = str(error_value or f"ProdSeller request failed ({response.status}).")
        raise ProdSellerError(message, status=response.status, retryable=response.status >= 500)

    if not isinstance(data, dict):
        raise ProdSellerError("ProdSeller returned an invalid response.", status=response.status)
    return data


async def list_products() -> list[dict]:
    data = await _request("GET", "/products")
    products = data.get("products", [])
    if not isinstance(products, list):
        raise ProdSellerError("ProdSeller returned an invalid product list.", status=502)
    return [p for p in products if isinstance(p, dict)]


async def get_product(product_id: str) -> dict:
    data = await _request("GET", f"/products/{product_id}")
    return data


async def create_order(product_id: str, quantity: int, idempotency_key: str) -> dict:
    """Create an order, retrying transport failures with the same idempotency key."""
    last_error = None
    for attempt in range(2):
        try:
            return await _request(
                "POST",
                "/orders",
                payload={"productId": product_id, "quantity": quantity},
                idempotency_key=idempotency_key,
            )
        except ProdSellerError as exc:
            last_error = exc
            if not exc.retryable or attempt == 1:
                raise
            await asyncio.sleep(0.5)
    raise last_error or ProdSellerError("ProdSeller order failed.")
