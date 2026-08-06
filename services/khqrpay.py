"""KHQRPay managed ABA/KHQR checkout and transaction verification."""

import asyncio
import base64
import hashlib
import json
import logging
from urllib.parse import quote, urlencode, urlsplit

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
    parsed = urlsplit(base)
    if parsed.scheme != "https" or parsed.hostname not in {"khqr.cc", "www.khqr.cc"}:
        raise ValueError("KHQRPay base URL must be https://khqr.cc")
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


def _decode_urlsafe(value: str) -> str:
    padded = value.replace("-", "+").replace("_", "/")
    padded += "=" * (-len(padded) % 4)
    return base64.b64decode(padded).decode("utf-8")


def _decrypt_qr_token(token: str, profile_id: str) -> dict:
    """Decrypt the QR-data token used by the public KHQRPay checkout page."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    parts = str(token or "").split(":")
    if len(parts) != 3:
        raise ValueError("KHQRPay returned an invalid QR-data token")
    iv = bytes.fromhex(parts[0])
    auth_tag = bytes.fromhex(parts[1])
    ciphertext = bytes.fromhex(parts[2]) + auth_tag
    key = hashlib.sha256(str(profile_id).encode("utf-8")).digest()
    plaintext = AESGCM(key).decrypt(iv, ciphertext, None)
    return json.loads(plaintext.decode("utf-8"))


async def _fetch_bank_qr(session, checkout_url: str, profile_id: str) -> dict:
    """Resolve managed checkout and retrieve its real bank QR payload."""
    async with session.get(checkout_url, allow_redirects=True, timeout=30) as response:
        raw = await response.text()
        if response.status >= 400:
            return {"success": False, "error": f"KHQRPay checkout returned HTTP {response.status}"}
        resolved = str(response.url)

    path_parts = urlsplit(resolved).path.strip("/").split("/")
    if len(path_parts) < 4 or path_parts[0] != "payment" or path_parts[1] != "khqrcc":
        return {"success": False, "error": "KHQRPay returned an invalid managed checkout URL"}

    resolved_profile = _decode_urlsafe(path_parts[2])
    payload = "/".join(path_parts[3:])
    qr_data_url = (
        f"{_base_url()}/api/payment/qr-data/{quote(resolved_profile, safe='')}?"
        f"{urlencode({'payload': payload})}"
    )
    async with session.get(qr_data_url, timeout=30) as qr_response:
        qr_raw_response = await qr_response.text()
        if qr_response.status >= 400:
            return {
                "success": False,
                "error": f"KHQRPay QR-data returned HTTP {qr_response.status}",
            }
        try:
            qr_data = json.loads(qr_raw_response)
        except Exception:
            return {"success": False, "error": "KHQRPay returned invalid QR-data JSON"}

    try:
        details = _decrypt_qr_token(qr_data.get("token"), resolved_profile)
    except Exception as exc:
        logger.exception("Could not decrypt KHQRPay QR-data token")
        return {"success": False, "error": f"Could not decode KHQRPay QR data: {exc}"}

    qr_text = str(details.get("qr_raw") or details.get("qr_text") or "").strip()
    if not qr_text:
        return {"success": False, "error": "KHQRPay returned no bank QR payload"}
    return {"success": True, "qr_text": qr_text, "details": details, "raw": raw}


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
            bank_qr = await _fetch_bank_qr(session, checkout_url, profile_id)
        if not bank_qr.get("success"):
            return bank_qr
        qr_text = bank_qr["qr_text"]

        return {
            "success": True,
            "qr_image_url": _render_checkout_qr(qr_text),
            "qr_text": qr_text,
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
                    returned_transaction_id = str(tx_data.get("transaction_id") or "").strip()
                    if returned_transaction_id != str(transaction_id).strip():
                        return {
                            "success": False,
                            "paid": False,
                            "error": "KHQRPay returned a mismatched transaction ID",
                        }
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
