"""
KHQRPay Payment Gateway — ABA Pay / KHQRcc (khqr.cc)

Direct QR API (server-to-server) + Verify V2 with Bakong fallback.
- create_aba_qr() → POST to qr-api-khqrcc → returns QR image URL + md5
- verify_aba_payment() → POST to check-transv2-khqrcc → returns paid status
"""
import asyncio
import hashlib
import json
import logging
from urllib.parse import urlencode

import aiohttp

logger = logging.getLogger(__name__)

KHQRPAY_BASE = "https://khqr.cc/api"


def _sha1(*parts: str) -> str:
    return hashlib.sha1("".join(parts).encode("utf-8")).hexdigest()


def _api_url(profile_id: str, endpoint: str) -> str:
    return f"{KHQRPAY_BASE}/{profile_id}/payment-gateway/v1/payments/{endpoint}"


async def create_aba_qr(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
    amount: float,
    success_url: str = "https://t.me/storeaccount_bot",
    remark: str = "",
) -> dict:
    """
    Create an ABA Pay KHQR via Direct QR API (server-to-server).
    Returns raw QR image URL — send directly in Telegram, no browser needed.

    Returns:
        {"success": True, "qr_image_url": "...", "qr_text": "...", "md5": "...", "amount": "..."}
        {"success": False, "error": "..."}
    """
    amount_str = f"{amount:.2f}"
    hash_val = _sha1(secret_key, transaction_id, amount_str, success_url, remark)

    payload = {
        "transaction_id": transaction_id,
        "amount": amount_str,
        "success_url": success_url,
        "remark": remark,
        "hash": hash_val,
    }

    url = _api_url(profile_id, "qr-api-khqrcc")
    logger.info(f"KHQRPay QR API → {url} | txn={transaction_id} | amt={amount_str}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=30) as resp:
                raw = await resp.text()
                logger.info(f"KHQRPay QR API ← {resp.status} | body={raw[:300]}")

                try:
                    data = json.loads(raw)
                except Exception:
                    return {"success": False, "error": f"Invalid JSON response (HTTP {resp.status})"}

                if data.get("responseCode") == 0 and data.get("data"):
                    tx_data = data["data"]
                    return {
                        "success": True,
                        "qr_image_url": tx_data.get("qr_url", ""),
                        "qr_text": tx_data.get("qr", ""),
                        "md5": tx_data.get("md5", ""),
                        "amount": tx_data.get("amount", amount_str),
                        "transaction_id": tx_data.get("transaction_id", transaction_id),
                    }

                error_msg = data.get("responseMessage", f"API error (code {data.get('responseCode')})")
                logger.warning(f"KHQRPay QR API failed: {error_msg} | full={raw[:500]}")
                return {"success": False, "error": error_msg}

    except asyncio.TimeoutError:
        return {"success": False, "error": "Gateway timeout — try again"}
    except Exception as e:
        logger.error(f"KHQRPay QR API error: {e}")
        return {"success": False, "error": str(e)}


async def verify_aba_payment(
    profile_id: str,
    secret_key: str,
    transaction_id: str,
) -> dict:
    """
    Verify ABA Pay payment via Check Transaction V2 (fast + Bakong fallback).

    Returns:
        {"success": True, "paid": True/False, "amount": "..."}
        {"success": False, "error": "..."}
    """
    hash_val = _sha1(secret_key, transaction_id)

    payload = {
        "transaction_id": transaction_id,
        "hash": hash_val,
    }

    url = _api_url(profile_id, "check-transv2-khqrcc")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, data=payload, timeout=30) as resp:
                raw = await resp.text()
                try:
                    data = json.loads(raw)
                except Exception:
                    return {"success": True, "paid": False}

                if data.get("responseCode") == 0 and data.get("data"):
                    tx_data = data["data"]
                    status = tx_data.get("status", "").lower()
                    return {
                        "success": True,
                        "paid": status == "success",
                        "amount": tx_data.get("amount", ""),
                    }

                # Non-zero response code — log error for debugging
                logger.warning(
                    f"KHQRPay verify failed: code={data.get('responseCode')} "
                    f"msg={data.get('responseMessage', '?')} txn={transaction_id}"
                )
                return {"success": True, "paid": False}

    except Exception as e:
        logger.error(f"KHQRPay Verify V2 error: {e}")
        return {"success": False, "error": str(e)}


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
