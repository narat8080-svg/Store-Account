"""
Bakong KHQR Payment Integration

Uses bakong-khqr SDK (v0.5.7+).
SDK API:
  - create_qr(...) → str (QR text)
  - generate_md5(qr_text) → str (MD5 hash)
  - check_payment(md5) → "PAID" | "UNPAID"
  - get_payment(md5) → dict | None (full txn info)
  - qr_image(qr_text, format="bytes") → bytes
"""
import asyncio
import io
import logging
import time

from config import (
    BAKONG_ACCOUNT,
    BAKONG_API_TOKEN,
    MERCHANT_NAME,
    MERCHANT_PHONE,
    PAYMENT_CHECK_INTERVAL,
    QR_EXPIRE_SECONDS,
)

logger = logging.getLogger(__name__)

# Always use the SDK (installed as bakong-khqr==0.5.7)
from bakong_khqr import KHQR


# ---------------------------------------------------------------------------
# Bakong QR creation
# ---------------------------------------------------------------------------
async def create_khqr(amount: float, order_id: str) -> dict:
    """
    Create a Bakong KHQR for the given amount.
    Returns dict: {success, qr_text, qr_md5, qr_image (BytesIO), error}
    """
    currency = "USD"
    amount_val = float(f"{amount:.2f}")

    # Run SDK calls in thread (they use blocking http.client)
    loop = asyncio.get_event_loop()

    try:
        khqr = KHQR(BAKONG_API_TOKEN)

        # Step 1: Create QR text string (blocking)
        qr_text = await loop.run_in_executor(
            None,
            lambda: khqr.create_qr(
                bank_account=BAKONG_ACCOUNT,
                merchant_name=MERCHANT_NAME,
                merchant_city="Phnom Penh",
                amount=amount_val,
                currency=currency,
                store_label=order_id,
                phone_number=MERCHANT_PHONE,
                bill_number=order_id,
                static=False,
            ),
        )

        if not qr_text:
            return {"success": False, "error": "SDK returned empty QR text", "qr_text": "", "qr_md5": "", "qr_image": None}

        # Step 2: Generate MD5 hash (blocking)
        qr_md5 = await loop.run_in_executor(None, khqr.generate_md5, qr_text)

        # Step 3: Generate QR image bytes (blocking)
        img_bytes = await loop.run_in_executor(None, lambda: khqr.qr_image(qr_text, format="bytes"))

        qr_image = io.BytesIO(img_bytes)

        logger.info(f"✅ QR created: order={order_id} amount=${amount_val} md5={qr_md5[:16]}...")

        return {
            "success": True,
            "qr_text": qr_text,
            "qr_md5": qr_md5,
            "qr_image": qr_image,
            "error": None,
        }

    except ValueError as e:
        # SDK raises ValueError for API errors (400, 401, 403, 404, 429, 500, 504)
        logger.error(f"Bakong API error: {e}")
        return {"success": False, "error": str(e), "qr_text": "", "qr_md5": "", "qr_image": None}
    except Exception as e:
        logger.error(f"Unexpected error creating QR: {e}")
        return {"success": False, "error": str(e), "qr_text": "", "qr_md5": "", "qr_image": None}


# ---------------------------------------------------------------------------
# Payment checking
# ---------------------------------------------------------------------------
async def check_payment(md5_hash: str) -> dict:
    """
    Check if a Bakong payment has been made.
    Returns dict: {paid (bool), amount (float), txn_id (str)}
    """
    loop = asyncio.get_event_loop()

    try:
        khqr = KHQR(BAKONG_API_TOKEN)

        # Use get_payment for full transaction details (blocking)
        txn_data = await loop.run_in_executor(None, khqr.get_payment, md5_hash)

        if txn_data and isinstance(txn_data, dict):
            return {
                "paid": True,
                "amount": float(txn_data.get("amount", 0)),
                "txn_id": txn_data.get("transactionId", ""),
            }

        return {"paid": False, "amount": 0, "txn_id": ""}

    except ValueError as e:
        logger.warning(f"Check payment error: {e}")
        return {"paid": False, "amount": 0, "txn_id": ""}
    except Exception as e:
        logger.error(f"Unexpected check payment error: {e}")
        return {"paid": False, "amount": 0, "txn_id": ""}


# ---------------------------------------------------------------------------
# Auto-check background task
# ---------------------------------------------------------------------------
async def auto_check_payment(
    payment_id: int,
    md5_hash: str,
    amount: float,
    user_id: int,
    chat_id: int,
    message_id: int,
    context,  # telegram.ext.ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Background task: check payment every N seconds until paid or expired.
    Updates the QR message countdown and notifies user on result.
    """
    from services.database import add_balance, get_db, mark_payment_expired, mark_payment_paid

    deadline = time.time() + QR_EXPIRE_SECONDS
    last_update = 0

    while time.time() < deadline:
        remaining = int(deadline - time.time())

        # Update countdown every 10 seconds
        if time.time() - last_update >= 10:
            try:
                mins, secs = divmod(remaining, 60)
                await context.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=(
                        f"💰 <b>Deposit ${amount:.2f}</b>\n\n"
                        f"📱 Scan the QR code with any Bakong app to pay.\n"
                        f"⏳ Expires in: <b>{mins:02d}:{secs:02d}</b>"
                    ),
                    parse_mode="HTML",
                )
                last_update = time.time()
            except Exception:
                pass

        # Check payment
        result = await check_payment(md5_hash)
        if result.get("paid"):
            conn = get_db()
            try:
                # Only first pending→paid transition credits (no double deposit)
                if not mark_payment_paid(conn, payment_id):
                    return
                new_balance = add_balance(conn, user_id, amount)
                await context.bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=message_id,
                    caption=(
                        f"✅ <b>Payment Successful!</b>\n\n"
                        f"💰 Amount: <b>${amount:.2f}</b>\n"
                        f"💳 New Balance: <b>${new_balance:.2f}</b>\n\n"
                        f"Thank you for your deposit!"
                    ),
                    parse_mode="HTML",
                )
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"🎉 <b>Deposit Confirmed!</b>\n"
                        f"Your wallet has been credited with <b>${amount:.2f}</b>.\n"
                        f"💳 Current Balance: <b>${new_balance:.2f}</b>"
                    ),
                    parse_mode="HTML",
                )

                # ── Notify payment group (deposit confirmed) ──
                try:
                    from config import PAYMENT_GROUP_ID
                    from services.database import get_db as _gdb, get_or_create_user as _gu
                    from utils.emoji_manager import get as E
                    c2 = _gdb()
                    try:
                        u = _gu(c2, user_id)
                        uname = u.get("username") or ""
                        fname = u.get("first_name") or ""
                        username = f"@{uname}" if uname else f"ID:{user_id}"
                        if fname:
                            username = f"{fname} ({username})"
                    finally:
                        c2.close()
                    table_msg = (
                        f"{E('alert_payment')} <b>Payment / Deposit Confirmed</b>\n\n"
                        f"{E('profile')} User: {username}\n"
                        f"{E('id_label')} ID: <code>{user_id}</code>\n"
                        f"{E('price_label')} Amount: <b>${amount:.2f}</b>\n"
                        f"{E('order_detail')} Payment #: <code>{payment_id}</code>\n"
                        f"{E('pay_wallet')} Method: Bakong KHQR"
                    )
                    await context.bot.send_message(
                        chat_id=int(PAYMENT_GROUP_ID),
                        text=table_msg,
                        parse_mode="HTML",
                    )
                except Exception as e:
                    import logging
                    logging.getLogger(__name__).warning(f"Payment group notify failed: {e}")
            finally:
                conn.close()
            return

        await asyncio.sleep(PAYMENT_CHECK_INTERVAL)

    # --- Expired ---
    conn = get_db()
    try:
        mark_payment_expired(conn, payment_id)
    finally:
        conn.close()

    try:
        await context.bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=(
                f"⏰ <b>QR Code Expired</b>\n\n"
                f"The QR code for <b>${amount:.2f}</b> has expired.\n"
                f"Please request a new one if you still want to deposit."
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass
