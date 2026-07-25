"""Async client for the new VenteBot reseller API.

The bot keeps the old helper names so the Telegram checkout code remains
stable.  This module is the only place that knows the reseller API contract.
"""

import asyncio
import json
import logging
from urllib.parse import urlencode

import aiohttp

from config import PRODSELLER_API_BASE_URL, PRODSELLER_API_KEY

logger = logging.getLogger(__name__)
PRODSELLER_OVERRIDES_KEY = "prodseller_product_overrides"
_UNSET = object()
_catalog_cache: list[dict] | None = None
_catalog_etag: str | None = None

_EMOJI_NAMES = {
    "bolt": "⚡",
    "lightning": "⚡",
    "fire": "🔥",
    "star": "⭐",
    "gift": "🎁",
    "key": "🔑",
    "robot": "🤖",
    "rocket": "🚀",
    "crown": "👑",
    "diamond": "💎",
}


class ProdSellerError(Exception):
    """An API or transport error returned by ProdSeller."""

    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


def is_configured() -> bool:
    return bool(PRODSELLER_API_BASE_URL and PRODSELLER_API_KEY)


def _api_root() -> str:
    """Return the new API root, accepting both host-only and old /v1 config."""
    base = PRODSELLER_API_BASE_URL.rstrip("/")
    if base.endswith("/api/reseller"):
        return base
    if base.endswith("/v1"):
        base = base[:-3].rstrip("/")
    return f"{base}/api/reseller"


def _normalise_emoji(value):
    if not isinstance(value, str):
        return "📦"
    return _EMOJI_NAMES.get(value.strip().lower(), value) or "📦"


def _normalise_product(product: dict) -> dict:
    """Map the new catalog schema to the fields used by the bot UI."""
    result = dict(product or {})
    try:
        cost = float(result.get("price_usd", result.get("price", 0)))
    except (TypeError, ValueError):
        cost = 0.0
    try:
        standard = float(result.get("standard_price_usd", cost))
    except (TypeError, ValueError):
        standard = cost

    stock = result.get("stock")
    if isinstance(stock, str) and stock.isdigit():
        stock = int(stock)
    result["id"] = str(result.get("id")) if result.get("id") is not None else ""
    result["price"] = cost
    result["price_usd"] = cost
    result["standard_price_usd"] = standard
    result["supplier_price"] = cost
    result["stock"] = stock
    result["inStock"] = stock != 0
    result["emoji"] = _normalise_emoji(result.get("emoji", "📦"))
    return result


def _normalise_order(response: dict) -> dict:
    """Map the new order envelope/items to the legacy delivery fields."""
    order = response.get("order") if isinstance(response.get("order"), dict) else response
    order = dict(order or {})
    items = order.get("items") or []
    keys = [
        str(item.get("account_data"))
        for item in items
        if isinstance(item, dict) and item.get("account_data") is not None
    ]
    if not keys and order.get("account_data") is not None:
        keys = [str(order["account_data"])]
    order["orderId"] = order.get("id", order.get("orderId"))
    order["amount"] = order.get("amount_usd", order.get("total", order.get("amount")))
    order["deliveredKeys"] = keys
    if keys:
        order["deliveredKey"] = keys[0]
    return order


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
    """Return a display/sale copy with the configured price and emoji applied.

    ``price_usd`` is the reseller cost.  The API's standard price is the
    default customer price, and an admin override takes precedence.
    """
    result = _normalise_product(product)
    product_id = str(result.get("id") or "")
    override = (overrides or {}).get(product_id) or {}
    try:
        supplier_price = float(result.get("price_usd", result.get("price", 0)))
        result["supplier_price"] = supplier_price
        default_sale_price = float(result.get("standard_price_usd", supplier_price))
        result["price"] = float(override.get("price", default_sale_price))
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
    query: dict | None = None,
    extra_headers: dict | None = None,
) -> dict:
    if not is_configured():
        raise ProdSellerError("ProdSeller API is not configured.", status=0)

    headers = {
        "X-Reseller-Key": PRODSELLER_API_KEY,
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key[:100]
    if extra_headers:
        headers.update(extra_headers)

    url = f"{_api_root()}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
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

    if response.status >= 400 or (isinstance(data, dict) and data.get("success") is False):
        error_value = data.get("message") or data.get("error") if isinstance(data, dict) else None
        code = data.get("code") if isinstance(data, dict) else None
        message = str(error_value or f"Reseller API request failed ({response.status}).")
        if code:
            message = f"{code}: {message}"
        raise ProdSellerError(
            message,
            status=response.status,
            retryable=response.status in {408, 429} or response.status >= 500,
        )

    if not isinstance(data, dict):
        raise ProdSellerError("ProdSeller returned an invalid response.", status=response.status)
    return data


async def get_reseller_account() -> dict:
    """Verify the API key and return reseller identity and wallet balance."""
    return await _request("GET", "/me")


async def get_wallet_balance() -> float:
    account = await get_reseller_account()
    try:
        return float(account.get("wallet_balance", 0))
    except (TypeError, ValueError):
        raise ProdSellerError("Reseller API returned an invalid wallet balance.", status=502)


async def list_products(*, force_refresh: bool = False) -> list[dict]:
    global _catalog_cache, _catalog_etag
    if force_refresh:
        # A user-facing refresh must not reuse a previously cached catalog or
        # conditional request state.  The next response becomes the new cache.
        _catalog_cache = None
        _catalog_etag = None
    headers = {"If-None-Match": _catalog_etag} if _catalog_etag else None
    try:
        data = await _request("GET", "/products", query={"lang": "en"}, extra_headers=headers)
    except ProdSellerError as exc:
        if exc.status == 304 and _catalog_cache is not None:
            return [dict(p) for p in _catalog_cache]
        raise
    products = data.get("products", [])
    if not isinstance(products, list):
        raise ProdSellerError("ProdSeller returned an invalid product list.", status=502)
    # The reseller catalog includes a synthetic API test product. It must
    # never be exposed to Telegram customers or admin selling controls.
    _catalog_cache = [
        _normalise_product(p)
        for p in products
        if isinstance(p, dict)
        and not p.get("api_test", False)
        and str(p.get("name") or "").strip().lower() != "ventebot api test product"
    ]
    return [dict(p) for p in _catalog_cache]


async def get_product(product_id: str) -> dict:
    product_id = str(product_id)
    for product in await list_products():
        if str(product.get("id")) == product_id:
            return product
    raise ProdSellerError("Product not found.", status=404)


async def quote_product(product_id: str, quantity: int) -> dict:
    data = await _request(
        "POST", "/quote", payload={"product_id": int(product_id), "quantity": int(quantity)}
    )
    return data.get("quote", data)


async def create_order(
    product_id: str,
    quantity: int,
    idempotency_key: str,
    *,
    activation_identifier: str | None = None,
    customer_reference: str | None = None,
) -> dict:
    """Create an order, retrying transport failures with the same idempotency key."""
    last_error = None
    for attempt in range(2):
        try:
            payload = {
                "product_id": int(product_id),
                "quantity": int(quantity),
                "idempotency_key": idempotency_key[:100],
            }
            if activation_identifier:
                payload["activation_identifier"] = activation_identifier
            if customer_reference:
                payload["customer_reference"] = customer_reference
            response = await _request(
                "POST", "/orders", payload=payload, idempotency_key=idempotency_key
            )
            return _normalise_order(response)
        except ProdSellerError as exc:
            last_error = exc
            if not exc.retryable or attempt == 1:
                raise
            await asyncio.sleep(0.5)
    raise last_error or ProdSellerError("ProdSeller order failed.")


async def get_order(order_id: int | str) -> dict:
    data = await _request("GET", f"/orders/{int(order_id)}")
    return _normalise_order(data)


async def submit_activation_identifier(order_id: int | str, activation_identifier: str) -> dict:
    data = await _request(
        "POST",
        f"/orders/{int(order_id)}/activation-identifier",
        payload={"activation_identifier": activation_identifier},
    )
    return _normalise_order(data)
