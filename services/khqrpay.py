"""KHQRPay managed ABA/KHQR checkout and transaction verification."""

import asyncio
import hashlib
import json
import logging
from urllib.parse import urlencode

import aiohttp

from config import KHQRPAY_BASE_URL

logger = logging.getLogger(__name__)
KHQRPAY_BASE = KHQRPAY_BASE_URL


def _sha1(*parts: str) -> str:
    return hashlib.sha1("".join(str(part) for part in parts).encode("utf-8")).hexdigest()


def _is_success_code(value) -> bool:
    return str(value).strip().lower() in {"0", "00", "success", "ok"}


def _transaction_is_paid(tx_data: dict) -> bool:
    """Accept paid markers used by KHQRPay and ABA responses."""
    if tx_data.get("paid") is True or tx_data.get("is_paid") is True:
        return True
    values = (
        tx_data.get("status"),
        tx_data.get("transaction_status"),
        tx_data.get("payment_status"),
        tx_data.get("payment_status_code"),
        tx_data.get("response_code"),
    )
    return any(
        str(value).strip().lower()
        in {"0", "00", "success", "successful", "paid", "completed", "approved"}
        for value in values
        if value is not None
    )


def _base_url() -> str:
    base = KHQRPAY_BASE.rstrip("/")
    for suffix in ("/api/payment-gateway/v1", "/payment-gateway/v1", "/api"):
        if base.lower().endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            break
    return base


def _api_url(profile_id: str, endpoint: str) -> str:
    """Build the documented verification URL: /api/{profile}/..."""
    profile = str(profile_id or "").strip().strip("/")
    return f"{_base_url()}/api/{profile}/payment-gateway/v1/payments/{endpoint}"


def _checkout_url(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    amount: float,
    success_url: str,
    remark: str,
) -> str:
    """Build the signed managed ABA checkout URL from the KHQRPay docs."""
    amount_str = f"{amount:.2f}"
    params = {
        "transaction_id": transaction_id,
        "amount": amount_str,
        "success_url": success_url,
        "remark": remark,
        "hash": _sha1(secret_key, transaction_id, amount_str, success_url, remark),
    }
    profile = str(profile_id or "").strip().strip("/")
    return f"{_base_url()}/api/payment/requestv2/{profile}?{urlencode(params)}"


def _render_checkout_qr(checkout_url: str):
    """Render the managed checkout URL into a Telegram-uploadable PNG."""
    import io
    import qrcode

    buffer = io.BytesIO()
    qrcode.make(checkout_url).save(buffer, format="PNG")
    buffer.seek(0)
    buffer.name = "aba-khqr-checkout.png"
    return buffer


async def create_aba_qr(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    amount: float,
    success_url: str = "https://t.me/storeaccount_bot",
    remark: str = "",
) -> dict:
    """Create a signed managed ABA/KHQR checkout and render it as a QR."""
    amount_str = f"{amount:.2f}"
    checkout_url = _checkout_url(
        profile_id,
        secret_key,
        transaction_id,
        amount,
        success_url,
        remark,
    )

    try:
        async with aiohttp.ClientSession() as session:
            # Probe the managed route before sending the QR. A redirect is
            # expected; a 404 means the merchant profile is not active.
            async with session.get(
                checkout_url,
                allow_redirects=False,
                timeout=30,
            ) as response:
                raw = await response.text()
                logger.info(
                    "KHQRPay managed checkout probe HTTP %s | txn=%s",
                    response.status,
                    transaction_id,
                )
                if response.status >= 400:
                    if response.status == 404:
                        return {
                            "success": False,
                            "error": (
                                "KHQRPay returned HTTP 404 for the managed checkout. "
                                "Use an active Profile ID from the KHQRPay dashboard and "
                                "confirm ABA/KHQR checkout access is enabled."
                            ),
                            "http_status": response.status,
                        }
                    return {
                        "success": False,
                        "error": f"KHQRPay checkout returned HTTP {response.status}",
                        "http_status": response.status,
                    }

                body = raw.lower()
                if "profile not found" in body or "invalid profile" in body:
                    return {
                        "success": False,
                        "error": "KHQRPay rejected the configured merchant profile.",
                        "http_status": response.status,
                    }

        return {
            "success": True,
            "qr_image_url": _render_checkout_qr(checkout_url),
            "qr_text": checkout_url,
            "checkout_url": checkout_url,
            "md5": "",
            "amount": amount_str,
            "transaction_id": transaction_id,
        }
    except asyncio.TimeoutError:
        return {"success": False, "error": "KHQRPay checkout timeout - try again"}
    except aiohttp.ClientError as exc:
        logger.exception("KHQRPay checkout connection error")
        return {"success": False, "error": f"KHQRPay connection error: {exc}"}
    except Exception as exc:
        logger.exception("KHQRPay checkout error")
        return {"success": False, "error": str(exc)}


async def verify_aba_payment(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
) -> dict:
    """Verify a transaction using the documented check-trans endpoint."""
    payload = {
        "transaction_id": transaction_id,
        "hash": _sha1(secret_key, transaction_id),
    }
    url = _api_url(profile_id, "check-trans")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=30) as response:
                raw = await response.text()
                try:
                    data = json.loads(raw)
                except Exception:
                    if response.status >= 400:
                        logger.warning(
                            "KHQRPay verification HTTP %s for txn=%s: %s",
                            response.status,
                            transaction_id,
                            raw[:300],
                        )
                        return {
                            "success": False,
                            "paid": False,
                            "error": f"KHQRPay verification HTTP {response.status}",
                            "http_status": response.status,
                        }
                    return {
                        "success": False,
                        "paid": False,
                        "error": "Invalid KHQRPay verification response",
                        "http_status": response.status,
                    }

                # KHQRPay may use HTTP 404 with responseCode=1 for a
                # transaction that is not paid yet. That is a normal poll
                # result, not a broken profile or network failure.
                if str(data.get("responseCode")).strip() == "1":
                    return {
                        "success": True,
                        "paid": False,
                        "status": "pending",
                        "gateway_code": data.get("responseCode"),
                    }

                if response.status >= 400:
                    logger.warning(
                        "KHQRPay verification HTTP %s for txn=%s: %s",
                        response.status,
                        transaction_id,
                        raw[:300],
                    )
                    return {
                        "success": False,
                        "paid": False,
                        "error": f"KHQRPay verification HTTP {response.status}",
                        "http_status": response.status,
                    }

                if _is_success_code(data.get("responseCode")) and data.get("data"):
                    tx_data = data["data"]
                    status = str(tx_data.get("status") or "").strip().lower()
                    return {
                        "success": True,
                        "paid": _transaction_is_paid(tx_data),
                        "amount": (
                            tx_data.get("amount")
                            or tx_data.get("paid_amount")
                            or tx_data.get("payment_amount")
                            or tx_data.get("original_amount")
                            or ""
                        ),
                        "currency": (
                            tx_data.get("currency")
                            or tx_data.get("payment_currency")
                            or tx_data.get("original_currency")
                            or ""
                        ),
                        "status": status,
                    }

                logger.info(
                    "KHQRPay transaction pending: code=%s txn=%s",
                    data.get("responseCode"),
                    transaction_id,
                )
                return {
                    "success": True,
                    "paid": False,
                    "status": "pending",
                    "gateway_code": data.get("responseCode"),
                }
    except asyncio.TimeoutError:
        return {"success": False, "paid": False, "error": "KHQRPay verification timeout"}
    except Exception as exc:
        logger.exception("KHQRPay verification error")
        return {"success": False, "paid": False, "error": str(exc)}


async def poll_aba_payment(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    timeout_seconds: int = 180,
    interval: int = 5,
) -> dict:
    """Poll ABA Pay until confirmed or timeout."""
    import time

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = await verify_aba_payment(profile_id, secret_key, transaction_id)
        if result.get("success") and result.get("paid"):
            return result
        await asyncio.sleep(interval)
    return {"success": True, "paid": False}


def get_khqrpay_config(conn) -> dict:
    """Load KHQRPay config from bot_settings, with environment fallbacks."""
    from config import KHQRPAY_ABA_URL, KHQRPAY_PROFILE_ID, KHQRPAY_SECRET_KEY
    from services.database import get_bot_setting

    return {
        "profile_id": get_bot_setting(conn, "khqrpay_profile_id", "") or KHQRPAY_PROFILE_ID or "",
        "secret_key": get_bot_setting(conn, "khqrpay_secret_key", "") or KHQRPAY_SECRET_KEY or "",
        "aba_url": get_bot_setting(conn, "khqrpay_aba_url", "") or KHQRPAY_ABA_URL or "",
    }
