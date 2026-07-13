"""
KHQRPay Payment Gateway Integration (khqr.cc)

Supports Bakong KHQR, ABA Pay, Binance Pay via a unified checkout page.
- create_checkout() → returns checkout URL (v1 — general KHQR)
- create_aba_checkout() → returns ABA Pay checkout URL (v2 — live QR + ABA deep link)
- verify_transaction() → checks if payment is completed
"""
import asyncio
import hashlib
import logging
from typing import Optional
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

KHQRPAY_BASE = "https://khqr.cc/api"
KHQRPAY_CHECKOUT_BASE = "https://checkout.khqr.cc/payment/khqrcc"


def _sha1(*parts: str) -> str:
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _build_checkout_url(endpoint: str, profile_id: str, params: dict) -> str:
    """Build a KHQRPay checkout URL with proper encoding."""
    base = f"{KHQRPAY_BASE}/{endpoint}/{profile_id}"
    return f"{base}?{urlencode(params)}"


async def create_checkout(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    amount: float,
    success_url: str = "https://t.me",
    remark: str = "",
) -> dict:
    """
    Create a KHQRPay checkout session (v1 — general KHQR/Bakong).

    Returns:
        {"success": True, "checkout_url": "https://..."}
        {"success": False, "error": "..."}
    """
    amount_str = f"{amount:.2f}"
    hash_val = _sha1(secret_key, transaction_id, amount_str, success_url, remark)

    params = {
        "transaction_id": transaction_id,
        "amount": amount_str,
        "success_url": success_url,
        "remark": remark,
        "hash": hash_val,
    }

    final_url = _build_checkout_url("payment/request", profile_id, params)

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(final_url, timeout=30, allow_redirects=False) as resp:
                if resp.status in (200, 302, 303, 307, 308):
                    return {"success": True, "checkout_url": final_url}
                try:
                    body = await resp.text()
                    logger.warning(f"KHQRPay create_checkout returned {resp.status}: {body[:200]}")
                except Exception:
                    body = ""
                return {"success": False, "error": f"Gateway error ({resp.status})" if not body else body[:200]}
    except asyncio.TimeoutError:
        logger.error("KHQRPay create_checkout timeout")
        return {"success": True, "checkout_url": final_url}
    except Exception as e:
        logger.error(f"KHQRPay create_checkout error: {e}")
        return {"success": False, "error": str(e)}


async def create_aba_checkout(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    amount: float,
    success_url: str = "https://t.me",
    remark: str = "",
) -> dict:
    """
    Create an ABA Pay checkout session (v2 — live KHQR + ABA Mobile deep link).

    Uses the /payment/requestv2 endpoint which shows:
    - Live KHQR code verified by Bakong
    - "Open in ABA Mobile" button for one-tap payment

    Returns:
        {"success": True, "checkout_url": "https://...", "direct_url": "https://checkout.khqr.cc/..."}
        {"success": False, "error": "..."}
    """
    amount_str = f"{amount:.2f}"
    # Same hash formula: sha1(secret + id + amount + url + remark)
    hash_val = _sha1(secret_key, transaction_id, amount_str, success_url, remark)

    params = {
        "transaction_id": transaction_id,
        "amount": amount_str,
        "success_url": success_url,
        "remark": remark,
        "hash": hash_val,
    }

    final_url = _build_checkout_url("payment/requestv2", profile_id, params)
    # Direct frontend checkout URL (can be used as a clean link)
    direct_url = f"{KHQRPAY_CHECKOUT_BASE}/{profile_id}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(final_url, timeout=30, allow_redirects=False) as resp:
                if resp.status in (200, 302, 303, 307, 308):
                    return {"success": True, "checkout_url": final_url, "direct_url": direct_url}
                try:
                    body = await resp.text()
                    logger.warning(f"KHQRPay create_aba_checkout returned {resp.status}: {body[:200]}")
                except Exception:
                    body = ""
                return {"success": False, "error": f"Gateway error ({resp.status})" if not body else body[:200]}
    except asyncio.TimeoutError:
        logger.error("KHQRPay create_aba_checkout timeout")
        return {"success": True, "checkout_url": final_url, "direct_url": direct_url}
    except Exception as e:
        logger.error(f"KHQRPay create_aba_checkout error: {e}")
        return {"success": False, "error": str(e)}


async def verify_transaction(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
) -> dict:
    """
    Verify a transaction status with KHQRPay.

    Returns:
        {"success": True, "paid": True/False, "amount": 10.00, ...}
        {"success": False, "error": "..."}
    """
    hash_val = _sha1(secret_key, transaction_id)
    verify_url = f"{KHQRPAY_BASE}/{profile_id}/payment-gateway/v1/payments/check-trans"

    payload = {
        "transaction_id": transaction_id,
        "hash": hash_val,
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(verify_url, data=payload, timeout=30) as resp:
                data = await resp.json()

                if data.get("responseCode") == 0 and data.get("data", {}).get("status", "").lower() == "success":
                    tx_data = data["data"]
                    return {
                        "success": True,
                        "paid": True,
                        "amount": float(tx_data.get("amount", 0)),
                        "currency": tx_data.get("currency", "USD"),
                        "payment_date": tx_data.get("payment_date", ""),
                        "sender": tx_data.get("payment_details", {}).get("sender", ""),
                        "external_ref": tx_data.get("payment_details", {}).get("externalRef", ""),
                    }

                return {"success": True, "paid": False}

    except Exception as e:
        logger.error(f"KHQRPay verify_transaction error: {e}")
        return {"success": False, "error": str(e)}


async def poll_payment(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    timeout_seconds: int = 180,
    interval: int = 5,
) -> dict:
    """
    Poll KHQRPay until payment is confirmed or timeout.

    Returns same as verify_transaction.
    """
    import time
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        result = await verify_transaction(profile_id, secret_key, transaction_id)
        if not result["success"]:
            await asyncio.sleep(interval)
            continue
        if result.get("paid"):
            return result
        await asyncio.sleep(interval)

    return {"success": True, "paid": False}


def get_khqrpay_config(conn) -> dict:
    """Load KHQRPay config from bot_settings (with env fallbacks)."""
    from config import KHQRPAY_PROFILE_ID, KHQRPAY_SECRET_KEY, KHQRPAY_ABA_URL
    from services.database import get_bot_setting

    return {
        "profile_id": get_bot_setting(conn, "khqrpay_profile_id", KHQRPAY_PROFILE_ID or ""),
        "secret_key": get_bot_setting(conn, "khqrpay_secret_key", KHQRPAY_SECRET_KEY or ""),
        "aba_url": get_bot_setting(conn, "khqrpay_aba_url", KHQRPAY_ABA_URL or ""),
    }
