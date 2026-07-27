"""Async client for the Cluster Shop Developer API.

This module is the only place where the bot talks to the external product
supplier.  The API uses a Bearer token and exposes a simple product/purchase
contract at https://pay.rapidx.me.
"""

import asyncio
import json
import logging
import time
from urllib.parse import urlencode

import aiohttp

from config import CLUSTER_SHOP_API_BASE_URL, CLUSTER_SHOP_API_KEY

logger = logging.getLogger(__name__)
CLUSTER_SHOP_OVERRIDES_KEY = "cluster_shop_product_overrides"
_UNSET = object()
_PRODUCT_CACHE_TTL = 120
_product_cache: list[dict] | None = None
_product_cache_at = 0.0

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


class ClusterShopError(Exception):
    """An API or transport error returned by Cluster Shop."""

    def __init__(self, message: str, status: int = 0, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


def is_configured() -> bool:
    return bool(CLUSTER_SHOP_API_BASE_URL and CLUSTER_SHOP_API_KEY)


def _api_root() -> str:
    return CLUSTER_SHOP_API_BASE_URL.rstrip("/") + "/api"


def _normalise_emoji(value):
    if not isinstance(value, str):
        return "📦"
    return _EMOJI_NAMES.get(value.strip().lower(), value) or "📦"


def _first(product: dict, *keys, default=None):
    for key in keys:
        if key in product and product[key] is not None:
            return product[key]
    return default


def _as_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "available"}
    return bool(value)


def _normalise_stock(product: dict):
    value = _first(
        product,
        "stock",
        "stock_count",
        "stockCount",
        "available_stock",
        "availableStock",
        "quantity_available",
        "quantity",
    )
    if isinstance(value, str):
        try:
            value = int(value)
        except ValueError:
            pass
    return value


def _normalise_product(product: dict) -> dict:
    """Map Cluster Shop product fields to the bot's existing UI fields."""
    result = dict(product or {})
    result["id"] = str(_first(result, "id", "product_id", "productId", default=""))
    result["name"] = str(_first(result, "name", "title", "product_name", default="Product"))
    result["description"] = str(
        _first(result, "description", "details", "desc", default="") or ""
    )

    try:
        price = float(
            _first(
                result,
                "price_usd",
                "price",
                "selling_price",
                "amount",
                "cost",
                default=0,
            )
        )
    except (TypeError, ValueError):
        price = 0.0

    stock = _normalise_stock(result)
    explicit_in_stock = _first(result, "inStock", "in_stock", "available")
    if explicit_in_stock is None:
        in_stock = stock is None or stock > 0
    else:
        in_stock = _as_bool(explicit_in_stock)

    # Cluster Shop applies a 6% API discount at purchase time.  The displayed
    # product price is therefore the customer's default price, while this
    # value is the minimum safe local selling price.
    result["price"] = price
    result["price_usd"] = price
    result["standard_price_usd"] = price
    result["supplier_price"] = round(price * 0.94, 2)
    result["stock"] = stock
    result["inStock"] = in_stock
    result["manual_delivery"] = _as_bool(result.get("manual_delivery"), False)
    result["delivery_type"] = result.get("delivery_type") or "automatic"
    result["emoji"] = _normalise_emoji(result.get("emoji", "📦"))
    return result


def _extract_products(data) -> list[dict]:
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    if not isinstance(data, dict):
        return []
    for key in ("products", "product", "items", "results", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return [p for p in value if isinstance(p, dict)]
        if isinstance(value, dict):
            nested = _extract_products(value)
            if nested:
                return nested
    if any(key in data for key in ("id", "product_id", "productId")):
        return [data]
    return []


def _normalise_order(response) -> dict:
    """Normalize common delivery envelopes without exposing raw API data."""
    order = response if isinstance(response, dict) else {"response": response}
    for key in ("order", "purchase", "result", "data"):
        if isinstance(order.get(key), dict):
            order = dict(order[key])
            break
    order = dict(order)

    values = []

    def collect(value, key=""):
        if isinstance(value, list):
            for item in value:
                collect(item, key)
        elif isinstance(value, dict):
            for child_key, child_value in value.items():
                if child_key.lower() in {
                    "account_data", "account", "credential", "credentials", "key",
                    "code", "content", "delivery", "delivered_key", "deliveredkey",
                    "number", "phone", "username", "password", "accountdata",
                }:
                    collect(child_value, child_key)
                elif child_key.lower() in {"items", "accounts", "keys", "results", "data"}:
                    collect(child_value, child_key)
        elif value is not None and key:
            text = str(value).strip()
            if text and text not in values:
                values.append(text)

    collect(order)
    order["orderId"] = _first(order, "id", "order_id", "orderId")
    order["deliveredKeys"] = values
    if values:
        order["deliveredKey"] = values[0]
    return order


def get_product_overrides(conn) -> dict:
    from services.database import get_bot_setting

    raw = get_bot_setting(conn, CLUSTER_SHOP_OVERRIDES_KEY, "{}") or "{}"
    try:
        value = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("Invalid Cluster Shop product overrides; using defaults")
        return {}
    return value if isinstance(value, dict) else {}


def save_product_override(conn, product_id: str, *, price=_UNSET, emoji=_UNSET, description=_UNSET) -> dict:
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
    if description is not _UNSET:
        if description is None:
            entry.pop("description", None)
        else:
            entry["description"] = str(description)
    if entry:
        overrides[key] = entry
    else:
        overrides.pop(key, None)
    set_bot_setting(conn, CLUSTER_SHOP_OVERRIDES_KEY, json.dumps(overrides, ensure_ascii=False))
    return entry


def apply_product_override(product: dict, overrides: dict | None = None) -> dict:
    result = _normalise_product(product)
    override = (overrides or {}).get(str(result.get("id") or "")) or {}
    if "price" in override:
        try:
            result["price"] = float(override["price"])
        except (TypeError, ValueError):
            pass
    if override.get("emoji"):
        result["emoji"] = override["emoji"]
    if "description" in override:
        result["description"] = override["description"]
    return result


async def _request(method: str, path: str, *, payload=None, query=None) -> object:
    if not is_configured():
        raise ClusterShopError("Cluster Shop API is not configured.", status=0)

    headers = {
        "Authorization": f"Bearer {CLUSTER_SHOP_API_KEY}",
        "Accept": "application/json",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"

    url = f"{_api_root()}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"

    try:
        # The live Cluster Shop catalog endpoint currently takes roughly
        # 45 seconds to respond.  Keep enough headroom for that provider
        # latency while caching the result for subsequent menu taps.
        timeout = aiohttp.ClientTimeout(total=90)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.request(method, url, headers=headers, json=payload) as response:
                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {"error": (await response.text())[:300]}
    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        raise ClusterShopError(
            "Cluster Shop is temporarily unreachable. Please try again.", retryable=True
        ) from exc

    if response.status >= 400:
        if isinstance(data, dict):
            message = data.get("detail") or data.get("message") or data.get("error")
            if isinstance(message, list):
                message = "; ".join(str(item) for item in message)
        else:
            message = None
        message = str(message or f"Cluster Shop request failed ({response.status}).")
        raise ClusterShopError(
            message,
            status=response.status,
            retryable=response.status in {408, 429} or response.status >= 500,
        )
    return data


async def get_reseller_account() -> dict:
    """Return the API user's account and wallet balance."""
    data = await _request("GET", "/me")
    if isinstance(data, dict):
        result = dict(data)
        result.setdefault("wallet_balance", result.get("balance", 0))
        result.setdefault("key_name", result.get("username", "Cluster Shop"))
        return result
    return {"key_name": "Cluster Shop", "wallet_balance": 0, "response": data}


async def list_products() -> list[dict]:
    global _product_cache, _product_cache_at
    now = time.monotonic()
    if _product_cache is not None and now - _product_cache_at < _PRODUCT_CACHE_TTL:
        return [dict(product) for product in _product_cache]

    data = await _request("GET", "/products", query={"include_manual": "false"})
    products = [
        _normalise_product(p)
        for p in _extract_products(data)
        if not _as_bool(p.get("manual_delivery"), False)
    ]
    _product_cache = products
    _product_cache_at = time.monotonic()
    return [dict(product) for product in products]


async def get_product(product_id: str) -> dict:
    try:
        data = await _request("GET", f"/products/{int(product_id)}")
        products = _extract_products(data)
        if products:
            return _normalise_product(products[0])
    except (ValueError, ClusterShopError) as exc:
        if isinstance(exc, ClusterShopError) and exc.status not in {404, 405}:
            raise

    for product in await list_products():
        if str(product.get("id")) == str(product_id):
            return product
    raise ClusterShopError("Product not found.", status=404)


async def create_order(
    product_id: str,
    quantity: int,
    idempotency_key: str | None = None,
    *,
    activation_identifier: str | None = None,
    customer_reference: str | None = None,
) -> dict:
    """Purchase through Cluster Shop.

    The documented endpoint has no idempotency contract.  This request is
    intentionally not retried automatically because retrying after a timeout
    could create a second paid order.
    """
    if int(quantity) < 1:
        raise ClusterShopError("Quantity must be at least 1.", status=422)
    response = await _request(
        "POST",
        "/purchase",
        payload={"product_id": int(product_id), "quantity": int(quantity)},
    )
    return _normalise_order(response)
