"""Async client for the ProdSeller reseller API."""

import asyncio
import json
import logging

import aiohttp

from config import PRODSELLER_API_BASE_URL, PRODSELLER_API_KEY

logger = logging.getLogger(__name__)
PRODSELLER_OVERRIDES_KEY = "prodseller_product_overrides"
_UNSET = object()


class ProdSellerError(Exception):
    """An API or transport error returned by ProdSeller."""

    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


def is_configured() -> bool:
    return bool(PRODSELLER_API_BASE_URL and PRODSELLER_API_KEY)


def get_product_overrides(conn) -> dict:
    """Load persisted selling-price and emoji overrides keyed by supplier ID."""
    from services.database import get_bot_setting

    raw = get_bot_setting(conn, PRODSELLER_OVERRIDES_KEY, "{}") or "{}"
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("Invalid ProdSeller product overrides; using defaults")
        return {}
    return value if isinstance(value, dict) else {}


def save_product_override(conn, product_id: str, *, price=_UNSET, emoji=_UNSET) -> dict:
    """Update one supplier product override and persist the full override map."""
    from services.database import set_bot_setting

    overrides = get_product_overrides(conn)
    key = str(product_id)
    entry = dict(overrides.get(key) or {})
    if price is not _UNSET:
        if price is None:
            entry.pop("price", None)
        else:
            entry["price"] = round(float(price), 2)
    if emoji is not _UNSET:
        if emoji is None:
            entry.pop("emoji", None)
        else:
            entry["emoji"] = emoji
    if entry:
        overrides[key] = entry
    else:
        overrides.pop(key, None)
    set_bot_setting(conn, PRODSELLER_OVERRIDES_KEY, json.dumps(overrides, ensure_ascii=False))
    return entry


def apply_product_override(product: dict, overrides: dict | None = None) -> dict:
    """Return a display/sale copy with the configured price and emoji applied."""
    result = dict(product or {})
    product_id = str(result.get("id") or "")
    override = (overrides or {}).get(product_id) or {}
    try:
        supplier_price = float(result.get("price", 0))
        result["supplier_price"] = supplier_price
        result["price"] = float(override.get("price", supplier_price))
    except (TypeError, ValueError):
        result["supplier_price"] = result.get("price", 0)
    if override.get("emoji"):
        result["emoji"] = override["emoji"]
    return result


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
