import asyncio
import io
import logging
import uuid

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import Conflict, NetworkError, TimedOut, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN, DEPOSIT_AMOUNTS, ADMIN_ID, SUPPORT_USERNAME, WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_PATH
from utils.emoji_manager import (get as E, get_plain as EP, get_premium_id as EID,
                           emoji_for_html, emoji_for_button, emoji_premium_id,
                           parse_db_emoji)
from services.database import (
    get_db,
    get_or_create_user,
    get_order_count,
    create_payment,
    get_product,
    get_stock_for_product,
    mark_stock_sold,
    create_order,
    get_user_orders,
    get_promo_code,
    use_promo_code,
    calculate_discount,
    add_balance,  # used for negative balance (deduct)
)
from services.khqrpay import create_aba_qr, verify_aba_payment, get_khqrpay_config

# Admin imports
from admin import (
    admin_panel,
    admin_dashboard,
    admin_categories,
    admin_add_category_start,
    admin_del_category_start,
    admin_del_category_confirm,
    admin_del_category_execute,
    admin_edit_category_start,
    admin_edit_cat_choose,
    admin_editcat_name,
    admin_editcat_emoji,
    admin_products,
    admin_add_product_start,
    admin_prod_cat_selected,
    admin_del_product_start,
    admin_del_product_confirm,
    admin_del_product_execute,
    admin_edit_product_start,
    admin_edit_prod_choose,
    admin_edit_prod_field,
    admin_stock_menu,
    admin_add_stock_start,
    admin_stock_prod_selected,
    admin_stock_limited_start,
    admin_stock_unlimited_toggle,
    admin_view_stock,
    admin_view_stock_detail,
    admin_clear_stock,
    admin_edit_stock_menu,
    admin_editstock_single,
    admin_editstock_prod_selected,
    admin_editstock_item_actions,
    admin_editstock_edit_detail,
    admin_editstock_delete,
    admin_editstock_replace,
    admin_editstock_replace_start,
    admin_promos,
    admin_add_promo_start,
    admin_del_promo_start,
    admin_del_promo_confirm,
    admin_users,
    admin_list_users,
    admin_search_user_prompt,
    admin_user_detail,
    admin_ban_user,
    admin_unban_user,
    admin_addbal_start,
    admin_deduct_start,
    admin_user_orders,
    admin_dm_start,
    admin_reports,
    report_sales,
    report_bestsellers,
    report_revenue,
    report_stock_sold,
    report_user_growth,
    admin_customize,
    custom_reset_all,
    custom_reset_all_confirm,
    custom_sections,
    custom_section_detail,
    custom_emoji_detail,
    custom_set_value_start,
    custom_reset_key,
    handle_admin_text,
    # New admin handlers
    admin_broadcast,
    admin_broadcast_start,
    admin_orders_mgmt,
    admin_orders_list,
    admin_order_detail,
    admin_order_refund,
    admin_order_search_prompt,
    admin_users_enhanced,
    admin_user_vip_set,
    admin_user_discount_set,
    admin_settings,
    admin_settings_maintenance,
    admin_settings_welcome,
    admin_settings_notify,
    admin_settings_restock,
    # Payment Gateway
    admin_settings_payment,
    admin_pay_set_profile,
    admin_pay_set_secret,
    admin_pay_set_aba,
    admin_pay_reset,
    admin_export_csv,
    admin_button_styles,
    admin_button_section,
    admin_button_detail,
    admin_button_style_set,
    # Backup & Restore
    admin_backup,
    admin_backup_create,
    admin_backup_restore,
    admin_backup_restore_execute,
    # Payments
    admin_payments,
    admin_pay_list,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ===========================================================================
# IN-MEMORY CACHE — avoids Supabase call on every button tap
# ===========================================================================
_user_cache: dict[int, dict] = {}  # user_id → user dict
_cache_hits = 0


def _cached_get_user(conn, user_id: int, username=None, first_name=None) -> dict:
    """Get user from cache, falling back to Supabase. Cuts latency by ~200ms per tap."""
    global _cache_hits
    if user_id in _user_cache:
        _cache_hits += 1
        u = _user_cache[user_id]
        if username and u.get("username") != username:
            u["username"] = username
        if first_name and u.get("first_name") != first_name:
            u["first_name"] = first_name
        return u

    u = get_or_create_user(conn, user_id, username, first_name)
    _user_cache[user_id] = u
    if _cache_hits > 0 and _cache_hits % 50 == 0:
        logger.info(f"⚡ User cache: {len(_user_cache)} users, {_cache_hits} hits")
    return u


# ===========================================================================
# NOTIFICATION HELPERS
# ===========================================================================
def _notify_payment_group(context, user_id: int, amount: float, payment_id: int, method: str = "KHQR") -> None:
    """Send payment notification to the payment group."""
    try:
        from config import PAYMENT_GROUP_ID
        if not PAYMENT_GROUP_ID:
            return
        gid = int(PAYMENT_GROUP_ID)
        context.bot.send_message(
            chat_id=gid,
            text=(
                f"💵 <b>Payment Received</b>\n\n"
                f"👤 User: <code>{user_id}</code>\n"
                f"💰 Amount: <b>${amount:.2f}</b>\n"
                f"🏷 Payment #: <code>{payment_id}</code>\n"
                f"💳 Method: {method}"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"Payment group notify failed: {e}")


def _notify_order_group(context, user, prod: dict, qty: int, total: float, new_balance: float, promo_data=None) -> None:
    """Send order notification to the order group."""
    try:
        from config import ORDER_GROUP_ID
        if not ORDER_GROUP_ID:
            return
        username = f"@{user.username}" if user.username else f"ID:{user.id}"
        text = (
            f"🛒 <b>New Order</b>\n\n"
            f"📦 {emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
            f"👤 {username}\n"
            f"💰 Amount: <b>${total:.2f}</b>\n"
            f"💳 Balance after: <b>${new_balance:.2f}</b>"
        )
        if promo_data:
            text += f"\n🎟 Promo: <code>{promo_data['code']}</code>"
        context.bot.send_message(chat_id=int(ORDER_GROUP_ID), text=text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Order group notify failed: {e}")


def _notify_new_user(context, user) -> None:
    """Send new user notification to the new-user group."""
    try:
        from config import NEW_USER_GROUP_ID
        if not NEW_USER_GROUP_ID:
            return
        username = f"@{user.username}" if user.username else "(no username)"
        context.bot.send_message(
            chat_id=int(NEW_USER_GROUP_ID),
            text=(
                f"🆕 <b>New User</b>\n\n"
                f"👤 {user.first_name or 'N/A'}\n"
                f"📎 {username}\n"
                f"🆔 <code>{user.id}</code>"
            ),
            parse_mode="HTML",
        )
    except Exception as e:
        logger.warning(f"New user notify failed: {e}")


# ===========================================================================
# HELPERS
# ===========================================================================
def _load_btn_cfg() -> dict:
    from utils.emoji_manager import load_button_config
    return load_button_config()


def _get_button_style(key: str, stock_count: int = None) -> str | None:
    """
    Get button style from button_config.json with hardcoded stock fallbacks.
    Stock-aware for buy buttons: in-stock=blue, out-of-stock=red, low-stock=blue.
    """
    from utils.emoji_manager import get_button_style

    # Stock-aware: check stock status keys for buy buttons
    if key in ("buy", "buy_now") and stock_count is not None:
        if stock_count == 0:
            return get_button_style("out_of_stock") or "danger"
        elif stock_count <= 3:
            return get_button_style("low_stock") or "primary"
        return get_button_style("in_stock") or "primary"

    # Direct lookup from button_config.json
    return get_button_style(key)


def _make_smart_button(text: str, callback_data: str, key: str = None,
                       stock_count: int = None) -> InlineKeyboardButton:
    """Create a button with style and emoji from config. PTB-version safe."""
    btn_cfg = _load_btn_cfg()
    cfg = btn_cfg.get(key, {}) if key else {}

    icon_custom_emoji_id = cfg.get("icon_custom_emoji_id")
    style = _get_button_style(key, stock_count) if key else None

    # Fall back to the emoji_manager premium ID whenever button_config has no
    # explicit icon (missing key OR null value). This is what makes an emoji
    # set via /admin → Customize show up on the home menu, shop, wallet, etc.
    if not icon_custom_emoji_id and key:
        icon_custom_emoji_id = EID(key)

    if icon_custom_emoji_id:
        button_text = cfg.get("text") or text
    else:
        button_text = f"{EP(key) if key else ''} {text}".strip()

    return _safe_button(button_text, callback_data, icon_custom_emoji_id, style)


def _safe_button(text: str, callback_data: str,
                 icon_custom_emoji_id: str = None, style: str = None) -> InlineKeyboardButton:
    """Create an InlineKeyboardButton safely.
    NOTE: 'style' param is ignored — Telegram Bot API does not support colored buttons.
    Button colors can only be achieved via premium custom emoji icons."""
    kwargs = {"text": text, "callback_data": callback_data}
    if icon_custom_emoji_id:
        kwargs["icon_custom_emoji_id"] = str(icon_custom_emoji_id)
    # style intentionally omitted — not supported by Telegram API
    try:
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        return InlineKeyboardButton(text=text, callback_data=callback_data)


def _main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            _make_smart_button("Profile", "menu_profile", "menu_profile"),
            _make_smart_button("Shop Now", "menu_product", "menu_product"),
        ],
        [
            _make_smart_button("Wallet", "menu_wallet", "menu_wallet"),
            _make_smart_button("My Order", "menu_myorder", "menu_myorder"),
        ],
        [
            InlineKeyboardButton(
                f"{EP('support')} Support".strip(),
                url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}",
            ),
        ],
    ])


def _back_button(target: str = "menu_start") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [_make_smart_button("Back", target, "back")],
    ])


# ===========================================================================
# /start
# ===========================================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the welcome message with main menu buttons. Checks maintenance mode."""
    user = update.effective_user

    conn = get_db()
    try:
        if user:
            db_user = get_or_create_user(conn, user.id, user.username, user.first_name)
            # Notify group on first interaction (once per user session)
            if context.user_data.get("new_user_notified") is None:
                order_count = get_order_count(conn, user.id)
                if order_count == 0:
                    _notify_new_user(context, user)
                context.user_data["new_user_notified"] = True

        if user.id != ADMIN_ID:
            from services.database import get_bot_setting
            mode = get_bot_setting(conn, "maintenance_mode", "off")
            if mode == "on":
                msg = get_bot_setting(conn, "maintenance_msg", "Bot is under maintenance. Please try again later.")
                if update.message:
                    await update.message.reply_html(f"🚧 {msg}")
                return

        welcome_text = (
            f"{E('welcome')} <b>Welcome to Rat Store!</b>\n\n"
            "Your one-stop shop for premium digital accounts.\n"
            "Choose an option below to get started:"
        )
        from services.database import get_bot_setting
        custom = get_bot_setting(conn, "welcome_msg", "")
        welcome_photo = get_bot_setting(conn, "welcome_photo", "")
        if custom:
            welcome_text = custom
    finally:
        conn.close()

    if update.message:
        try:
            if welcome_photo:
                # Send photo + caption with menu buttons
                await update.message.reply_photo(
                    photo=welcome_photo,
                    caption=welcome_text,
                    parse_mode="HTML",
                    reply_markup=_main_menu_keyboard(),
                )
            else:
                await update.message.reply_html(welcome_text, reply_markup=_main_menu_keyboard())
        except Exception:
            import re
            clean = re.sub(r'<[^>]+>', '', welcome_text)
            await update.message.reply_text(clean, reply_markup=_main_menu_keyboard())
    elif update.callback_query and update.callback_query.message:
        query = update.callback_query
        try:
            await query.answer()
        except Exception:
            pass
        try:
            await query.edit_message_text(
                welcome_text, parse_mode="HTML", reply_markup=_main_menu_keyboard()
            )
        except Exception as e:
            # HTML rejected (bad entity / premium emoji id) — retry as plain text.
            logger.warning(f"start(): HTML edit failed ({e}); retrying plain")
            import re
            clean = re.sub(r'<[^>]+>', '', welcome_text)
            try:
                await query.edit_message_text(clean, reply_markup=_main_menu_keyboard())
            except Exception as e2:
                logger.error(f"start(): plain edit also failed: {e2}")


# ===========================================================================
# PROFILE
# ===========================================================================
async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user profile with account info."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    conn = get_db()
    try:
        db_user = get_or_create_user(
            conn, user.id, user.username, user.first_name
        )
        balance = db_user["balance"]
        orders = get_order_count(conn, user.id)
    finally:
        conn.close()

    name = user.first_name or "N/A"
    username = f"@{user.username}" if user.username else "Not set"

    text = (
        f"{E('profile')} <b>Your Account</b>\n\n"
        f"{E('id_label')} ID: <code>{user.id}</code>\n"
        f"{E('name_label')} Name: {name}\n"
        f"{E('username_label')} Username: {username}\n"
        f"{E('balance_label')} Balance: <b>${balance:.2f}</b>\n"
        f"{E('order_label')} Orders: <b>{orders}</b>"
    )

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=_back_button()
    )


# ===========================================================================
# WALLET
# ===========================================================================
async def wallet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show wallet with balance and deposit options."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    conn = get_db()
    try:
        db_user = get_or_create_user(conn, user.id, user.username, user.first_name)
        balance = db_user["balance"]
    finally:
        conn.close()

    keyboard = [
        [_make_smart_button("Deposit", "wallet_deposit", "deposit")],
        [_make_smart_button("Back", "menu_start", "back")],
    ]

    text = (
        f"{E('wallet')} <b>My Wallet</b>\n\n"
        f"{E('balance_label')} Balance: <b>${balance:.2f}</b>\n\n"
        "Tap <b>Deposit</b> to add funds via KHQR (Bakong/ABA/Binance)."
    )

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ===========================================================================
# DEPOSIT – Amount Picker (Bakong KHQR only)
# ===========================================================================
async def deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit amount selection via Bakong KHQR."""
    query = update.callback_query
    await query.answer()
    context.user_data["deposit_method"] = "bakong"
    await deposit_amounts(update, context)


async def deposit_amounts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show deposit amount selection for Bakong KHQR."""
    query = update.callback_query
    await query.answer()

    prefix = "depamt_bakong_"

    buttons = []
    row = []
    for amt in DEPOSIT_AMOUNTS:
        row.append(InlineKeyboardButton(
            f"${amt}", callback_data=f"{prefix}{amt}"
        ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([_make_smart_button("Custom Amount", "deposit_custom", "deposit_custom")])
    buttons.append([_make_smart_button("Back", "menu_wallet", "back")])

    text = (
        f"{E('deposit')} <b>Deposit via ABA Pay / KHQR</b>\n\n"
        f"Select an amount to top up:\n"
        f"📱 ABA Pay · Bakong · Binance\n"
        f"{E('timer')} Session expires in <b>3 minutes</b>."
    )

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def deposit_custom_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to enter a custom deposit amount for KHQRPay."""
    query = update.callback_query
    await query.answer()
    context.user_data["buy_state"] = "custom_deposit"
    await query.edit_message_text(
        f"{E('deposit')} <b>Custom Deposit — KHQR</b>\n\n"
        f"Type the amount you want to deposit:\n"
        f"<i>Example: 3.50 or 25</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            _make_smart_button("Cancel", "menu_wallet", "cancel")
        ]]),
    )


# ===========================================================================
# DEPOSIT – ABA Pay / KHQR Direct QR
# ===========================================================================
async def deposit_create_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Generate ABA Pay KHQR via Direct QR API and send image."""
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    raw = data.replace("depamt_bakong_", "").replace("deposit_amt_", "")
    try:
        amount = float(raw)
    except ValueError:
        await query.edit_message_text("❌ Invalid amount.", reply_markup=_back_button("menu_wallet"))
        return

    user = query.from_user
    transaction_id = f"RAT-{uuid.uuid4().hex[:8].upper()}"

    conn = get_db()
    try:
        cfg = get_khqrpay_config(conn)
    finally:
        conn.close()

    if not cfg["profile_id"] or not cfg["secret_key"]:
        await query.edit_message_text(
            f"{E('error')} Payment gateway not configured.\nContact admin.",
            parse_mode="HTML", reply_markup=_back_button("menu_wallet"),
        )
        return

    await query.edit_message_text(
        f"{E('timer')} Generating ABA Pay QR for <b>${amount:.2f}</b>...",
        parse_mode="HTML",
    )

    result = await create_aba_qr(
        profile_id=cfg["profile_id"],
        secret_key=cfg["secret_key"],
        transaction_id=transaction_id,
        amount=amount,
        remark=f"Deposit for {user.id}",
    )

    if not result["success"]:
        await query.edit_message_text(
            f"{E('error')} <b>QR Failed</b>\n\n{result.get('error', 'Unknown error')}",
            parse_mode="HTML", reply_markup=_back_button("menu_wallet"),
        )
        return

    # Store pending payment
    conn = get_db()
    try:
        payment_id = create_payment(conn, user.id, amount, result["qr_text"], transaction_id)
    finally:
        conn.close()

    # Send QR image
    caption = (
        f"{E('deposit')} <b>Deposit ${amount:.2f}</b>\n\n"
        f"📱 Scan with ABA Mobile or Bakong app.\n"
        f"{E('timer')} Expires in: <b>03:00</b>"
    )
    msg = await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=result["qr_image_url"],
        caption=caption,
        parse_mode="HTML",
    )

    try:
        await query.delete_message()
    except Exception:
        pass

    asyncio.create_task(_khqrpay_watcher(
        payment_id=payment_id, cfg=cfg, transaction_id=transaction_id,
        amount=amount, user_id=user.id, chat_id=query.message.chat_id,
        message_id=msg.message_id, context=context,
    ))


async def _khqrpay_watcher(
    payment_id: int, cfg: dict, transaction_id: str, amount: float,
    user_id: int, chat_id: int, message_id: int,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Poll ABA Pay for confirmation, then credit balance."""
    from services.database import mark_payment_paid, add_balance, get_db
    import time

    deadline = time.time() + 180
    last_update = 0

    while time.time() < deadline:
        remaining = int(deadline - time.time())

        if time.time() - last_update >= 10:
            mins, secs = divmod(remaining, 60)
            try:
                await context.bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id,
                    caption=(
                        f"{E('deposit')} <b>Deposit ${amount:.2f}</b>\n\n"
                        f"📱 Scan with ABA Mobile or Bakong app.\n"
                        f"{E('timer')} Expires in: <b>{mins:02d}:{secs:02d}</b>"
                    ),
                    parse_mode="HTML",
                )
                last_update = time.time()
            except Exception:
                pass

        result = await verify_aba_payment(cfg["profile_id"], cfg["secret_key"], transaction_id)
        if result.get("paid"):
            conn = get_db()
            try:
                mark_payment_paid(conn, payment_id)
                new_balance = add_balance(conn, user_id, amount)
            finally:
                conn.close()

            # Delete QR photo, send clean success message
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{E('success')} <b>Payment Confirmed!</b>\n\n"
                        f"💰 Amount: <b>${amount:.2f}</b>\n"
                        f"💳 New Balance: <b>${new_balance:.2f}</b>"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

            # Notify payment group
            _notify_payment_group(context, user_id, amount, payment_id, "ABA Pay")
            return

        await asyncio.sleep(5)

    # Expired
    conn = get_db()
    try:
        from services.database import mark_payment_expired
        mark_payment_expired(conn, payment_id)
    finally:
        conn.close()
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        pass




# ===========================================================================
# PRODUCT BROWSING (User-facing)
# ===========================================================================
async def product_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Flat product list — Shop Now goes straight to products (no category step).
    Categories still exist in the DB (products remain linked) but are hidden from users.
    """
    query = update.callback_query
    await query.answer()

    from services.database import get_all_products
    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if not products:
        await query.edit_message_text(
            f"{E('product')} <b>Products</b>\n\nNo products available yet.\nPlease check back later!",
            parse_mode="HTML",
            reply_markup=_back_button(),
        )
        return

    buttons = []
    for p in products:
        stock = p.get("stock_count", 0)
        is_unlimited = p.get("is_unlimited", 0)
        if is_unlimited:
            stock_label = ""
            out_label = ""
        else:
            stock_label = f" ({stock} left)" if 0 < stock <= 3 else ""
            out_label = " Out of Stock" if stock == 0 else ""
        label = f"{emoji_for_button(p['emoji'])} {p['name']} — ${p['price']:.2f}{stock_label}{out_label}"

        pid = emoji_premium_id(p['emoji'])
        icon_id = str(pid) if pid else None
        if pid:
            plain, _ = parse_db_emoji(p['emoji'])
            if label.startswith(plain):
                label = label[len(plain):].strip()
        style = _get_button_style("buy", 999 if is_unlimited else stock)
        buttons.append([_safe_button(label, f"buy_detail_{p['id']}", icon_id, style)])

    buttons.append([_make_smart_button("Back", "menu_start", "back")])

    await query.edit_message_text(
        f"{E('product')} <b>Products</b>\n\nTap a product to buy:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )




# ===========================================================================
# BUY FLOW
# ===========================================================================
async def buy_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    First step of the buy flow: prompt the user for a quantity.
    Only a Cancel button is shown here — the user types a number in chat.
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    prod_id = int(data.replace("buy_detail_", "").split("_")[0])

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        is_unlimited = prod.get("is_unlimited", 0) if prod else 0
        if is_unlimited:
            max_stock = None
        else:
            max_stock = len(get_stock_for_product(conn, prod_id, unsold_only=True))
    finally:
        conn.close()

    if not prod:
        await query.edit_message_text(
            f"{E('error')} Product not found.",
            reply_markup=_back_button("menu_product"),
        )
        return

    if not is_unlimited and max_stock == 0:
        await query.edit_message_text(
            f"{E('out_of_stock')} <b>Out of Stock</b>\n\n"
            f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> has no stock right now.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                _make_smart_button("Back", f"prod_cat_{prod['category_id']}", "back")
            ]]),
        )
        return

    # Enter quantity mode: unified_text_handler will pick up the reply
    context.user_data["buy_state"] = "buy_custom_qty"
    context.user_data["buy_data"] = {"prod_id": prod_id}

    max_line = "♾️ Unlimited" if is_unlimited else f"<b>{max_stock}</b>"
    desc = prod.get("description", "")
    desc_text = f"\n{E('description')} {desc}" if desc else ""
    text = (
        f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b>{desc_text}\n"
        f"{E('price_label')} Price: <b>${prod['price']:.2f}</b> each\n"
        f"{E('stock_label')} Available: {max_line}\n\n"
        f"{E('qty_custom')} <b>Enter quantity</b>\n"
        f"<i>Type the number of items you want to buy.</i>"
    )

    await query.edit_message_text(
        text,
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            _make_smart_button("Cancel", f"prod_cat_{prod['category_id']}", "cancel")
        ]]),
    )


def _parse_pay_cb(data: str, prefix: str) -> tuple[int, int, int | None]:
    """Parse 'pay_wallet_PRODID_QTY[_promo_PROMOID]' → (prod_id, qty, promo_id_or_None)."""
    body = data[len(prefix):]
    parts = body.split("_")
    prod_id = int(parts[0])
    qty = int(parts[1])
    promo_id = None
    if len(parts) >= 4 and parts[2] == "promo":
        try:
            promo_id = int(parts[3])
        except ValueError:
            promo_id = None
    return prod_id, qty, promo_id


async def pay_confirm_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show an order-summary screen with Confirm / Cancel before actually charging.
    Handles both 'pay_wallet_<...>' and 'pay_khqr_<...>' callbacks.
    Confirm → dispatches 'dowallet_<...>' or 'dokhqr_<...>' which run the real handler.
    Cancel  → returns to product detail.
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    if data.startswith("pay_wallet_"):
        method = "wallet"
        prefix = "pay_wallet_"
    else:
        method = "khqr"
        prefix = "pay_khqr_"

    try:
        prod_id, qty, promo_id = _parse_pay_cb(data, prefix)
    except (ValueError, IndexError):
        await query.edit_message_text("❌ Invalid payment request.", reply_markup=_back_button())
        return

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
    finally:
        conn.close()
    if not prod:
        await query.edit_message_text("❌ Product not found.", reply_markup=_back_button("menu_product"))
        return

    # Reuse the same active_promo the checkout screen uses — keeps the total consistent.
    promo_data = context.user_data.get("active_promo")
    unit_price = prod["price"]
    promo_note = ""
    if promo_data:
        discount, unit_price = calculate_discount(promo_data, prod["price"])
        promo_note = (
            f"\n{E('promo_code')} Promo: <code>{promo_data['code']}</code>"
            f" (-${discount:.2f}/each)"
        )
    total = unit_price * qty

    method_label = "Wallet" if method == "wallet" else "KHQR"

    # Rebuild the callback body so the confirm button targets the SAME order.
    cb_body = data[len(prefix):]
    confirm_cb = ("dowallet_" if method == "wallet" else "dokhqr_") + cb_body

    text = (
        f"{emoji_for_html(prod['emoji'])} <b>Confirm Order</b>\n\n"
        f"{E('product')} Product: <b>{prod['name']}</b>\n"
        f"{E('qty_custom')} Quantity: <b>{qty}</b>\n"
        f"{E('price_label')} Unit: <b>${unit_price:.2f}</b>{promo_note}\n"
        f"{E('price_label')} Total: <b>${total:.2f}</b>\n"
        f"💳 Method: <b>{method_label}</b>\n\n"
        f"Tap <b>Confirm Order</b> to proceed."
    )

    keyboard = InlineKeyboardMarkup([
        [_make_smart_button(f"Confirm Order — ${total:.2f}", confirm_cb, "confirm")],
        [_make_smart_button("Cancel", f"buy_detail_{prod_id}", "cancel")],
    ])

    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=keyboard)
    except Exception:
        await context.bot.send_message(
            chat_id=query.message.chat_id, text=text,
            parse_mode="HTML", reply_markup=keyboard,
        )


async def buy_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Wallet payment path: deduct balance, assign stock, send .txt file.
    Accepts 'dowallet_PRODID_QTY[_promo_PROMOID]' (post-confirm)
    or legacy 'pay_wallet_...' (kept for backward compatibility).
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    prefix = "dowallet_" if data.startswith("dowallet_") else "pay_wallet_"
    try:
        prod_id, qty, promo_id_from_cb = _parse_pay_cb(data, prefix)
    except (ValueError, IndexError):
        await query.edit_message_text(
            f"{E('error')} Invalid request.",
            reply_markup=_back_button("menu_product"),
        )
        return

    user = query.from_user

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        is_unlimited = prod.get("is_unlimited", 0) if prod else 0
        if is_unlimited:
            stock_items = []
        else:
            stock_items = get_stock_for_product(conn, prod_id, unsold_only=True)
    finally:
        conn.close()

    if not prod:
        await query.edit_message_text(
            f"{E('error')} Product not found.",
            parse_mode="HTML", reply_markup=_back_button("menu_product"),
        )
        return

    if not is_unlimited and len(stock_items) < qty:
        await query.edit_message_text(
            f"{E('out_of_stock')} <b>Out of Stock!</b>",
            parse_mode="HTML", reply_markup=_back_button("menu_product"),
        )
        return

    price = prod["price"]
    unit_price = price
    promo_data = None
    discount = 0

    conn = get_db()
    try:
        if promo_id_from_cb is not None:
            r = _get_supabase_for_promo(promo_id_from_cb)
            if r:
                promo_data = r
                discount, unit_price = calculate_discount(promo_data, price)
                if promo_data["max_uses"] > 0 and promo_data["current_uses"] >= promo_data["max_uses"]:
                    promo_data = None; discount = 0; unit_price = price
                elif price < promo_data["min_order"]:
                    promo_data = None; discount = 0; unit_price = price
                else:
                    use_promo_code(conn, promo_data["id"])

        db_user = get_or_create_user(conn, user.id, user.username, user.first_name)
        balance = db_user["balance"]
        total_price = unit_price * qty

        if balance < total_price:
            shortfall = total_price - balance
            cb_pay = f"pay_khqr_{prod_id}_{qty}"
            if promo_data:
                cb_pay += f"_promo_{promo_data['id']}"
            await query.edit_message_text(
                f"{E('error')} <b>Insufficient Balance</b>\n\n"
                f"{E('price_label')} Total: <b>${total_price:.2f}</b>\n"
                f"{E('balance_label')} Balance: <b>${balance:.2f}</b>\n"
                f"{E('warning')} Need <b>${shortfall:.2f}</b> more.\n\n"
                f"Pay directly with KHQR or top up your wallet.",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [_make_smart_button(f"Pay with KHQR — ${total_price:.2f}", cb_pay, "pay_khqr")],
                    [_make_smart_button("Deposit", "wallet_deposit", "deposit")],
                    [_make_smart_button("Back", f"prod_cat_{prod['category_id']}", "back")],
                ]),
            )
            return

        add_balance(conn, user.id, -total_price)

        # ── Assign stock items & collect details ──
        details = []
        if is_unlimited:
            # Unlimited product: no stock items to assign, just create orders
            for i in range(qty):
                order_id = create_order(conn, user.id, prod_id, unit_price,
                    stock_id=None,
                    promo_code=promo_data["code"] if promo_data else None,
                    original_amount=price,
                )
        else:
            for i in range(qty):
                item = stock_items[i]
                details.append(item["detail"])
                order_id = create_order(conn, user.id, prod_id, unit_price,
                    stock_id=item["id"],
                    promo_code=promo_data["code"] if promo_data else None,
                    original_amount=price,
                )
                mark_stock_sold(conn, item["id"], order_id)

        new_balance = balance - total_price
    finally:
        conn.close()

    # ── Clear quantity state ──
    context.user_data.get("buy_qty", {}).pop(str(prod_id), None)

    promo_note = ""
    if promo_data:
        promo_note = f"\n{E('promo_code')} Promo: <code>{promo_data['code']}</code> (-${discount:.2f}/each)"

    if is_unlimited:
        # ── Unlimited product: just send confirmation message ──
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=(
                f"{E('success')} <b>Purchase Successful!</b>\n\n"
                f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
                f"{E('price_label')} Paid: <b>${total_price:.2f}</b>{promo_note}\n"
                f"{E('balance_label')} Balance: <b>${new_balance:.2f}</b>\n\n"
                f"<i>Thank you for your purchase! {E('welcome')}</i>"
            ),
            parse_mode="HTML",
        )
    else:
        # ── Limited product: send .txt file with accounts ──
        file_content = "\n\n---\n\n".join(details)
        file_bytes = io.BytesIO(file_content.encode("utf-8"))
        file_bytes.name = f"{prod['name'].replace(' ','_')}_{qty}x.txt"

        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=file_bytes,
            caption=(
                f"{E('success')} <b>Purchase Successful!</b>\n\n"
                f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
                f"{E('price_label')} Paid: <b>${total_price:.2f}</b>{promo_note}\n"
                f"{E('balance_label')} Balance: <b>${new_balance:.2f}</b>\n\n"
                f"<i>Your accounts are in the attached file. Thank you! {E('welcome')}</i>"
            ),
            parse_mode="HTML",
        )

    keyboard = [
        [_make_smart_button("Browse More", "menu_product", "menu_product")],
        [_make_smart_button("Back to Menu", "menu_start", "back")],
    ]

    await query.edit_message_text(
        f"{E('success')} <b>Purchase Complete!</b>\n\n"
        + (f"♾️ Unlimited product — no account delivery needed.\n" if is_unlimited else f"📄 {qty} account(s) sent as file above.\n")
        + f"{E('balance_label')} Balance: <b>${new_balance:.2f}</b>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard),
    )

    # ── Notify order group ──
    _notify_order_group(context, user, prod, qty, total_price, new_balance, promo_data)


def _get_supabase_for_promo(promo_id: int):
    """Get promo by ID from Supabase."""
    from services.database import _get_supabase
    s = _get_supabase()
    r = s.table('promo_codes').select('*').eq('id', promo_id).eq('is_active', 1).execute()
    return dict(r.data[0]) if r.data else None


# ===========================================================================
# KHQRPAY DIRECT-PAY FOR ORDERS
# ===========================================================================
async def pay_khqr_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle 'dokhqr_PRODID_QTY[_promo_PROMOID]' (post-confirm) or legacy
    'pay_khqr_...': create a KHQRPay checkout and start the auto-check loop.
    On payment success, assign stock and deliver.
    """
    query = update.callback_query
    await query.answer()

    data = query.data or ""
    prefix = "dokhqr_" if data.startswith("dokhqr_") else "pay_khqr_"
    try:
        prod_id, qty, promo_id_from_cb = _parse_pay_cb(data, prefix)
    except (ValueError, IndexError):
        await query.edit_message_text(
            f"{E('error')} Invalid request.",
            reply_markup=_back_button("menu_product"),
        )
        return

    user = query.from_user

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        is_unlimited = prod.get("is_unlimited", 0) if prod else 0
        stock_items = [] if is_unlimited else get_stock_for_product(conn, prod_id, unsold_only=True)
        cfg = get_khqrpay_config(conn)
    finally:
        conn.close()

    if not prod:
        await query.edit_message_text(
            f"{E('error')} Product not found.",
            reply_markup=_back_button("menu_product"),
        )
        return

    if not cfg["profile_id"] or not cfg["secret_key"]:
        await query.edit_message_text(
            f"{E('error')} Payment gateway not configured.",
            reply_markup=_back_button("menu_product"),
        )
        return

    if not is_unlimited and len(stock_items) < qty:
        await query.edit_message_text(
            f"{E('out_of_stock')} <b>Not Enough Stock!</b>\n\n"
            f"Only {len(stock_items)} available, you asked for {qty}.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                _make_smart_button("Back", f"prod_cat_{prod['category_id']}", "back")
            ]]),
        )
        return

    price = prod["price"]
    unit_price = price
    promo_data = None
    discount = 0

    if promo_id_from_cb is not None:
        r = _get_supabase_for_promo(promo_id_from_cb)
        if r:
            promo_data = r
            discount, unit_price = calculate_discount(promo_data, price)
            if promo_data["max_uses"] > 0 and promo_data["current_uses"] >= promo_data["max_uses"]:
                promo_data = None; discount = 0; unit_price = price
            elif price < promo_data["min_order"]:
                promo_data = None; discount = 0; unit_price = price

    total_price = unit_price * qty
    transaction_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"

    await query.edit_message_text(
        f"{E('timer')} <b>Generating ABA Pay QR…</b>",
        parse_mode="HTML",
    )

    result = await create_aba_qr(
        profile_id=cfg["profile_id"],
        secret_key=cfg["secret_key"],
        transaction_id=transaction_id,
        amount=total_price,
        remark=f"Order: {prod['name']} x{qty}",
    )

    if not result["success"]:
        await query.edit_message_text(
            f"{E('error')} Failed to generate QR: {result.get('error')}",
            reply_markup=_back_button("menu_product"),
        )
        return

    conn = get_db()
    try:
        payment_id = create_payment(conn, user.id, total_price, result["qr_text"], transaction_id)
    finally:
        conn.close()

    caption = (
        f"{E('buy')} <b>Pay ${total_price:.2f} via ABA Pay</b>\n\n"
        f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
        f"{E('timer')} Expires in: <b>03:00</b>\n\n"
        f"📱 Scan with ABA Mobile or any Bakong app."
    )
    msg = await context.bot.send_photo(
        chat_id=query.message.chat_id,
        photo=result["qr_image_url"],
        caption=caption,
        parse_mode="HTML",
    )

    asyncio.create_task(_khqrpay_order_watcher(
        payment_id=payment_id,
        cfg=cfg,
        transaction_id=transaction_id,
        amount=total_price,
        user_id=user.id,
        chat_id=query.message.chat_id,
        message_id=msg.message_id,
        prod_id=prod_id,
        qty=qty,
        promo_data=promo_data,
        unit_price=unit_price,
        original_price=price,
        context=context,
    ))


async def _khqrpay_order_watcher(
    payment_id: int,
    cfg: dict,
    transaction_id: str,
    amount: float,
    user_id: int,
    chat_id: int,
    message_id: int,
    prod_id: int,
    qty: int,
    promo_data: dict | None,
    unit_price: float,
    original_price: float,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Poll ABA Pay for order payment; on success, assign stock and deliver."""
    import time
    from services.database import mark_payment_paid, mark_payment_expired

    deadline = time.time() + 180
    last_update = 0

    while time.time() < deadline:
        remaining = int(deadline - time.time())

        if time.time() - last_update >= 10:
            mins, secs = divmod(remaining, 60)
            try:
                await context.bot.edit_message_caption(
                    chat_id=chat_id, message_id=message_id,
                    caption=(
                        f"{E('buy')} <b>Pay ${amount:.2f} via ABA Pay</b>\n\n"
                        f"{E('timer')} Expires in: <b>{mins:02d}:{secs:02d}</b>\n\n"
                        f"📱 Scan with ABA Mobile or any Bakong app."
                    ),
                    parse_mode="HTML",
                )
                last_update = time.time()
            except Exception:
                pass

        result = await verify_aba_payment(cfg["profile_id"], cfg["secret_key"], transaction_id)
        if result.get("paid"):
            conn = get_db()
            try:
                mark_payment_paid(conn, payment_id)

                prod = get_product(conn, prod_id)
                is_unlimited = prod.get("is_unlimited", 0) if prod else 0
                stock_items = [] if is_unlimited else get_stock_for_product(conn, prod_id, unsold_only=True)

                if not is_unlimited and len(stock_items) < qty:
                    add_balance(conn, user_id, amount)
                    try:
                        await context.bot.edit_message_caption(
                            chat_id=chat_id, message_id=message_id,
                            caption=(
                                f"{E('warning')} <b>Out of Stock After Payment</b>\n\n"
                                f"We received your ${amount:.2f} but stock ran out. "
                                f"Credited to your wallet."
                            ),
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
                    return

                if promo_data:
                    use_promo_code(conn, promo_data["id"])

                details = []
                if is_unlimited:
                    for _ in range(qty):
                        create_order(conn, user_id, prod_id, unit_price,
                                     stock_id=None,
                                     promo_code=promo_data["code"] if promo_data else None,
                                     original_amount=original_price)
                else:
                    for i in range(qty):
                        item = stock_items[i]
                        details.append(item["detail"])
                        order_id = create_order(conn, user_id, prod_id, unit_price,
                                                stock_id=item["id"],
                                                promo_code=promo_data["code"] if promo_data else None,
                                                original_amount=original_price)
                        mark_stock_sold(conn, item["id"], order_id)
            finally:
                conn.close()

            # Delete QR, show success, deliver
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                pass

            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{E('success')} <b>Payment Received!</b>\n\n"
                        f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
                        f"{E('price_label')} Paid: <b>${amount:.2f}</b>"
                    ),
                    parse_mode="HTML",
                )
            except Exception:
                pass

            # Deliver
            if is_unlimited:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=(
                        f"{E('success')} <b>Purchase Successful!</b>\n\n"
                        f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
                        f"{E('price_label')} Paid: <b>${amount:.2f}</b>\n\n"
                        f"<i>Thank you for your purchase! {E('welcome')}</i>"
                    ),
                    parse_mode="HTML",
                )
            else:
                file_content = "\n\n---\n\n".join(details)
                file_bytes = io.BytesIO(file_content.encode("utf-8"))
                file_bytes.name = f"{prod['name'].replace(' ','_')}_{qty}x.txt"
                await context.bot.send_document(
                    chat_id=chat_id,
                    document=file_bytes,
                    caption=(
                        f"{E('success')} <b>Purchase Successful!</b>\n\n"
                        f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
                        f"{E('price_label')} Paid: <b>${amount:.2f}</b>\n\n"
                        f"<i>Your accounts are in the attached file. {E('welcome')}</i>"
                    ),
                    parse_mode="HTML",
                )

            # Notify both groups
            try:
                from config import ORDER_GROUP_ID
                if ORDER_GROUP_ID:
                    text = (
                        f"🛒 <b>New Order (KHQR)</b>\n\n"
                        f"📦 {emoji_for_html(prod['emoji'])} <b>{prod['name']}</b> × {qty}\n"
                        f"👤 <code>{user_id}</code>\n"
                        f"💰 Amount: <b>${amount:.2f}</b>"
                    )
                    if promo_data:
                        text += f"\n🎟 Promo: <code>{promo_data['code']}</code>"
                    await context.bot.send_message(chat_id=int(ORDER_GROUP_ID), text=text, parse_mode="HTML")
            except Exception:
                pass
            _notify_payment_group(context, user_id, amount, payment_id, "ABA Pay")
            return

        await asyncio.sleep(5)

    # Expired
    conn = get_db()
    try:
        mark_payment_expired(conn, payment_id)
    finally:
        conn.close()
    try:
        await context.bot.edit_message_caption(
            chat_id=chat_id, message_id=message_id,
            caption=(
                f"{E('expired')} <b>QR Expired</b>\n\n"
                f"The payment window for <b>${amount:.2f}</b> closed. Try again."
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass


# ===========================================================================
# PROMO CODE APPLICATION (User-side)
# ===========================================================================
async def apply_promo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to type a promo code."""
    query = update.callback_query
    await query.answer()

    prod_id = query.data.replace("apply_promo_", "")
    context.user_data["buy_state"] = "buy_promo"
    context.user_data["buy_data"] = {"prod_id": prod_id}

    await query.edit_message_text(
        f"{E('promo_code')} <b>Apply Promo Code</b>\n\n"
        "Please <b>type</b> your promo code:\n\n"
        "<i>Example: WELCOME10</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            _make_smart_button("Cancel", f"buy_detail_{prod_id}", "cancel")
        ]]),
    )


async def handle_buy_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process promo code entered by user during purchase."""
    if not update.message:
        return

    user = update.effective_user
    state = context.user_data.get("buy_state")
    if state != "buy_promo":
        return

    code = update.message.text.strip().upper()
    buy_data = context.user_data.get("buy_data", {})
    prod_id = buy_data.get("prod_id", "")

    context.user_data.pop("buy_state", None)
    context.user_data.pop("buy_data", None)

    conn = get_db()
    try:
        promo = get_promo_code(conn, code)
        if not promo:
            await update.message.reply_html(
                f"{E('error')} <b>Invalid or expired promo code.</b>",
                reply_markup=InlineKeyboardMarkup([[
                    _make_smart_button("Back to Product", f"buy_detail_{prod_id}", "back")
                ]]),
            )
            return

        if promo["max_uses"] > 0 and promo["current_uses"] >= promo["max_uses"]:
            await update.message.reply_html(
                f"{E('error')} This promo code has reached its usage limit.",
                reply_markup=InlineKeyboardMarkup([[
                    _make_smart_button("Back to Product", f"buy_detail_{prod_id}", "back")
                ]]),
            )
            return

        prod = get_product(conn, int(prod_id)) if prod_id else None
        if prod and prod["price"] < promo["min_order"]:
            await update.message.reply_html(
                f"{E('error')} Minimum order for this promo is <b>${promo['min_order']:.2f}</b>.",
                reply_markup=InlineKeyboardMarkup([[
                    _make_smart_button("Back to Product", f"buy_detail_{prod_id}", "back")
                ]]),
            )
            return

    finally:
        conn.close()

    # Store valid promo in context
    context.user_data["active_promo"] = dict(promo)

    d_type = "%" if promo["discount_type"] == "percent" else "$"

    # If a quantity is already known, jump straight to the pay screen with the new discount.
    qty = context.user_data.get("buy_qty", {}).get(str(prod_id))
    if qty and prod:
        await update.message.reply_html(
            f"{E('success')} Promo <code>{promo['code']}</code> applied "
            f"({d_type}{promo['discount_value']} off)."
        )
        await _send_pay_options(update.message.chat_id, context, prod, int(qty))
        return

    await update.message.reply_html(
        f"{E('success')} Promo applied!\n\n"
        f"{E('promo_code')} <code>{promo['code']}</code> — {d_type}{promo['discount_value']} off\n\n"
        f"Go back to the product to enter a quantity.",
        reply_markup=InlineKeyboardMarkup([[
            _make_smart_button("Back to Product", f"buy_detail_{prod_id}", "back")
        ]]),
    )


# ===========================================================================
# CUSTOM ORDER
# ===========================================================================
async def custom_order_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt user to describe their custom order."""
    query = update.callback_query
    await query.answer()
    context.user_data["buy_state"] = "custom_order"
    await query.edit_message_text(
        f"{E('edit')} <b>Custom Order</b>\n\n"
        f"Describe what kind of account you want:\n"
        f"<i>Example: I need a Netflix 4K account for 1 year</i>\n\n"
        f"Your request will be sent to the admin who will contact you.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            _make_smart_button("Cancel", "menu_product", "cancel")
        ]]),
    )


async def _handle_custom_order(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send custom order request to admin."""
    if not update.message:
        return
    user = update.effective_user
    desc = update.message.text.strip()
    context.user_data.pop("buy_state", None)

    username = f"@{user.username}" if user.username else f"ID:{user.id}"
    from config import ORDER_GROUP_ID, ADMIN_ID

    # Send to admin DM
    try:
        await context.bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📩 <b>Custom Order Request</b>\n\n"
                f"👤 {username}\n"
                f"📝 {desc}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    # Also send to order group
    try:
        await context.bot.send_message(
            chat_id=ORDER_GROUP_ID,
            text=(
                f"📩 <b>Custom Order</b>\n"
                f"👤 {username}\n"
                f"📝 {desc}"
            ),
            parse_mode="HTML",
        )
    except Exception:
        pass

    await update.message.reply_html(
        f"✅ <b>Request sent!</b>\n\n"
        f"The admin will review your request and contact you.\n"
        f"<i>You can continue browsing while you wait.</i>",
        reply_markup=InlineKeyboardMarkup([[
            _make_smart_button("Browse Products", "menu_product", "menu_product"),
        ]]),
    )
async def my_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show user's order history as tappable buttons."""
    query = update.callback_query
    await query.answer()

    user = query.from_user
    conn = get_db()
    try:
        orders = get_user_orders(conn, user.id, limit=10)
        db_user = get_or_create_user(conn, user.id)
        balance = db_user.get("balance", 0)
    finally:
        conn.close()

    if not orders:
        await query.edit_message_text(
            f"{E('orders')} <b>My Orders</b>\n\nNo purchases yet.\nBrowse {E('product')} Products!",
            parse_mode="HTML",
            reply_markup=_back_button(),
        )
        return

    text = (
        f"{E('orders')} <b>My Orders</b>\n"
        f"{E('balance_label')} Balance: <b>${balance:.2f}</b>\n\n"
        f"<i>Tap an order to view details & get account file.</i>"
    )

    buttons = []
    for o in orders:
        db_emoji = o.get("product_emoji", "📦")
        emoji = emoji_for_button(db_emoji)
        name = o.get("product_name") or f"Product #{o.get('product_id', '?')}"
        date = (o.get("created_at", "")[:10] or "?")
        btn_text = f"{emoji} {name} — ${o['amount']:.2f} | {date}"
        pid = emoji_premium_id(db_emoji)
        icon_id = str(pid) if pid else None
        if pid:
            plain, _ = parse_db_emoji(db_emoji)
            if btn_text.startswith(plain):
                btn_text = btn_text[len(plain):].strip()
        buttons.append([_safe_button(btn_text, f"order_detail_{o['id']}", icon_id)])

    buttons.append([_make_smart_button("Back", "menu_start", "back")])

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show order details and send account file."""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("order_detail_", ""))
    conn = get_db()
    try:
        orders = get_user_orders(conn, query.from_user.id, limit=50)
        order = next((o for o in orders if o["id"] == order_id), None)
    finally:
        conn.close()

    if not order:
        await query.edit_message_text("Order not found.", reply_markup=_back_button("menu_myorder"))
        return

    detail = order.get("stock_detail", "No data")
    name = order.get("product_name") or f"Product #{order.get('product_id', '?')}"
    emoji = emoji_for_html(order.get("product_emoji", "📦"))
    # Full datetime: 2026-07-13 15:56
    date_full = (order.get("created_at", "")[:16] or "?").replace("T", " ")
    date_short = (order.get("created_at", "")[:10] or "?")

    # Build file content with date/time header
    file_content = (
        f"🛒 Order #{order_id}\n"
        f"📦 Product: {name}\n"
        f"💰 Amount: ${order['amount']:.2f}\n"
        f"📅 Date: {date_full}\n"
        f"{'─' * 30}\n\n"
        f"{detail}"
    )
    file_bytes = io.BytesIO(file_content.encode("utf-8"))
    file_bytes.name = f"order_{order_id}_{name.replace(' ','_')[:20]}.txt"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=file_bytes,
        caption=f"📄 {emoji} <b>{name}</b> — ${order['amount']:.2f}\n📅 {date_full}",
        parse_mode="HTML",
    )

    await query.edit_message_text(
        f"{emoji} <b>{name}</b>\n\n"
        f"💰 Amount: <b>${order['amount']:.2f}</b>\n"
        f"📅 Date: {date_full}\n\n"
        f"<i>Account details in the file above.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [_make_smart_button("Back to Orders", "menu_myorder", "back")],
        ]),
    )


# ===========================================================================
# ROUTER
# ===========================================================================
async def router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route callback queries to the right handler."""
    query = update.callback_query
    data = query.data or ""

    # Register every user who taps a button (covers users who came via a shared
    # link and clicked the menu without ever sending /start).
    user = update.effective_user
    if user:
        try:
            conn = get_db()
            try:
                _cached_get_user(conn, user.id, user.username, user.first_name)
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"router(): could not register user {user.id}: {e}")

    try:
        await _route_callback(update, context, data)
    except Exception as e:
        # Ignore "Message is not modified" — harmless repeated click.
        msg = str(e)
        if "Message is not modified" in msg:
            try:
                await query.answer()
            except Exception:
                pass
            return
        logger.exception(f"Router error handling '{data}': {e}")
        try:
            await query.answer()
        except Exception:
            pass


async def _route_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, data: str) -> None:
    """Dispatch a callback query to the matching handler."""
    query = update.callback_query

    # --- Main Menu ---
    if data == "menu_profile":
        await profile(update, context)
    elif data == "menu_wallet":
        await wallet(update, context)
    elif data == "wallet_deposit":
        await deposit_menu(update, context)
    elif data.startswith("depamt_bakong_") or data.startswith("deposit_amt_"):
        await deposit_create_checkout(update, context)
    elif data == "deposit_custom":
        await deposit_custom_start(update, context)
    elif data == "menu_start":
        await start(update, context)
    elif data == "menu_support":
        from config import SUPPORT_USERNAME
        await query.answer()
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=f"💬 <b>Support</b>\n\nContact us: {SUPPORT_USERNAME}\nTap to open chat.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💬 Open Support", url=f"https://t.me/{SUPPORT_USERNAME.lstrip('@')}"),
                _make_smart_button("Back", "menu_start", "back"),
            ]]),
        )

    # --- Product Browsing ---
    elif data == "menu_product":
        await product_categories(update, context)
    elif data.startswith("prod_cat_"):
        await product_categories(update, context)

    # --- Buy Flow ---
    elif data.startswith("buy_detail_"):
        await buy_detail(update, context)
    elif data.startswith("pay_wallet_") or data.startswith("pay_khqr_"):
        await pay_confirm_prompt(update, context)
    elif data.startswith("dowallet_"):
        await buy_execute(update, context)
    elif data.startswith("dokhqr_"):
        await pay_khqr_start(update, context)
    elif data.startswith("apply_promo_"):
        await apply_promo_start(update, context)

    # --- My Order ---
    elif data == "menu_myorder":
        await my_orders(update, context)
    elif data.startswith("order_detail_"):
        await order_detail(update, context)
    elif data == "custom_order":
        await custom_order_start(update, context)

    # --- Admin Dashboard ---
    elif data == "admin_dashboard":
        await admin_dashboard(update, context)

    # --- Admin Panel ---
    elif data == "admin_panel":
        await admin_panel(update, context)

    # --- Admin Categories ---
    elif data == "admin_categories":
        await admin_categories(update, context)
    elif data == "admin_add_category":
        await admin_add_category_start(update, context)
    elif data == "admin_del_category":
        await admin_del_category_start(update, context)
    elif data.startswith("admin_del_cat_ask_"):
        await admin_del_category_confirm(update, context)
    elif data.startswith("admin_del_cat_exec_"):
        await admin_del_category_execute(update, context)
    elif data == "admin_edit_category":
        await admin_edit_category_start(update, context)
    elif data.startswith("admin_edit_cat_") and not data.startswith("admin_editcat_"):
        await admin_edit_cat_choose(update, context)
    elif data.startswith("admin_editcat_name_"):
        await admin_editcat_name(update, context)
    elif data.startswith("admin_editcat_emoji_"):
        await admin_editcat_emoji(update, context)

    # --- Admin Products ---
    elif data == "admin_products":
        await admin_products(update, context)
    elif data == "admin_add_product":
        await admin_add_product_start(update, context)
    elif data.startswith("admin_prod_cat_"):
        await admin_prod_cat_selected(update, context)
    elif data == "admin_del_product":
        await admin_del_product_start(update, context)
    elif data.startswith("admin_del_prod_ask_"):
        await admin_del_product_confirm(update, context)
    elif data.startswith("admin_del_prod_exec_"):
        await admin_del_product_execute(update, context)
    elif data == "admin_edit_product":
        await admin_edit_product_start(update, context)
    elif data.startswith("admin_edit_prod_") and not data.startswith("admin_ep_"):
        await admin_edit_prod_choose(update, context)
    elif data.startswith("admin_ep_"):
        await admin_edit_prod_field(update, context)

    # --- Admin Stock ---
    elif data == "admin_stock_menu":
        await admin_stock_menu(update, context)
    elif data == "admin_add_stock":
        await admin_add_stock_start(update, context)
    elif data.startswith("admin_stock_prod_"):
        await admin_stock_prod_selected(update, context)
    elif data.startswith("admin_stock_limited_"):
        await admin_stock_limited_start(update, context)
    elif data.startswith("admin_stock_unlimited_"):
        await admin_stock_unlimited_toggle(update, context)
    elif data == "admin_view_stock":
        await admin_view_stock(update, context)
    elif data.startswith("admin_view_stock_"):
        await admin_view_stock_detail(update, context)
    elif data.startswith("admin_clear_stock_"):
        await admin_clear_stock(update, context)
    elif data == "admin_edit_stock_menu":
        await admin_edit_stock_menu(update, context)
    elif data == "admin_editstock_single":
        await admin_editstock_single(update, context)
    elif data.startswith("admin_es_prod_"):
        await admin_editstock_prod_selected(update, context)
    elif data.startswith("admin_es_item_"):
        await admin_editstock_item_actions(update, context)
    elif data.startswith("admin_es_edit_"):
        await admin_editstock_edit_detail(update, context)
    elif data.startswith("admin_es_del_"):
        await admin_editstock_delete(update, context)
    elif data == "admin_editstock_replace":
        await admin_editstock_replace(update, context)
    elif data.startswith("admin_es_replace_"):
        await admin_editstock_replace_start(update, context)

    # --- Admin Promo Codes ---
    elif data == "admin_promos":
        await admin_promos(update, context)
    elif data == "admin_add_promo":
        await admin_add_promo_start(update, context)
    elif data == "admin_del_promo":
        await admin_del_promo_start(update, context)
    elif data.startswith("admin_del_promo_"):
        await admin_del_promo_confirm(update, context)

    # --- Admin Users ---
    elif data == "admin_users" or data.startswith("admin_users_p"):
        await admin_users(update, context)
    elif data == "admin_list_users" or data.startswith("admin_list_users_p"):
        await admin_list_users(update, context)
    elif data == "admin_search_user":
        await admin_search_user_prompt(update, context)
    elif data.startswith("admin_user_detail_"):
        await admin_user_detail(update, context)
    elif data.startswith("admin_ban_"):
        await admin_ban_user(update, context)
    elif data.startswith("admin_unban_"):
        await admin_unban_user(update, context)
    elif data.startswith("admin_addbal_"):
        await admin_addbal_start(update, context)
    elif data.startswith("admin_deduct_"):
        await admin_deduct_start(update, context)
    elif data.startswith("admin_user_orders_"):
        await admin_user_orders(update, context)
    elif data.startswith("admin_dm_"):
        await admin_dm_start(update, context)

    # --- Admin Reports ---
    elif data == "admin_reports":
        await admin_reports(update, context)
    elif data == "report_sales":
        await report_sales(update, context)
    elif data == "report_bestsellers":
        await report_bestsellers(update, context)
    elif data == "report_revenue":
        await report_revenue(update, context)
    elif data == "report_stock":
        await report_stock_sold(update, context)
    elif data == "report_growth":
        await report_user_growth(update, context)

    # --- Admin Customize (Emoji) ---
    elif data == "admin_customize":
        await admin_customize(update, context)
    elif data == "custom_set_emoji":
        await custom_sections(update, context)
    elif data == "custom_reset_all":
        await custom_reset_all(update, context)
    elif data == "custom_reset_all_confirm":
        await custom_reset_all_confirm(update, context)
    elif data.startswith("custom_sec_"):
        await custom_section_detail(update, context)
    elif data.startswith("custom_emoji_"):
        await custom_emoji_detail(update, context)
    elif data.startswith("custom_setval_"):
        await custom_set_value_start(update, context)
    elif data.startswith("custom_update_"):
        await custom_set_value_start(update, context)
    elif data.startswith("custom_reset_"):
        await custom_reset_key(update, context)

    # --- Admin Broadcasting ---
    elif data == "admin_broadcast":
        await admin_broadcast(update, context)
    elif data == "admin_broadcast_start":
        await admin_broadcast_start(update, context)

    # --- Admin Order Management ---
    elif data == "admin_orders_mgmt":
        await admin_orders_mgmt(update, context)
    elif data.startswith("admin_orders_list_"):
        await admin_orders_list(update, context)
    elif data.startswith("admin_order_detail_"):
        await admin_order_detail(update, context)
    elif data.startswith("admin_order_refund_"):
        await admin_order_refund(update, context)
    elif data == "admin_order_search":
        await admin_order_search_prompt(update, context)

    # --- Admin Payment Management ---
    elif data == "admin_payments":
        await admin_payments(update, context)
    elif data.startswith("admin_pay_list_"):
        await admin_pay_list(update, context)

    # --- Admin Enhanced Users ---
    elif data == "admin_users_enhanced":
        await admin_users_enhanced(update, context)
    elif data.startswith("admin_vip_set_"):
        await admin_user_vip_set(update, context)
    elif data.startswith("admin_discount_set_"):
        await admin_user_discount_set(update, context)

    # --- Admin Settings ---
    elif data == "admin_settings":
        await admin_settings(update, context)
    elif data == "admin_settings_maintenance":
        await admin_settings_maintenance(update, context)
    elif data == "admin_settings_welcome":
        await admin_settings_welcome(update, context)
    elif data == "admin_settings_notify":
        await admin_settings_notify(update, context)
    elif data == "admin_settings_restock":
        await admin_settings_restock(update, context)
    elif data == "admin_settings_payment":
        await admin_settings_payment(update, context)
    elif data == "admin_pay_profile":
        await admin_pay_set_profile(update, context)
    elif data == "admin_pay_secret":
        await admin_pay_set_secret(update, context)
    elif data == "admin_pay_aba":
        await admin_pay_set_aba(update, context)
    elif data == "admin_pay_reset":
        await admin_pay_reset(update, context)

    # --- Admin Export ---
    elif data == "admin_export_csv":
        await admin_export_csv(update, context)

    # --- Admin Backup & Restore ---
    elif data == "admin_backup":
        await admin_backup(update, context)
    elif data == "admin_backup_create":
        await admin_backup_create(update, context)
    elif data == "admin_backup_restore":
        await admin_backup_restore(update, context)

    # --- Admin Button Styles ---
    elif data == "admin_button_styles":
        await admin_button_styles(update, context)
    elif data.startswith("btn_sec_"):
        await admin_button_section(update, context)
    elif data.startswith("btn_detail_"):
        await admin_button_detail(update, context)
    elif data.startswith("btn_set_") or data.startswith("btn_prompt_"):
        await admin_button_style_set(update, context)

    else:
        await query.answer("Unknown action")


# ===========================================================================
# UNIFIED TEXT HANDLER
# ===========================================================================
async def unified_text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route text messages: admin input vs user promo code input vs custom qty."""
    user = update.effective_user
    # Register every user who sends any message so admin's user list is complete.
    if user:
        try:
            conn = get_db()
            try:
                get_or_create_user(conn, user.id, user.username, user.first_name)
                order_count = get_order_count(conn, user.id)
                if order_count == 0:
                    _notify_new_user(context, user)
            finally:
                conn.close()
        except Exception as e:
            logger.warning(f"unified_text_handler(): could not register user {user.id}: {e}")

    if user.id == ADMIN_ID and context.user_data.get("admin_state"):
        await handle_admin_text(update, context)
    elif context.user_data.get("buy_state") == "buy_promo":
        await handle_buy_promo(update, context)
    elif context.user_data.get("buy_state") == "buy_custom_qty":
        await _handle_custom_qty(update, context)
    elif context.user_data.get("buy_state") == "custom_deposit":
        await _handle_custom_deposit(update, context)
    elif context.user_data.get("buy_state") == "custom_order":
        await _handle_custom_order(update, context)


async def _handle_custom_qty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle quantity input, then show payment options (Wallet / Bakong)."""
    if not update.message:
        return
    try:
        qty = int(update.message.text.strip())
        if qty < 1:
            raise ValueError
    except ValueError:
        await update.message.reply_html(f"{E('error')} Enter a valid number (1 or more).")
        return

    buy_data = context.user_data.get("buy_data", {})
    prod_id = buy_data.get("prod_id")
    context.user_data.pop("buy_state", None)
    context.user_data.pop("buy_data", None)

    if not prod_id:
        return

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        is_unlimited = prod.get("is_unlimited", 0) if prod else 0
        if not is_unlimited:
            stock_items = get_stock_for_product(conn, prod_id, unsold_only=True)
            max_stock = len(stock_items)
        else:
            max_stock = None
    finally:
        conn.close()

    if not prod:
        await update.message.reply_html(f"{E('error')} Product not found.")
        return

    if not is_unlimited and qty > max_stock:
        await update.message.reply_html(
            f"{E('out_of_stock')} Only <b>{max_stock}</b> available. Enter a smaller number:",
        )
        # Keep the state alive so the user can retry
        context.user_data["buy_state"] = "buy_custom_qty"
        context.user_data["buy_data"] = {"prod_id": prod_id}
        return

    context.user_data.setdefault("buy_qty", {})[str(prod_id)] = qty
    await _send_pay_options(update.message.chat_id, context, prod, qty)


async def _send_pay_options(chat_id: int, context: ContextTypes.DEFAULT_TYPE,
                            prod: dict, qty: int, edit_query=None) -> None:
    """
    Show product summary + Pay with Wallet / Pay with KHQR / Apply Promo / Cancel.
    If `edit_query` is given, edit that callback's message; otherwise send a new one.
    """
    prod_id = prod["id"]
    price = prod["price"]
    promo_data = context.user_data.get("active_promo")

    unit_price = price
    discount = 0
    promo_note = ""
    if promo_data:
        discount, unit_price = calculate_discount(promo_data, price)
        promo_note = (
            f"\n{E('promo_code')} Promo: <code>{promo_data['code']}</code>"
            f" (-${discount:.2f}/each)"
        )

    total = unit_price * qty
    desc = prod.get("description", "")
    desc_text = f"\n{E('description')} <i>{desc}</i>" if desc else ""

    text = (
        f"{emoji_for_html(prod['emoji'])} <b>{prod['name']}</b>{desc_text}\n\n"
        f"{E('qty_custom')} Quantity: <b>{qty}</b>\n"
        f"{E('price_label')} Unit: <b>${unit_price:.2f}</b>{promo_note}\n"
        f"{E('price_label')} Total: <b>${total:.2f}</b>\n\n"
        f"Choose a payment method:"
    )

    cb_suffix = f"{prod_id}_{qty}"
    if promo_data:
        cb_suffix += f"_promo_{promo_data['id']}"

    keyboard = [
        [_make_smart_button(f"Pay with Wallet — ${total:.2f}",
                            f"pay_wallet_{cb_suffix}", "pay_wallet")],
        [_make_smart_button(f"Pay with KHQR — ${total:.2f}",
                            f"pay_khqr_{cb_suffix}", "pay_khqr")],
        [_make_smart_button("Apply Promo Code", f"apply_promo_{prod_id}", "apply_promo")],
        [_make_smart_button("Cancel", f"prod_cat_{prod['category_id']}", "cancel")],
    ]

    markup = InlineKeyboardMarkup(keyboard)
    if edit_query is not None:
        await edit_query.edit_message_text(text, parse_mode="HTML", reply_markup=markup)
    else:
        await context.bot.send_message(
            chat_id=chat_id, text=text, parse_mode="HTML", reply_markup=markup,
        )


async def _handle_custom_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle custom deposit amount via KHQRPay checkout."""
    if not update.message:
        return
    try:
        amount = float(update.message.text.strip())
        if amount < 0.01:
            raise ValueError
    except ValueError:
        await update.message.reply_html("❌ Enter a valid amount (e.g. 3.50).")
        return

    context.user_data.pop("buy_state", None)
    context.user_data.pop("deposit_method", None)
    user = update.effective_user

    # Get KHQRPay config
    conn = get_db()
    try:
        cfg = get_khqrpay_config(conn)
    finally:
        conn.close()

    if not cfg["profile_id"] or not cfg["secret_key"]:
        await update.message.reply_html(
            "❌ Payment gateway not configured. Contact admin.",
            reply_markup=_back_button("menu_wallet"),
        )
        return

    transaction_id = f"RAT-{uuid.uuid4().hex[:8].upper()}"
    result = await create_aba_qr(
        profile_id=cfg["profile_id"],
        secret_key=cfg["secret_key"],
        transaction_id=transaction_id,
        amount=amount,
        remark=f"Custom deposit for {user.id}",
    )

    if not result["success"]:
        await update.message.reply_html(f"❌ QR failed: {result.get('error')}")
        return

    conn = get_db()
    try:
        payment_id = create_payment(conn, user.id, amount, result["qr_text"], transaction_id)
    finally:
        conn.close()

    msg = await context.bot.send_photo(
        chat_id=update.effective_chat.id,
        photo=result["qr_image_url"],
        caption=(
            f"{E('deposit')} <b>Deposit ${amount:.2f}</b>\n\n"
            f"📱 Scan with ABA Mobile or any Bakong app.\n"
            f"{E('timer')} Expires in: <b>03:00</b>"
        ),
        parse_mode="HTML",
    )

    asyncio.create_task(_khqrpay_watcher(
        payment_id=payment_id,
        cfg=cfg,
        transaction_id=transaction_id,
        amount=amount,
        user_id=user.id,
        chat_id=update.effective_chat.id,
        message_id=msg.message_id,
        context=context,
    ))




# ===========================================================================
# MAIN
# ===========================================================================
def main() -> None:
    # concurrent_updates lets PTB process independent updates in parallel
    # (default = 1 → serial). Big responsiveness boost with many users.
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("admin", admin_panel))

    # Callback queries (inline buttons)
    app.add_handler(CallbackQueryHandler(router))

    # Text messages (admin input + user promo code input + /skip for description)
    app.add_handler(MessageHandler(filters.TEXT, unified_text_handler))

    # Media messages (broadcast: photo, video, voice, document, animation)
    app.add_handler(MessageHandler(filters.PHOTO, unified_text_handler))
    app.add_handler(MessageHandler(filters.VIDEO, unified_text_handler))
    app.add_handler(MessageHandler(filters.VOICE, unified_text_handler))
    app.add_handler(MessageHandler(filters.Document.ALL, unified_text_handler))
    app.add_handler(MessageHandler(filters.ANIMATION, unified_text_handler))

    # ── Error handler ──
    _conflict_logged = False  # debounce so we don't flood logs every 5 seconds

    async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        nonlocal _conflict_logged
        err = context.error
        if isinstance(err, Conflict):
            if not _conflict_logged:
                _conflict_logged = True
                logger.warning(
                    "⛔ Conflict: Another bot instance is already polling. "
                    "Waiting for the other instance to release the token… "
                    "(This is normal during Railway deploys — the bot will "
                    "recover automatically once the old instance exits.)"
                )
            # Don't stop() – the app may not be fully started yet,
            # and PTB retries getUpdates automatically.
            return
        elif isinstance(err, NetworkError):
            logger.error(f"🌐 Network error (will retry): {err}")
        elif isinstance(err, TimedOut):
            logger.error(f"⏱️ Request timed out (will retry): {err}")
        elif isinstance(err, TelegramError):
            logger.error(f"📡 Telegram API error: {err}")
        else:
            logger.exception(f"❌ Unhandled error: {err}")

    app.add_error_handler(error_handler)

    if WEBHOOK_URL:
        # ── Webhook mode (Railway Web Service) ──
        webhook_url = f"{WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"
        logger.info(f"🔗 Webhook: {webhook_url} (port {WEBHOOK_PORT})")
        app.run_webhook(
            listen="0.0.0.0",
            port=WEBHOOK_PORT,
            url_path=WEBHOOK_PATH,
            webhook_url=webhook_url,
            drop_pending_updates=True,
        )
    else:
        # ── Polling mode ──
        logger.info("🤖 Bot is starting (polling)...")
        app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    # Python 3.12+: always create a fresh event loop to avoid
    # DeprecationWarning on get_event_loop() and RuntimeError on 3.14+
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    main()
