"""
Admin panel handlers for Rat Store bot.
Admin ID: 7322712989 (config.ADMIN_ID)

State machine via context.user_data["admin_state"]:
  - None: idle
  - "cat_name": waiting for category name
  - "cat_emoji": waiting for category emoji
  - "prod_cat": waiting for product category selection
  - "prod_name": waiting for product name
  - "prod_price": waiting for product price
  - "prod_emoji": waiting for product emoji
  - "prod_desc": waiting for product description
  - "stock_prod": waiting for stock product selection
  - "stock_account": waiting for stock account text
  - "promo_code": waiting for promo code name
  - "promo_type": waiting for promo discount type
  - "promo_value": waiting for promo discount value
  - "promo_max": waiting for promo max uses
  - "promo_min": waiting for promo min order
"""
import asyncio
import json
import logging

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ADMIN_ID
from utils.emoji_manager import (get_all, get as eget, get_plain, get_premium_id,
                           set_emoji, reset, SECTIONS, LABELS, DEFAULTS,
                           emoji_for_html, emoji_for_button, emoji_premium_id,
                           parse_db_emoji, load_button_config, get_button_style,
                           strip_leading_emoji)
from services.database import (
    add_category,
    add_product,
    add_stock_bulk,
    ban_user,
    create_promo_code,
    delete_category,
    delete_product,
    delete_promo_code,
    delete_stock,
    get_all_categories,
    get_all_products,
    get_all_promo_codes,
    get_all_users,
    get_best_sellers,
    get_dashboard_stats,
    get_db,
    get_product,
    get_revenue_by_payment,
    get_sales_report,
    get_stock_for_product,
    get_stock_count,
    get_stock_item,
    get_stock_sold_report,
    get_user_by_id,
    get_user_growth,
    get_user_orders,
    replace_all_stock,
    set_product_unlimited,
    unban_user,
    update_category,
    update_product,
    update_stock_detail,
    update_user_balance,
    # New imports
    get_all_orders,
    search_orders,
    refund_order,
    get_users_with_stats,
    set_user_vip,
    get_bot_setting,
    set_bot_setting,
    export_orders_csv,
    export_users_csv,
)

logger = logging.getLogger(__name__)


def _cat_btn(label: str, callback_data: str, db_emoji) -> InlineKeyboardButton:
    """Button with premium emoji icon — only new premium icon, no old unicode."""
    pid = emoji_premium_id(db_emoji)
    icon_id = str(pid) if pid else None
    if pid:
        label = strip_leading_emoji(label) or label
    kwargs = {"text": label or "•", "callback_data": callback_data}
    if icon_id:
        kwargs["icon_custom_emoji_id"] = icon_id
    try:
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        return InlineKeyboardButton(text=label or "•", callback_data=callback_data)


def _styled_btn(label: str, callback_data: str, key: str) -> InlineKeyboardButton:
    """
    Create a styled admin button using button_config.json for colors
    and emoji_config.json for premium emoji icons.

    When a premium icon is set, ALL leading unicode emoji are stripped from the
    label so Telegram shows only the new premium icon (never old + new together).
    """
    btn_cfg = load_button_config()
    cfg = btn_cfg.get(key, {}) if btn_cfg else {}
    style = cfg.get("style") if isinstance(cfg, dict) else None

    # Premium icon from emoji_config (single source of truth)
    premium_id = get_premium_id(key)

    if premium_id:
        # Drop hardcoded / old unicode from label — premium icon replaces it
        label = strip_leading_emoji(label) or label

    kwargs = {"text": label or "•", "callback_data": callback_data}
    if premium_id:
        kwargs["icon_custom_emoji_id"] = str(premium_id)
    if style and style != "none":
        kwargs["style"] = style
    try:
        return InlineKeyboardButton(**kwargs)
    except TypeError:
        # Old PTB without icon_custom_emoji_id — keep plain label only
        return InlineKeyboardButton(text=label or "•", callback_data=callback_data)


async def _send_restock_notify(context, prod: dict, stock: int, chat_id: int) -> None:
    """Send restock notification to all users who bought or may be interested."""
    from services.database import get_db, get_all_users, get_bot_setting

    name = prod.get("name", "Product")
    price = prod.get("price", 0)
    emoji_raw = prod.get("emoji", "📦")

    notify_msg = (
        f"{emoji_for_html(emoji_raw)} <b>Restock Alert!</b>\n\n"
        f"📦 <b>{name}</b>\n"
        f"💰 Price: <b>${price:.2f}</b>\n"
        f"📥 Stock: <b>{stock}</b>\n\n"
        f"<i>Tap below to shop now!</i>"
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"🛒 Buy Now",
            callback_data=f"buy_detail_{prod['id']}"
        )
    ]])

    conn = get_db()
    try:
        users = get_all_users(conn)
    finally:
        conn.close()

    sent = 0
    for u in users:
        uid = u.get("user_id")
        if not uid or uid == ADMIN_ID:
            continue
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=notify_msg,
                parse_mode="HTML",
                reply_markup=keyboard,
            )
            sent += 1
        except Exception:
            pass  # User may have blocked bot
        await asyncio.sleep(0.05)  # Rate limit safety

    # Confirm to admin
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📢 Restock notification sent to <b>{sent}</b> user(s).",
        parse_mode="HTML",
    )


# ===========================================================================
# Entry: /admin command
# ===========================================================================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show admin main panel (admin only)."""
    user = update.effective_user
    if user.id != ADMIN_ID:
        if update.message:
            await update.message.reply_text("⛔ Access denied.")
        return

    context.user_data.pop("admin_state", None)
    context.user_data.pop("admin_data", None)

    keyboard = [
        [_styled_btn("📊 Dashboard", "admin_dashboard", "admin_dashboard")],
        [_styled_btn("📦 Products", "admin_products", "admin_products")],
        [_styled_btn("📥 Stock", "admin_stock_menu", "admin_stock"),
         _styled_btn("🎟 Promos", "admin_promos", "admin_promos")],
        [_styled_btn("👥 Users", "admin_users", "admin_users_btn"),
         _styled_btn("📈 Reports", "admin_reports", "admin_reports")],
        [_styled_btn("🛒 Orders", "admin_orders_mgmt", "admin_orders"),
         _styled_btn("💵 Payments", "admin_payments", "admin_payments")],
        [_styled_btn("📢 Broadcast", "admin_broadcast", "admin_broadcast")],
        [_styled_btn("🎨 Customize", "admin_customize", "admin_customize"),
         _styled_btn("🎨 Button Styles", "admin_button_styles", "admin_button_styles")],
        [_styled_btn("⚙️ Settings", "admin_settings", "admin_settings"),
         _styled_btn("💾 Backup", "admin_backup", "admin_backup")],
        [_styled_btn("📥 Export CSV", "admin_export_csv", "admin_export")],
        [_styled_btn("🔙 Close", "menu_start", "admin_close")],
    ]

    text = "🛠 <b>Admin Panel</b>\n\nManage your store:"

    if update.message:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(keyboard))
    elif update.callback_query:
        await update.callback_query.edit_message_text(
            text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
        )


# ===========================================================================
# CATEGORIES
# ===========================================================================
async def admin_categories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all categories with add/delete options."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        cats = get_all_categories(conn)
    finally:
        conn.close()

    if cats:
        lines = []
        for c in cats:
            lines.append(f"{emoji_for_html(c['emoji'])} <b>{c['name']}</b>")
        text = "📂 <b>Categories</b>\n\n" + "\n".join(lines)
    else:
        text = "📂 <b>Categories</b>\n\nNo categories yet."

    keyboard = [
        [_styled_btn("➕ Add Category", "admin_add_category", "admin_dashboard")],
    ]
    if cats:
        keyboard.append([_styled_btn("✏️ Edit Category", "admin_edit_category", "admin_products")])
        keyboard.append([_styled_btn("🗑 Delete Category", "admin_del_category", "admin_close")])
    keyboard.append([_styled_btn("🔙 Back", "admin_panel", "admin_close")])

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_add_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to enter category name."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "cat_name"
    context.user_data["admin_data"] = {}

    await query.edit_message_text(
        "📂 <b>New Category</b>\n\n"
        "Please <b>type</b> the category name:\n\n"
        "<i>Example: Premium Telegram</i>",
        parse_mode="HTML",
        reply_markup=_cancel_button(),
    )


async def admin_del_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show category list for deletion."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        cats = get_all_categories(conn)
    finally:
        conn.close()

    if not cats:
        await query.edit_message_text(
            "📂 No categories to delete.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_categories")
            ]]),
        )
        return

    buttons = []
    for c in cats:
        buttons.append([_cat_btn(
            f"{emoji_for_button(c['emoji'])} {c['name']}",
            f"admin_del_cat_ask_{c['id']}", c['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_categories")])

    await query.edit_message_text(
        "🗑 <b>Delete Category</b>\n\nSelect a category to delete:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_del_category_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show confirmation before deleting category."""
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.replace("admin_del_cat_ask_", ""))
    conn = get_db()
    try:
        from services.database import get_category
        cat = get_category(conn, cat_id)
        cat_name = cat["name"] if cat else "Unknown"
    finally:
        conn.close()

    await query.edit_message_text(
        f"⚠️ <b>Delete Category?</b>\n\n"
        f"{emoji_for_html(cat['emoji'])} <b>{cat_name}</b>\n\n"
        f"Products in this category will remain but become uncategorized.\n"
        f"<i>This action can be undone by re-adding the category.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Delete", callback_data=f"admin_del_cat_exec_{cat_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="admin_categories"),
            ]
        ])
    )


async def admin_del_category_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the actual category deletion."""
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.replace("admin_del_cat_exec_", ""))
    conn = get_db()
    try:
        delete_category(conn, cat_id)
    finally:
        conn.close()

    await query.edit_message_text(
        "✅ Category deleted!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Categories", callback_data="admin_categories")
        ]]),
    )


# ===========================================================================
# PRODUCTS
# ===========================================================================
async def admin_products(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all products. Clears any in-progress admin state."""
    query = update.callback_query
    await query.answer()

    # Clear any admin state when navigating to products (clean cancel path)
    context.user_data.pop("admin_state", None)
    context.user_data.pop("admin_data", None)

    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if products:
        lines = []
        for p in products:
            is_unl = p.get("is_unlimited", 0)
            stock_disp = "Unlimited" if is_unl else str(p.get("stock_count", 0))
            lines.append(
                f"{emoji_for_html(p['emoji'])} <b>{p['name']}</b> — ${p['price']:.2f} "
                f"| Stock: {stock_disp}"
            )
        text = "📦 <b>Products</b>\n\n" + "\n".join(lines)
    else:
        text = "📦 <b>Products</b>\n\nNo products yet."

    keyboard = [
        [_styled_btn("➕ Add Product", "admin_add_product", "admin_dashboard")],
        [_styled_btn("✏️ Edit Product", "admin_edit_product", "admin_products")],
        [_styled_btn("🗑 Delete Product", "admin_del_product", "admin_close")],
        [_styled_btn("🔙 Back", "admin_panel", "admin_close")],
    ]

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_add_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Skip category selection — auto-assign to the 'General' category (create it if missing).
    Categories are hidden from admin/user UI, but the DB still requires each product
    to reference one, so we keep a single default bucket.
    """
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        cats = get_all_categories(conn)
        # Reuse an existing category if any exist; otherwise create a "General" bucket.
        if cats:
            default_cat_id = cats[0]["id"]
        else:
            default_cat_id = add_category(conn, "General", "📦")
    finally:
        conn.close()

    context.user_data["admin_state"] = "prod_name"
    context.user_data["admin_data"] = {"cat_id": default_cat_id}

    await query.edit_message_text(
        "📦 <b>New Product — Step 1/4</b>\n\n"
        "Send the <b>product name</b>:\n\n"
        "<i>Example: Netflix Premium 1-Month</i>",
        parse_mode="HTML",
        reply_markup=_cancel_button("admin_products"),
    )


async def admin_prod_cat_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Category selected → ask for product name."""
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.replace("admin_prod_cat_", ""))
    context.user_data["admin_data"]["cat_id"] = cat_id

    conn = get_db()
    try:
        from services.database import get_category
        cat = get_category(conn, cat_id)
        cat_name = cat["name"] if cat else "Unknown"
    finally:
        conn.close()

    context.user_data["admin_state"] = "prod_name"

    await query.edit_message_text(
        f"📦 <b>New Product — Step 2/4</b>\n\n"
        f"Category: {cat_name}\n\n"
        f"Please <b>type</b> the product name:\n\n"
        f"<i>Example: Gemini 18 Month</i>",
        parse_mode="HTML",
        reply_markup=_cancel_button("admin_products"),
    )


async def admin_del_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show product list for deletion."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if not products:
        await query.edit_message_text(
            "📦 No products to delete.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_products")
            ]]),
        )
        return

    buttons = []
    for p in products:
        buttons.append([_cat_btn(
            f"{emoji_for_button(p['emoji'])} {p['name']} (${p['price']:.2f})",
            f"admin_del_prod_ask_{p['id']}", p['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_products")])

    await query.edit_message_text(
        "🗑 <b>Delete Product</b>\n\nSelect a product to delete:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_del_product_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show confirmation before deleting product."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_del_prod_ask_", ""))
    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        prod_name = prod["name"] if prod else "Unknown"
        stock = prod.get("stock_count", 0) if prod else 0
        is_unl = prod.get("is_unlimited", 0) if prod else 0
        stock_disp = "Unlimited" if is_unl else str(stock)
    finally:
        conn.close()

    await query.edit_message_text(
        f"⚠️ <b>Delete Product?</b>\n\n"
        f"{emoji_for_html(prod['emoji'])} <b>{prod_name}</b>\n"
        f"💰 ${prod['price']:.2f} | 📦 Stock: {stock_disp}\n\n"
        f"All related stock items will be hidden.\n"
        f"<i>This hides the product, not permanently deletes it.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Delete", callback_data=f"admin_del_prod_exec_{prod_id}"),
                InlineKeyboardButton("❌ Cancel", callback_data="admin_products"),
            ]
        ])
    )


async def admin_del_product_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the actual product deletion."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_del_prod_exec_", ""))
    conn = get_db()
    try:
        delete_product(conn, prod_id)
    finally:
        conn.close()

    await query.edit_message_text(
        "✅ Product deleted!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Products", callback_data="admin_products")
        ]]),
    )


# ===========================================================================
# STOCK MANAGEMENT
# ===========================================================================
async def admin_stock_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show stock management menu."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("➕ Add Stock", callback_data="admin_add_stock")],
        [InlineKeyboardButton("📋 View Stock", callback_data="admin_view_stock")],
        [InlineKeyboardButton("✏️ Edit Stock", callback_data="admin_edit_stock_menu")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        "📥 <b>Stock Management</b>\n\nAdd or view stock accounts for products.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_add_stock_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Step 1: Select product for stock upload."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if not products:
        await query.edit_message_text(
            "❌ No products exist. Please create a product first.",
            reply_markup=InlineKeyboardMarkup([[
                _styled_btn("➕ Add Product", "admin_add_product", "admin_dashboard"),
                _styled_btn("🔙 Back", "admin_stock_menu", "admin_close"),
            ]]),
        )
        return

    context.user_data["admin_state"] = "stock_prod"
    context.user_data["admin_data"] = {}

    buttons = []
    for p in products:
        stock = p.get("stock_count", 0)
        is_unlimited = p.get("is_unlimited", 0)
        if is_unlimited:
            stock_label = "♾️ Unlimited"
        else:
            stock_label = f"Stock: {stock}"
        buttons.append([_cat_btn(
            f"{emoji_for_button(p['emoji'])} {p['name']} ({stock_label})",
            f"admin_stock_prod_{p['id']}", p['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Cancel", callback_data="admin_stock_menu")])

    await query.edit_message_text(
        "📥 <b>Add Stock — Step 1/2</b>\n\nSelect a <b>product</b>:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_stock_prod_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Product selected → choose Limited or Unlimited stock mode."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_stock_prod_", ""))
    context.user_data["admin_data"]["prod_id"] = prod_id

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        prod_name = prod["name"] if prod else "Unknown"
        is_unlimited = prod.get("is_unlimited", 0) if prod else 0
        stock = prod.get("stock_count", 0) if prod else 0
    finally:
        conn.close()

    unlimited_status = "✅ ON (♾️ Unlimited)" if is_unlimited else "❌ OFF (Limited)"
    stock_info = f"📦 Current stock: <b>{stock}</b> items" if not is_unlimited else "♾️ Stock mode: <b>Unlimited</b>"

    keyboard = [
        [InlineKeyboardButton("➕ Add Limited Stock", callback_data=f"admin_stock_limited_{prod_id}")],
        [InlineKeyboardButton(
            f"{'🔴 Disable' if is_unlimited else '🟢 Enable'} Unlimited Stock",
            callback_data=f"admin_stock_unlimited_{prod_id}"
        )],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_add_stock")],
    ]

    await query.edit_message_text(
        f"📥 <b>Add Stock — {prod_name}</b>\n\n"
        f"{stock_info}\n"
        f"Unlimited mode: {unlimited_status}\n\n"
        f"<b>Choose stock mode:</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def admin_stock_limited_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Limited stock mode → ask for account details (original flow)."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_stock_limited_", ""))
    context.user_data["admin_data"]["prod_id"] = prod_id

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        prod_name = prod["name"] if prod else "Unknown"
        # Ensure product is NOT unlimited when adding limited stock
        if prod and prod.get("is_unlimited", 0):
            set_product_unlimited(conn, prod_id, False)
    finally:
        conn.close()

    context.user_data["admin_state"] = "stock_account"

    await query.edit_message_text(
        f"📥 <b>Add Limited Stock — {prod_name}</b>\n\n"
        f"<b>Option 1:</b> Type/paste accounts below\n"
        f"<b>Option 2:</b> Upload a .txt file with accounts\n\n"
        f"<i>Format: email:password</i>\n"
        f"<i>One account per line (Gmail, Outlook, etc.)</i>",
        parse_mode="HTML",
        reply_markup=_cancel_button("admin_stock_menu"),
    )


async def admin_stock_unlimited_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle unlimited stock mode for a product."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_stock_unlimited_", ""))

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        if not prod:
            await query.edit_message_text("❌ Product not found.", reply_markup=_back_button("admin_stock_menu"))
            return

        current = prod.get("is_unlimited", 0)
        new_val = not current
        set_product_unlimited(conn, prod_id, new_val)

        prod_name = prod["name"]
        stock = prod.get("stock_count", 0)
    finally:
        conn.close()

    if new_val:
        msg = (
            f"♾️ <b>Unlimited Stock ENABLED</b>\n\n"
            f"Product: {prod_name}\n"
            f"Users can now buy any quantity — no stock items needed.\n"
            f"Existing {stock} stock item(s) are preserved but won't be consumed."
        )
    else:
        msg = (
            f"📦 <b>Limited Stock Mode</b>\n\n"
            f"Product: {prod_name}\n"
            f"Unlimited mode disabled. Stock items are required for purchases.\n"
            f"Current stock: <b>{stock}</b> items."
        )

    keyboard = [
        [InlineKeyboardButton("➕ Add Limited Stock", callback_data=f"admin_stock_limited_{prod_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_stock_menu")],
    ]

    await query.edit_message_text(msg, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_view_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show products list for viewing stock."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if not products:
        await query.edit_message_text(
            "📋 No products yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_stock_menu")
            ]]),
        )
        return

    buttons = []
    for p in products:
        stock = p.get("stock_count", 0)
        is_unl = p.get("is_unlimited", 0)
        stock_disp = "Unlimited" if is_unl else f"Stock: {stock}"
        buttons.append([_cat_btn(
            f"{emoji_for_button(p['emoji'])} {p['name']} ({stock_disp})",
            f"admin_view_stock_{p['id']}", p['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_stock_menu")])

    await query.edit_message_text(
        "📋 <b>View Stock</b>\n\nSelect a product to view its stock:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_view_stock_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show stock details for a product."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_view_stock_", ""))
    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        is_unl = prod.get("is_unlimited", 0) if prod else 0
        if is_unl:
            stock_items = []
        else:
            stock_items = get_stock_for_product(conn, prod_id, unsold_only=True)
    finally:
        conn.close()

    if not prod:
        await query.edit_message_text("❌ Product not found.", reply_markup=_back_button("admin_stock_menu"))
        return

    if is_unl:
        text = (
            f"♾️ <b>Unlimited Stock</b>\n\n"
            f"📋 Product: {emoji_for_html(prod['emoji'])} {prod['name']}\n"
            f"💰 Price: ${prod['price']:.2f}\n"
            f"♾️ Stock: <b>Unlimited</b>\n\n"
            f"No individual stock items needed. Users can buy any quantity."
        )
    elif not stock_items:
        text = (
            f"📋 <b>Stock for:</b> {emoji_for_html(prod['emoji'])} {prod['name']}\n\n"
            f"💰 Price: ${prod['price']:.2f}\n"
            f"📦 Available: <b>0</b>\n\n"
            f"⚠️ No stock available."
        )
    else:
        lines = []
        for i, s in enumerate(stock_items, 1):
            detail = s["detail"]
            # Mask long details for display
            display = detail if len(detail) <= 40 else detail[:37] + "..."
            lines.append(f"{i}. <code>{display}</code>")

        text = (
            f"📋 <b>Stock for:</b> {emoji_for_html(prod['emoji'])} {prod['name']}\n\n"
            f"💰 Price: ${prod['price']:.2f}\n"
            f"📦 Available: <b>{len(stock_items)}</b>\n\n"
            + "\n".join(lines)
        )

    # Paginate if too many
    if len(text) > 4000:
        text = text[:4000] + "\n\n<i>... and more</i>"

    keyboard = [[InlineKeyboardButton("🗑 Clear Stock", callback_data=f"admin_clear_stock_{prod_id}")],
                [InlineKeyboardButton("🔙 Back", callback_data="admin_view_stock")]]

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_clear_stock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear all unsold stock for a product."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_clear_stock_", ""))
    conn = get_db()
    try:
        items = get_stock_for_product(conn, prod_id, unsold_only=True)
        for item in items:
            delete_stock(conn, item["id"])
    finally:
        conn.close()

    await query.edit_message_text(
        f"✅ Cleared {len(items)} stock items!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_view_stock")
        ]]),
    )


# ===========================================================================
# DASHBOARD
# ===========================================================================
async def admin_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show store statistics."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        stats = get_dashboard_stats(conn)
    finally:
        conn.close()

    text = (
        "📊 <b>Store Dashboard</b>\n\n"
        f"👥 Total Users: <b>{stats['total_users']}</b>\n"
        f"💰 Total Deposits: <b>${stats['total_deposits']:.2f}</b>\n"
        f"💳 User Balances: <b>${stats['total_balance']:.2f}</b>\n"
        f"📦 Products: <b>{stats['total_products']}</b>\n"
        f"📥 Available Stock: <b>{stats['total_stock']}</b>\n"
        f"\n── 📅 <b>Today</b> ──\n"
        f"🛒 Orders: <b>{stats['today_orders']}</b>\n"
        f"💵 Revenue: <b>${stats['today_revenue']:.2f}</b>\n"
        f"🏆 Top Product: <b>{stats['top_product']}</b>"
    )

    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            _styled_btn("🔄 Refresh", "admin_dashboard", "admin_dashboard"),
            _styled_btn("🔙 Back", "admin_panel", "admin_close"),
        ]]),
    )


# ===========================================================================
# PROMO CODES
# ===========================================================================
async def admin_promos(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all promo codes."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        promos = get_all_promo_codes(conn)
    finally:
        conn.close()

    if promos:
        lines = []
        for p in promos:
            d_type = "%" if p["discount_type"] == "percent" else "$"
            uses = f"{p['current_uses']}/{p['max_uses']}" if p["max_uses"] > 0 else f"{p['current_uses']}/∞"
            lines.append(
                f"🎟 <code>{p['code']}</code> — {d_type}{p['discount_value']} off "
                f"| Min: ${p['min_order']:.2f} | Uses: {uses}"
            )
        text = "🎟 <b>Promo Codes</b>\n\n" + "\n".join(lines)
    else:
        text = "🎟 <b>Promo Codes</b>\n\nNo promo codes yet."

    keyboard = [
        [_styled_btn("➕ Create Promo", "admin_add_promo", "admin_dashboard")],
    ]
    if promos:
        keyboard.append([_styled_btn("🗑 Delete Promo", "admin_del_promo", "admin_close")])
    keyboard.append([_styled_btn("🔙 Back", "admin_panel", "admin_close")])

    await query.edit_message_text(
        text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_add_promo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Start promo creation: ask for code name."""
    query = update.callback_query
    await query.answer()

    context.user_data["admin_state"] = "promo_code"
    context.user_data["admin_data"] = {}

    await query.edit_message_text(
        "🎟 <b>New Promo Code — Step 1/5</b>\n\n"
        "Please <b>type</b> the promo code:\n\n"
        "<i>Example: WELCOME10</i>",
        parse_mode="HTML",
        reply_markup=_cancel_button("admin_promos"),
    )


async def admin_del_promo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show promo list for deletion."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        promos = get_all_promo_codes(conn)
    finally:
        conn.close()

    if not promos:
        await query.edit_message_text(
            "🎟 No promo codes to delete.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_promos")
            ]]),
        )
        return

    buttons = []
    for p in promos:
        buttons.append([InlineKeyboardButton(
            f"🎟 {p['code']}",
            callback_data=f"admin_del_promo_{p['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_promos")])

    await query.edit_message_text(
        "🗑 <b>Delete Promo Code</b>\n\nSelect one:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def admin_del_promo_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    promo_id = int(query.data.replace("admin_del_promo_", ""))
    conn = get_db()
    try:
        delete_promo_code(conn, promo_id)
    finally:
        conn.close()

    await query.edit_message_text(
        "✅ Promo code deleted!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Promos", callback_data="admin_promos")
        ]]),
    )


# ===========================================================================
# EDIT CATEGORY
# ===========================================================================
async def admin_edit_category_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show category list for editing."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        cats = get_all_categories(conn)
    finally:
        conn.close()

    if not cats:
        await query.edit_message_text("📂 No categories to edit.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_categories")
            ]]))
        return

    buttons = []
    for c in cats:
        buttons.append([_cat_btn(
            f"{emoji_for_button(c['emoji'])} {c['name']}",
            f"admin_edit_cat_{c['id']}", c['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_categories")])

    await query.edit_message_text(
        "✏️ <b>Edit Category</b>\n\nSelect a category:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_edit_cat_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show edit options for selected category."""
    query = update.callback_query
    await query.answer()

    cat_id = int(query.data.replace("admin_edit_cat_", ""))
    conn = get_db()
    try:
        cat = get_all_categories(conn)
        cat = next((c for c in cat if c["id"] == cat_id), None)
    finally:
        conn.close()

    if not cat:
        await query.edit_message_text("❌ Category not found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_categories")
            ]]))
        return

    context.user_data["admin_data"] = {"edit_cat_id": cat_id}

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Name", callback_data=f"admin_editcat_name_{cat_id}")],
        [InlineKeyboardButton("🎨 Edit Emoji", callback_data=f"admin_editcat_emoji_{cat_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_edit_category")],
    ]

    await query.edit_message_text(
        f"✏️ <b>Edit:</b> {emoji_for_html(cat['emoji'])} {cat['name']}\n\nWhat do you want to change?",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_editcat_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "cat_edit_name"
    context.user_data["admin_data"]["edit_cat_id"] = int(query.data.replace("admin_editcat_name_", ""))
    await query.edit_message_text(
        "✏️ Send the <b>new name</b>:",
        parse_mode="HTML", reply_markup=_cancel_button("admin_categories")
    )


async def admin_editcat_emoji(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "cat_edit_emoji"
    context.user_data["admin_data"]["edit_cat_id"] = int(query.data.replace("admin_editcat_emoji_", ""))
    await query.edit_message_text(
        "🎨 Send the <b>new emoji</b>:",
        parse_mode="HTML", reply_markup=_cancel_button("admin_categories")
    )


# ===========================================================================
# EDIT PRODUCT
# ===========================================================================
async def admin_edit_product_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if not products:
        await query.edit_message_text("📦 No products to edit.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_products")
            ]]))
        return

    buttons = []
    for p in products:
        buttons.append([_cat_btn(
            f"{emoji_for_button(p['emoji'])} {p['name']} (${p['price']:.2f})",
            f"admin_edit_prod_{p['id']}", p['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_products")])

    await query.edit_message_text(
        "✏️ <b>Edit Product</b>\n\nSelect a product:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_edit_prod_choose(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_edit_prod_", ""))
    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        cats = get_all_categories(conn)
    finally:
        conn.close()

    if not prod:
        await query.edit_message_text("❌ Not found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_products")
            ]]))
        return

    context.user_data["admin_data"] = {"edit_prod_id": prod_id}

    cat = next((c for c in cats if c["id"] == prod["category_id"]), None)
    cat_name = cat["name"] if cat else "Unknown"

    text = (
        f"✏️ <b>Edit:</b> {emoji_for_html(prod['emoji'])} {prod['name']}\n"
        f"   💰 ${prod['price']:.2f} | 📂 {cat_name}\n\n"
        f"Select field to edit:"
    )

    keyboard = [
        [InlineKeyboardButton("📛 Name", callback_data=f"admin_ep_name_{prod_id}")],
        [InlineKeyboardButton("💰 Price", callback_data=f"admin_ep_price_{prod_id}")],
        [InlineKeyboardButton("🎨 Emoji", callback_data=f"admin_ep_emoji_{prod_id}")],
        [InlineKeyboardButton("📝 Description", callback_data=f"admin_ep_desc_{prod_id}")],
        [InlineKeyboardButton("📂 Category", callback_data=f"admin_ep_cat_{prod_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_edit_product")],
    ]

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_edit_prod_field(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data  # "admin_ep_FIELD_PRODID"
    parts = data.split("_")
    field = parts[2]
    prod_id = int(parts[3])

    context.user_data["admin_data"]["edit_prod_id"] = prod_id
    context.user_data["admin_data"]["edit_prod_field"] = field
    context.user_data["admin_state"] = "prod_edit_value"

    prompts = {
        "name": "Send the <b>new name</b>:",
        "price": "Send the <b>new price</b> (number only):",
        "emoji": "Send the <b>new emoji</b>:",
        "desc": "Send the <b>new description</b> (supports formatting + premium emoji):\n<i>Use Ctrl+B, Ctrl+I, Ctrl+U for formatting</i>",
        "cat": "Send the <b>new category ID</b>:",
    }
    prompt = prompts.get(field, "Send the new value:")

    if field == "cat":
        conn = get_db()
        try:
            cats = get_all_categories(conn)
        finally:
            conn.close()
        lines = "\n".join(f"  {c['id']}: {emoji_for_button(c['emoji'])} {c['name']}" for c in cats)
        prompt += f"\n\nAvailable categories:\n{lines}"

    await query.edit_message_text(prompt, parse_mode="HTML", reply_markup=_cancel_button("admin_products"))


# ===========================================================================
# EDIT STOCK
# ===========================================================================
async def admin_edit_stock_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show stock edit options."""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Single Stock", callback_data="admin_editstock_single")],
        [InlineKeyboardButton("🔄 Replace All Stock", callback_data="admin_editstock_replace")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_stock_menu")],
    ]

    await query.edit_message_text(
        "📥 <b>Edit Stock</b>\n\nChoose action:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_editstock_single(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Select product then select individual stock item to edit."""
    query = update.callback_query
    await query.answer()
    await _show_product_picker(query, "admin_es_prod_", "admin_edit_stock_menu",
        "✏️ <b>Edit Single Stock — Step 1/2</b>\n\nSelect a product:")


async def admin_editstock_prod_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show individual stock items for editing."""
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_es_prod_", ""))
    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        items = get_stock_for_product(conn, prod_id, unsold_only=True)
    finally:
        conn.close()

    if not items:
        await query.edit_message_text("📥 No unsold stock items.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_editstock_single")
            ]]))
        return

    buttons = []
    for item in items:
        detail = item["detail"]
        display = detail if len(detail) <= 25 else detail[:22] + "..."
        buttons.append([InlineKeyboardButton(
            f"#{item['id']}: {display}",
            callback_data=f"admin_es_item_{item['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_editstock_single")])

    await query.edit_message_text(
        f"✏️ <b>Edit Stock — Step 2/2</b>\n\nProduct: {emoji_for_html(prod['emoji'])} {prod['name']}\nSelect item to edit or delete:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_editstock_item_actions(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show actions for a single stock item."""
    query = update.callback_query
    await query.answer()

    stock_id = int(query.data.replace("admin_es_item_", ""))
    conn = get_db()
    try:
        item = get_stock_item(conn, stock_id)
    finally:
        conn.close()

    if not item:
        await query.answer("Not found")
        return

    detail = item["detail"]
    display = detail if len(detail) <= 50 else detail[:47] + "..."

    keyboard = [
        [InlineKeyboardButton("✏️ Edit Detail", callback_data=f"admin_es_edit_{stock_id}")],
        [InlineKeyboardButton("🗑 Delete", callback_data=f"admin_es_del_{stock_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"admin_es_prod_{item['product_id']}")],
    ]

    await query.edit_message_text(
        f"📥 <b>Stock #{stock_id}</b>\n\n<code>{display}</code>\n\nChoose action:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_editstock_edit_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    stock_id = int(query.data.replace("admin_es_edit_", ""))
    context.user_data["admin_state"] = "stock_edit_detail"
    context.user_data["admin_data"] = {"stock_id": stock_id}

    await query.edit_message_text(
        "✏️ Send the <b>new detail</b> for this stock item:",
        parse_mode="HTML", reply_markup=_cancel_button("admin_editstock_single")
    )


async def admin_editstock_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    stock_id = int(query.data.replace("admin_es_del_", ""))
    conn = get_db()
    try:
        item = get_stock_item(conn, stock_id)
        prod_id = item["product_id"] if item else 0
        delete_stock(conn, stock_id)
    finally:
        conn.close()

    await query.edit_message_text(
        "✅ Stock item deleted!",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data=f"admin_es_prod_{prod_id}")
        ]])
    )


async def admin_editstock_replace(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Select product for stock replacement."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()

    if not products:
        await query.edit_message_text("❌ No products.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_stock_menu")
            ]]))
        return

    buttons = []
    for p in products:
        buttons.append([_cat_btn(
            f"{emoji_for_button(p['emoji'])} {p['name']} (Stock: {p.get('stock_count', 0)})",
            f"admin_es_replace_{p['id']}", p['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_edit_stock_menu")])

    await query.edit_message_text(
        "🔄 <b>Replace All Stock</b>\n\n⚠️ This deletes ALL unsold stock and adds new ones.\nSelect product:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_editstock_replace_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    prod_id = int(query.data.replace("admin_es_replace_", ""))
    context.user_data["admin_state"] = "stock_replace"
    context.user_data["admin_data"] = {"prod_id": prod_id}

    conn = get_db()
    try:
        prod = get_product(conn, prod_id)
        count = len(get_stock_for_product(conn, prod_id, unsold_only=True))
    finally:
        conn.close()

    await query.edit_message_text(
        f"🔄 <b>Replace Stock</b>\n\n"
        f"Product: {emoji_for_html(prod['emoji'])} {prod['name']}\n"
        f"Current unsold: <b>{count}</b>\n\n"
        f"⚠️ All existing unsold items will be <b>deleted</b>.\n"
        f"<b>Option 1:</b> Send accounts as text (one per line)\n"
        f"<b>Option 2:</b> Upload a .txt file",
        parse_mode="HTML", reply_markup=_cancel_button("admin_stock_menu")
    )


# ===========================================================================
# USER MANAGEMENT
# ===========================================================================
async def admin_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """User management hub — stats, search, browse all users."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        all_users = get_all_users(conn, include_banned=True)
        active_users = get_all_users(conn, include_banned=False)
        # Count VIP users
        vip_count = sum(1 for u in all_users if u.get("vip_tier", "regular") != "regular")
        # Total balances
        total_balance = sum(float(u.get("balance", 0)) for u in all_users)
    finally:
        conn.close()

    total = len(all_users)
    active = len(active_users)
    banned = total - active

    text = (
        f"👥 <b>User Management</b>\n\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👤 <b>Total Users: {total}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"✅ Active: <b>{active}</b>\n"
        f"🚫 Banned: <b>{banned}</b>\n"
        f"⭐ VIP Users: <b>{vip_count}</b>\n"
        f"💰 Total Balances: <b>${total_balance:.2f}</b>\n"
        f"\n📋 <i>Use the buttons below to manage users.</i>"
    )

    keyboard = [
        [_styled_btn(f"📋 View All Users ({total})", "admin_users_enhanced_0", "admin_users_btn")],
        [_styled_btn("🔍 Search by ID", "admin_search_user", "admin_users_btn")],
        [_styled_btn("⭐ VIP Management", "admin_users_enhanced_0", "admin_users_btn")],
        [_styled_btn("🔙 Back", "admin_panel", "admin_close")],
    ]

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_list_users(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Redirect to admin_users."""
    query = update.callback_query
    query.data = "admin_users"
    await admin_users(update, context)


async def admin_search_user_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "user_search"

    await query.edit_message_text(
        "🔍 Send the <b>User ID</b> to search:",
        parse_mode="HTML", reply_markup=_cancel_button("admin_users")
    )


async def admin_user_detail(update: Update, context: ContextTypes.DEFAULT_TYPE,
                             user_id: int = None) -> None:
    """Show user detail with actions."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        if not user_id:
            user_id = int(query.data.replace("admin_user_detail_", ""))
        msg_target = query.edit_message_text
    else:
        msg_target = update.message.reply_html
        query = None

    conn = get_db()
    try:
        u = get_user_by_id(conn, user_id)
        orders = get_user_orders(conn, user_id, limit=5)
        order_count = len(get_user_orders(conn, user_id, limit=100))
    finally:
        conn.close()

    if not u:
        if msg_target:
            await msg_target("❌ User not found.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_users")
                ]]))
        return

    banned = u.get("is_banned", 0)
    status = "🚫 BANNED" if banned else "✅ Active"
    name = u.get("first_name") or "N/A"
    username = f"@{u['username']}" if u.get("username") else "N/A"
    vip = u.get("vip_tier", "regular")
    discount = u.get("discount_percent", 0)

    text = (
        f"👤 <b>User #{u['user_id']}</b>\n\n"
        f"📛 Name: {name}\n"
        f"🔗 Username: {username}\n"
        f"💰 Balance: <b>${u['balance']:.2f}</b>\n"
        f"⭐ VIP: <b>{vip.upper()}</b> ({discount}% discount)\n"
        f"📊 Status: {status}\n"
        f"🛒 Orders: <b>{order_count}</b>\n"
        f"📅 Joined: {u.get('created_at', 'N/A')[:16]}\n"
    )

    if orders:
        text += "\n<b>Recent Orders:</b>\n"
        for o in orders[:5]:
            text += f"  {emoji_for_html(o.get('product_emoji','📦'))} {o.get('product_name','?')} — ${o['amount']:.2f}\n"

    keyboard = [
        [_styled_btn("💰 Add Balance", f"admin_addbal_{user_id}", "admin_dashboard"),
         _styled_btn("💸 Deduct", f"admin_deduct_{user_id}", "admin_close")],
        [_styled_btn("⭐ Set VIP", f"admin_vip_set_{user_id}", "admin_products"),
         _styled_btn("📋 Full Orders", f"admin_user_orders_{user_id}", "admin_orders")],
    ]
    if banned:
        keyboard.append([_styled_btn("✅ Unban User", f"admin_unban_{user_id}", "admin_dashboard")])
    else:
        keyboard.append([_styled_btn("🚫 Ban User", f"admin_ban_{user_id}", "admin_close")])

    keyboard.append([
        _styled_btn("📩 Send DM", f"admin_dm_{user_id}", "admin_broadcast"),
        _styled_btn("🔙 Back", "admin_users", "admin_close"),
    ])

    if query:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_ban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.replace("admin_ban_", ""))
    conn = get_db()
    try:
        ban_user(conn, user_id)
    finally:
        conn.close()
    await admin_user_detail(update, context, user_id=user_id)


async def admin_unban_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.replace("admin_unban_", ""))
    conn = get_db()
    try:
        unban_user(conn, user_id)
    finally:
        conn.close()
    await admin_user_detail(update, context, user_id=user_id)


async def admin_addbal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.replace("admin_addbal_", ""))
    context.user_data["admin_state"] = "user_addbal"
    context.user_data["admin_data"] = {"user_id": user_id}
    await query.edit_message_text(
        "💰 Send the <b>amount</b> to add:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=f"admin_user_detail_{user_id}")
        ]])
    )


async def admin_deduct_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.replace("admin_deduct_", ""))
    context.user_data["admin_state"] = "user_deduct"
    context.user_data["admin_data"] = {"user_id": user_id}
    await query.edit_message_text(
        "💸 Send the <b>amount</b> to deduct:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=f"admin_user_detail_{user_id}")
        ]])
    )


async def admin_user_orders(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.replace("admin_user_orders_", ""))

    conn = get_db()
    try:
        u = get_user_by_id(conn, user_id)
        orders = get_user_orders(conn, user_id, limit=50)
    finally:
        conn.close()

    name = u.get("first_name") or f"ID:{user_id}" if u else f"ID:{user_id}"

    if not orders:
        await query.edit_message_text(
            f"📋 No orders for {name}.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_detail_{user_id}")
            ]]))
        return

    lines = [f"📋 <b>Orders for {name}</b>\n"]
    for o in orders:
        lines.append(
            f"{emoji_for_html(o.get('product_emoji','📦'))} {o.get('product_name','?')} | "
            f"${o['amount']:.2f} | {o['created_at'][:16]}"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n\n..."

    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_detail_{user_id}")
        ]])
    )


async def admin_dm_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.replace("admin_dm_", ""))
    context.user_data["admin_state"] = "user_dm"
    context.user_data["admin_data"] = {"user_id": user_id}
    await query.edit_message_text(
        "📩 Send the <b>message</b> to deliver to this user:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data=f"admin_user_detail_{user_id}")
        ]])
    )


# ===========================================================================
# REPORTS
# ===========================================================================
async def admin_reports(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("📊 Sales Report", callback_data="report_sales")],
        [InlineKeyboardButton("🏆 Best Sellers", callback_data="report_bestsellers")],
        [InlineKeyboardButton("💵 Revenue Breakdown", callback_data="report_revenue")],
        [InlineKeyboardButton("📦 Stock Sold", callback_data="report_stock")],
        [InlineKeyboardButton("📈 User Growth", callback_data="report_growth")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        "📈 <b>Reports</b>\n\nSelect a report:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def report_sales(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        daily = get_sales_report(conn, "daily")[:7]
        weekly = get_sales_report(conn, "weekly")[:4]
        monthly = get_sales_report(conn, "monthly")[:6]
    finally:
        conn.close()

    text = "📊 <b>Sales Report</b>\n\n"
    text += "<b>── Daily (last 7 days) ──</b>\n"
    for d in daily:
        text += f"  {d['period']}: {d['order_count']} orders | ${d['revenue']:.2f}\n"
    text += "\n<b>── Weekly ──</b>\n"
    for w in weekly:
        text += f"  Week {w['period']}: {w['order_count']} orders | ${w['revenue']:.2f}\n"
    text += "\n<b>── Monthly ──</b>\n"
    for m in monthly:
        text += f"  {m['period']}: {m['order_count']} orders | ${m['revenue']:.2f}\n"

    if len(text) > 4000:
        text = text[:4000] + "\n..."

    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
        ]])
    )


async def report_bestsellers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        bs = get_best_sellers(conn)
    finally:
        conn.close()

    if not bs:
        await query.edit_message_text("🏆 No sales data yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
            ]]))
        return

    lines = ["🏆 <b>Best Selling Products</b>\n"]
    for i, p in enumerate(bs, 1):
        lines.append(
            f"{i}. {emoji_for_html(p['emoji'])} <b>{p['name']}</b>\n"
            f"   Sold: {p['sold']} | Revenue: ${p['revenue']:.2f}"
        )

    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
        ]])
    )


async def report_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        rev = get_revenue_by_payment(conn)
    finally:
        conn.close()

    text = (
        "💵 <b>Revenue Breakdown</b>\n\n"
        f"🏦 Bakong Deposits: <b>${rev['bakong_deposits']:.2f}</b>\n"
        f"🛒 Order Revenue: <b>${rev['order_revenue']:.2f}</b>\n"
        f"💳 Active Balances: <b>${rev['user_balances']:.2f}</b>"
    )

    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
        ]])
    )


async def report_stock_sold(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        data = get_stock_sold_report(conn)
    finally:
        conn.close()

    if not data:
        await query.edit_message_text("📦 No stock data.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
            ]]))
        return

    lines = ["📦 <b>Stock Sold Report</b>\n"]
    for s in data:
        pct = (s['sold'] / s['total_stock'] * 100) if s['total_stock'] > 0 else 0
        lines.append(
            f"{emoji_for_html(s['emoji'])} <b>{s['name']}</b>\n"
            f"   Total: {s['total_stock']} | Sold: {s['sold']} | "
            f"Left: {s['remaining']} | {pct:.0f}% sold"
        )

    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
        ]])
    )


async def report_user_growth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        growth = get_user_growth(conn, 30)
        total = len(get_all_users(conn, include_banned=True))
    finally:
        conn.close()

    lines = [f"📈 <b>User Growth</b> (Last 30 Days)\n\nTotal Users: <b>{total}</b>\n"]
    for g in growth:
        lines.append(f"  {g['date']}: +{g['new_users']}")

    await query.edit_message_text(
        "\n".join(lines), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_reports")
        ]])
    )


# ===========================================================================
# Helper: product picker
# ===========================================================================
async def _show_product_picker(query, prefix: str, back_target: str, title: str) -> None:
    conn = get_db()
    try:
        products = get_all_products(conn)
    finally:
        conn.close()
    if not products:
        await query.edit_message_text("❌ No products.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=back_target)
            ]]))
        return
    buttons = []
    for p in products:
        buttons.append([_cat_btn(
            f"{emoji_for_button(p['emoji'])} {p['name']} (Stock: {p.get('stock_count', 0)})",
            f"{prefix}{p['id']}", p['emoji']
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=back_target)])
    await query.edit_message_text(title, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(buttons))


# ===========================================================================
# CUSTOMIZE (Emoji Manager)
# ===========================================================================
async def admin_customize(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    keyboard = [
        [_styled_btn("🎨 Set Emoji", "custom_set_emoji", "admin_dashboard")],
        [_styled_btn("🔄 Reset All Emojis", "custom_reset_all", "admin_close")],
        [_styled_btn("🔙 Back", "admin_panel", "admin_close")],
    ]

    await query.edit_message_text(
        "🎨 <b>Customize Bot</b>\n\n"
        "Change emojis used throughout the bot.\n"
        "Tap <b>Set Emoji</b> to pick a section and customize its emoji.\n\n"
        "<i>Send a premium animated emoji from Telegram's sticker panel to use custom emojis on buttons.</i>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def custom_reset_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show confirmation before resetting all emojis."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "⚠️ <b>Reset All Emojis?</b>\n\n"
        "This will restore ALL emojis and button icons to their defaults.\n"
        "This action <b>cannot be undone</b>.\n\n"
        "<i>Are you sure?</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✅ Confirm Reset", callback_data="custom_reset_all_confirm"),
                InlineKeyboardButton("❌ Cancel", callback_data="admin_customize"),
            ]
        ])
    )


async def custom_reset_all_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute the actual reset after confirmation."""
    query = update.callback_query
    await query.answer()

    from utils.emoji_manager import reset_all, load_button_config, save_button_config
    reset_all()
    try:
        btn_cfg = load_button_config()
        for k in btn_cfg:
            btn_cfg[k]["icon_custom_emoji_id"] = None
        save_button_config(btn_cfg)
    except Exception as e:
        logger.error(f"Error resetting custom emoji IDs in button config: {e}")

    await query.edit_message_text(
        "✅ All emojis reset to defaults!",
        reply_markup=InlineKeyboardMarkup([[
            _styled_btn("🔙 Back", "admin_customize", "admin_close")
        ]])
    )


async def custom_sections(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all sections in a grid."""
    query = update.callback_query
    await query.answer()

    all_emojis = get_all()
    section_names = list(SECTIONS.keys())
    known = {k for keys in SECTIONS.values() for k in keys}
    extras = [k for k in all_emojis.keys() if k not in known]
    if extras and "🧩 Other" not in section_names:
        section_names.append("🧩 Other")

    buttons = []
    row = []
    for name in section_names:
        row.append(_styled_btn(name, f"custom_sec_{name}", "admin_dashboard"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([_styled_btn("🔙 Back", "admin_customize", "admin_close")])

    await query.edit_message_text(
        "🎨 <b>Set Emoji</b>\n\nSelect a section to customize its emojis:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def custom_section_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all emoji keys in a section as 3-per-row buttons."""
    query = update.callback_query
    await query.answer()

    section_name = query.data.replace("custom_sec_", "")
    emojis = get_all()
    if section_name == "🧩 Other":
        known = {k for ks in SECTIONS.values() for k in ks}
        keys = [k for k in emojis.keys() if k not in known]
    else:
        keys = SECTIONS.get(section_name, [])

    if not keys:
        await query.answer("No emojis in this section")
        return

    buttons = []
    row = []
    for key in keys:
        premium_id = get_premium_id(key)
        label = LABELS.get(key, key)
        if premium_id:
            # Premium only — no old unicode next to the new icon
            btn_text = strip_leading_emoji(label)[:18] or label[:18]
            btn_kwargs = {
                "text": btn_text,
                "callback_data": f"custom_emoji_{key}",
                "icon_custom_emoji_id": str(premium_id),
            }
        else:
            current = get_plain(key)
            btn_kwargs = {
                "text": f"{current} {label[:12]}".strip(),
                "callback_data": f"custom_emoji_{key}",
            }
        try:
            row.append(InlineKeyboardButton(**btn_kwargs))
        except TypeError:
            row.append(InlineKeyboardButton(
                text=btn_kwargs["text"], callback_data=btn_kwargs["callback_data"]
            ))
        if len(row) == 3:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([_styled_btn("🔙 Back", "custom_set_emoji", "admin_close")])

    await query.edit_message_text(
        f"🎨 <b>{section_name}</b>\n\nTap an emoji to customize:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def custom_emoji_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show current emoji only (new premium). Set / reset / back."""
    query = update.callback_query
    await query.answer()

    key = query.data.replace("custom_emoji_", "")
    current = eget(key)  # HTML <tg-emoji> for premium, plain otherwise
    label = LABELS.get(key, key)
    premium_id = get_premium_id(key)

    # Show only the CURRENT emoji (never old default + new side by side)
    if premium_id:
        text = (
            f"🎨 <b>{label}</b>\n\n"
            f"Current: {current}\n"
            f"<i>Premium emoji is active on buttons.</i>\n\n"
            f"Send a new premium emoji via <b>Set Emoji</b> to replace it.\n"
            f"Or <b>Reset</b> to the default unicode."
        )
    else:
        text = (
            f"🎨 <b>{label}</b>\n\n"
            f"Current: {current}\n\n"
            f"Tap <b>Set Emoji</b> and send a premium or normal emoji.\n"
            f"<i>Only the new emoji will show — old one is replaced.</i>"
        )

    keyboard = [
        [InlineKeyboardButton("✏️ Set Emoji", callback_data=f"custom_setval_{key}")],
        [InlineKeyboardButton("🔄 Reset to Default", callback_data=f"custom_reset_{key}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"custom_sec_{_find_section(key)}")],
    ]

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


def _find_section(key: str) -> str:
    for sec_name, keys in SECTIONS.items():
        if key in keys:
            return sec_name
    return "🧩 Other"


async def custom_set_value_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to send a new emoji (plain unicode or premium custom emoji)."""
    query = update.callback_query
    await query.answer()

    key = query.data.replace("custom_setval_", "").replace("custom_update_", "")
    context.user_data["admin_state"] = "custom_setval"
    context.user_data["admin_data"] = {"emoji_key": key}

    label = LABELS.get(key, key)

    await query.edit_message_text(
        f"✏️ <b>Set emoji for:</b> {label}\n\n"
        f"<b>Send the new emoji now</b> (it will replace the old one completely):\n\n"
        f"<b>Premium animated emoji:</b>\n"
        f"  • Open Telegram emoji panel\n"
        f"  • Tap a premium / animated emoji\n"
        f"  • Send it alone in this chat\n\n"
        f"<b>Normal emoji:</b>\n"
        f"  • Type one emoji character\n\n"
        f"<i>Send ONLY the emoji — no extra text.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            _styled_btn("❌ Cancel", f"custom_emoji_{key}", "admin_close")
        ]])
    )


async def custom_reset_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset an emoji key to its default and clear premium icon from button config."""
    query = update.callback_query
    await query.answer()

    key = query.data.replace("custom_reset_", "")
    reset(key)
    # Clear premium icon for this key on all button configs
    try:
        from utils.emoji_manager import load_button_config, save_button_config, reload, reload_button_cache
        btn_cfg = load_button_config()
        if key in btn_cfg and isinstance(btn_cfg[key], dict):
            btn_cfg[key]["icon_custom_emoji_id"] = None
            save_button_config(btn_cfg)
        reload()
        reload_button_cache()
    except Exception as e:
        logger.error(f"Error resetting custom emoji ID in button config for {key}: {e}")

    current = eget(key)
    label = LABELS.get(key, key)

    await query.edit_message_text(
        f"✅ Reset <b>{label}</b> to default: {current}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data=f"custom_emoji_{key}")
        ]])
    )




# ===========================================================================
# BROADCASTING
# ===========================================================================
async def admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show broadcast menu."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        from services.database import get_all_users
        total = len(get_all_users(conn, include_banned=False))
    finally:
        conn.close()

    keyboard = [
        [InlineKeyboardButton("📢 Send Broadcast", callback_data="admin_broadcast_start")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        f"📢 <b>Broadcasting</b>\n\n"
        f"Send a message to all <b>{total}</b> active users.\n\n"
        f"Tap <b>Send Broadcast</b> to compose your message.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to send broadcast (text, photo, video, voice, document, GIF)."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "broadcast"
    context.user_data["admin_data"] = {}

    await query.edit_message_text(
        "📢 <b>Compose Broadcast</b>\n\n"
        "Send the message or media you want to broadcast.\n\n"
        "📝 <b>Text</b> — with HTML formatting + premium emojis\n"
        "🖼 <b>Photo</b> — with caption\n"
        "🎬 <b>Video</b> — with caption\n"
        "🎤 <b>Voice</b> — audio message\n"
        "📄 <b>Document / GIF</b> — with caption\n\n"
        "<i>This will be sent to ALL active users.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="admin_broadcast")
        ]])
    )


# ===========================================================================
# ORDER MANAGEMENT
# ===========================================================================
async def admin_orders_mgmt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show order management menu with status filters."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        all_orders = get_all_orders(conn, limit=1000)
        pending = sum(1 for o in all_orders if o["status"] == "pending")
        completed = sum(1 for o in all_orders if o["status"] == "completed")
        refunded = sum(1 for o in all_orders if o["status"] == "refunded")
        total = len(all_orders)
    finally:
        conn.close()

    keyboard = [
        [InlineKeyboardButton(f"📋 All Orders ({total})", callback_data="admin_orders_list_all")],
        [InlineKeyboardButton(f"⏳ Pending ({pending})", callback_data="admin_orders_list_pending"),
         InlineKeyboardButton(f"✅ Completed ({completed})", callback_data="admin_orders_list_completed")],
        [InlineKeyboardButton(f"↩️ Refunded ({refunded})", callback_data="admin_orders_list_refunded")],
        [InlineKeyboardButton("🔍 Search Order", callback_data="admin_order_search")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        f"🛒 <b>Order Management</b>\n\n"
        f"Total: <b>{total}</b> | Pending: <b>{pending}</b> | "
        f"Completed: <b>{completed}</b> | Refunded: <b>{refunded}</b>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_orders_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List orders filtered by status."""
    query = update.callback_query
    await query.answer()

    status_filter = query.data.replace("admin_orders_list_", "")
    if status_filter == "all":
        status_filter = None

    conn = get_db()
    try:
        orders = get_all_orders(conn, status=status_filter, limit=30)
    finally:
        conn.close()

    if not orders:
        await query.edit_message_text(
            "📋 No orders found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")
            ]])
        )
        return

    buttons = []
    for o in orders:
        status_icon = {"pending": "⏳", "completed": "✅", "refunded": "↩️"}.get(o["status"], "❓")
        name = o.get("first_name") or f"ID:{o['user_id']}"
        pname = o.get("product_name") or "Unknown"
        buttons.append([InlineKeyboardButton(
            f"{status_icon} #{o['id']} | {name} | {pname} | ${o['amount']:.2f}",
            callback_data=f"admin_order_detail_{o['id']}"
        )])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")])

    label = status_filter.capitalize() if status_filter else "All"
    await query.edit_message_text(
        f"🛒 <b>{label} Orders</b>\n\nTap an order for details:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_order_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show order detail with refund option."""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("admin_order_detail_", ""))
    conn = get_db()
    try:
        orders = get_all_orders(conn, limit=1000)
        order = next((o for o in orders if o["id"] == order_id), None)
    finally:
        conn.close()

    if not order:
        await query.edit_message_text("❌ Order not found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")
            ]]))
        return

    status_icon = {"pending": "⏳", "completed": "✅", "refunded": "↩️"}.get(order["status"], "❓")
    name = order.get("first_name") or "N/A"
    username = f"@{order.get('user_username')}" if order.get("user_username") else "N/A"
    pname = order.get("product_name") or "Unknown"
    detail = order.get("stock_detail", "")

    text = (
        f"🛒 <b>Order #{order['id']}</b>\n\n"
        f"{status_icon} Status: <b>{order['status'].upper()}</b>\n"
        f"👤 User: {name} ({username}) | ID: <code>{order['user_id']}</code>\n"
        f"📦 Product: {emoji_for_html(order.get('product_emoji','📦'))} <b>{pname}</b>\n"
        f"💰 Amount: <b>${order['amount']:.2f}</b>\n"
        f"📅 Date: {order['created_at'][:16]}\n"
    )
    if detail:
        text += f"🔑 Detail: <code>{detail[:100]}</code>\n"
    if order.get("promo_code"):
        text += f"🎟 Promo: <code>{order['promo_code']}</code>\n"

    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")]]
    if order["status"] == "completed":
        keyboard.insert(0, [InlineKeyboardButton(
            "↩️ Refund Order", callback_data=f"admin_order_refund_{order_id}"
        )])

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


async def admin_order_refund(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Refund an order."""
    query = update.callback_query
    await query.answer()

    order_id = int(query.data.replace("admin_order_refund_", ""))
    conn = get_db()
    try:
        from services.database import refund_order as do_refund
        result = do_refund(conn, order_id)
    finally:
        conn.close()

    if not result:
        await query.edit_message_text("❌ Order not found or already refunded.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")
            ]]))
        return

    await query.edit_message_text(
        f"↩️ <b>Order #{order_id} Refunded!</b>\n\n"
        f"💰 ${result['amount']:.2f} credited back to user #{result['user_id']}.\n"
        f"💳 New balance: <b>${result['new_balance']:.2f}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Orders", callback_data="admin_orders_mgmt")
        ]])
    )


async def admin_order_search_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to search orders."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "order_search"

    await query.edit_message_text(
        "🔍 <b>Search Orders</b>\n\n"
        "Send a <b>User ID</b> or <b>username</b> to find their orders:",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="admin_orders_mgmt")
        ]])
    )


# ===========================================================================
# PAYMENT MANAGEMENT
# ===========================================================================
async def admin_payments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show payment management menu with status filters."""
    query = update.callback_query
    await query.answer()

    try:
        from services.database import _get_supabase
        s = _get_supabase()
        r = s.table('payments').select('*', count='exact').execute()
        total = r.count or 0
        r_paid = s.table('payments').select('*', count='exact').eq('status', 'paid').execute()
        paid = r_paid.count or 0
        r_pending = s.table('payments').select('*', count='exact').eq('status', 'pending').execute()
        pending = r_pending.count or 0
        r_expired = s.table('payments').select('*', count='exact').eq('status', 'expired').execute()
        expired = r_expired.count or 0
    except Exception:
        total = paid = pending = expired = 0

    keyboard = [
        [InlineKeyboardButton(f"📋 All Payments ({total})", callback_data="admin_pay_list_all")],
        [InlineKeyboardButton(f"✅ Paid ({paid})", callback_data="admin_pay_list_paid"),
         InlineKeyboardButton(f"⏳ Pending ({pending})", callback_data="admin_pay_list_pending")],
        [InlineKeyboardButton(f"⏰ Expired ({expired})", callback_data="admin_pay_list_expired")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        f"💵 <b>Payment Management</b>\n\n"
        f"Total: <b>{total}</b> | Paid: <b>{paid}</b> | "
        f"Pending: <b>{pending}</b> | Expired: <b>{expired}</b>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_pay_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List payments filtered by status."""
    query = update.callback_query
    await query.answer()

    status_filter = query.data.replace("admin_pay_list_", "")
    if status_filter == "all":
        status_filter = None

    try:
        from services.database import _get_supabase
        s = _get_supabase()
        q = s.table('payments').select('*').order('id', desc=True).limit(30)
        if status_filter:
            q = q.eq('status', status_filter)
        r = q.execute()
        payments = r.data or []
    except Exception:
        payments = []

    if not payments:
        await query.edit_message_text(
            "💵 No payments found.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_payments")
            ]])
        )
        return

    lines = [f"💵 <b>Payments</b> ({len(payments)})\n"]
    lines.append(f"<pre>{'ID':<6} {'User':<14} {'Amount':<8} {'Status':<10}</pre>")

    for p in payments[:25]:
        uid = str(p.get('user_id', ''))
        amt = p.get('amount', 0)
        status = p.get('status', '?')
        status_icon = {"paid": "✅", "pending": "⏳", "expired": "⏰"}.get(status, "❓")
        lines.append(
            f"<pre>#{p['id']:<5} {uid:<14} ${amt:<7.2f} {status_icon} {status:<8}</pre>"
        )

    text = "\n".join(lines)
    if len(text) > 4000:
        text = text[:4000] + "\n..."

    await query.edit_message_text(
        text, parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_payments")
        ]])
    )


# ===========================================================================
# ENHANCED USER MANAGEMENT
# ===========================================================================
async def admin_users_enhanced(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show paginated user list with stats (15 per page)."""
    query = update.callback_query
    await query.answer()

    # Parse page from callback: "admin_users_enhanced_0"
    data = query.data or "admin_users_enhanced_0"
    try:
        page = int(data.replace("admin_users_enhanced_", "").split("_")[0])
    except (ValueError, IndexError):
        page = 0

    per_page = 15

    conn = get_db()
    try:
        all_users_list = get_users_with_stats(conn, limit=500)
    finally:
        conn.close()

    total_users = len(all_users_list)
    total_pages = max(1, (total_users + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))

    start = page * per_page
    end = start + per_page
    page_users = all_users_list[start:end]

    if not page_users:
        await query.edit_message_text("👥 No users found.",
            reply_markup=InlineKeyboardMarkup([[
                _styled_btn("🔙 Back", "admin_users", "admin_close")
            ]]))
        return

    buttons = []
    for u in page_users:
        ban_icon = "🚫" if u.get("is_banned") else "✅"
        vip = f" [{u.get('vip_tier','regular')}]" if u.get("vip_tier") != "regular" else ""
        name = u.get("first_name") or f"ID:{u['user_id']}"
        buttons.append([InlineKeyboardButton(
            f"{ban_icon} {name}{vip} | 💰${u['balance']:.2f} | 🛒{u['order_count']}",
            callback_data=f"admin_user_detail_{u['user_id']}"
        )])

    # Pagination row
    nav_row = []
    if page > 0:
        nav_row.append(_styled_btn("⬅️ Prev", f"admin_users_enhanced_{page - 1}", "admin_close"))
    nav_row.append(InlineKeyboardButton(
        f"📄 {page + 1}/{total_pages}", callback_data="admin_users_enhanced_same"
    ))
    if page < total_pages - 1:
        nav_row.append(_styled_btn("Next ➡️", f"admin_users_enhanced_{page + 1}", "admin_close"))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([_styled_btn("🔙 Back", "admin_users", "admin_close")])

    await query.edit_message_text(
        f"👥 <b>All Users</b> ({total_users} total)\n"
        f"Page {page + 1} of {total_pages} — sorted by spending\n\n"
        f"<i>Tap a user for details & actions:</i>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_user_vip_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Set VIP tier for a user."""
    query = update.callback_query
    await query.answer()

    user_id = int(query.data.replace("admin_vip_set_", ""))
    tiers = ["regular", "bronze", "silver", "gold", "diamond"]
    discount_map = {"regular": 0, "bronze": 5, "silver": 10, "gold": 15, "diamond": 25}

    buttons = []
    for tier in tiers:
        d = discount_map[tier]
        label = f"{tier.upper()} ({d}% off)" if d > 0 else tier.upper()
        buttons.append([InlineKeyboardButton(
            label, callback_data=f"admin_discount_set_{user_id}_{tier}_{d}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data=f"admin_user_detail_{user_id}")])

    conn = get_db()
    try:
        u = get_user_by_id(conn, user_id)
        current = u.get("vip_tier", "regular") if u else "regular"
    finally:
        conn.close()

    await query.edit_message_text(
        f"⭐ <b>Set VIP Tier</b>\n\n"
        f"User: {u.get('first_name','N/A') if u else 'N/A'}\n"
        f"Current: <b>{current.upper()}</b>\n\n"
        f"Select new tier:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_user_discount_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply VIP tier and discount to user."""
    query = update.callback_query
    await query.answer()

    parts = query.data.replace("admin_discount_set_", "").split("_")
    user_id = int(parts[0])
    tier = parts[1]
    discount = float(parts[2])

    conn = get_db()
    try:
        set_user_vip(conn, user_id, tier, discount)
    finally:
        conn.close()

    await query.edit_message_text(
        f"✅ User #{user_id} set to <b>{tier.upper()}</b> with <b>{discount}%</b> discount!",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("👤 Back to User", callback_data=f"admin_user_detail_{user_id}")
        ]])
    )


# ===========================================================================
# BOT SETTINGS
# ===========================================================================
async def admin_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show bot settings menu."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        maint = get_bot_setting(conn, "maintenance_mode", "off")
        welcome = get_bot_setting(conn, "welcome_msg", "")
        restock = get_bot_setting(conn, "restock_notify", "on")
    finally:
        conn.close()

    keyboard = [
        [InlineKeyboardButton(
            f"🚧 Maintenance: {'ON 🔴' if maint == 'on' else 'OFF 🟢'}",
            callback_data="admin_settings_maintenance"
        )],
        [InlineKeyboardButton("📝 Edit Welcome Message", callback_data="admin_settings_welcome")],
        [InlineKeyboardButton("💳 Payment Gateway", callback_data="admin_settings_payment")],
        [InlineKeyboardButton("🔔 Admin Notifications", callback_data="admin_settings_notify")],
        [InlineKeyboardButton(
            f"📢 Restock Alerts: {'ON 🔔' if restock == 'on' else 'OFF 🔕'}",
            callback_data="admin_settings_restock"
        )],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        f"⚙️ <b>Bot Settings</b>\n\n"
        f"🚧 Maintenance: <b>{'ON' if maint == 'on' else 'OFF'}</b>\n"
        f"📝 Welcome: {'Custom' if welcome else 'Default'}\n"
        f"📢 Restock Alerts: <b>{'ON' if restock == 'on' else 'OFF'}</b>\n\n"
        f"Select a setting to change:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_settings_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle maintenance mode."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        current = get_bot_setting(conn, "maintenance_mode", "off")
        new_mode = "off" if current == "on" else "on"
        set_bot_setting(conn, "maintenance_mode", new_mode)
    finally:
        conn.close()

    await query.edit_message_text(
        f"🚧 Maintenance mode: <b>{'ON 🔴' if new_mode == 'on' else 'OFF 🟢'}</b>\n\n"
        f"{'Only admins can use the bot.' if new_mode == 'on' else 'All users can use the bot.'}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
        ]])
    )


async def admin_settings_welcome(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt to edit welcome message (text or photo)."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "settings_welcome"

    await query.edit_message_text(
        "📝 <b>Edit Welcome Message</b>\n\n"
        "Send the new welcome message:\n\n"
        "📝 <b>Text only</b> — with formatting:\n"
        "• <b>Bold</b> (Ctrl+B), <i>Italic</i> (Ctrl+I), <u>Underline</u> (Ctrl+U)\n"
        "• <s>Strikethrough</s> (Ctrl+Shift+X), <code>Code</code> (Ctrl+Shift+M)\n"
        '• Quote, <pre>Code Block</pre>, premium emojis\n\n'
        "🖼 <b>Photo + Caption</b> — send a photo with caption text\n"
        "  The caption supports the same formatting as above.\n\n"
        "<i>Send /default to reset to default.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="admin_settings")
        ]])
    )


async def admin_settings_notify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle admin notifications for new orders."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        current = get_bot_setting(conn, "notify_new_orders", "on")
        new_val = "off" if current == "on" else "on"
        set_bot_setting(conn, "notify_new_orders", new_val)
    finally:
        conn.close()

    await query.edit_message_text(
        f"🔔 New order notifications: <b>{'ON' if new_val == 'on' else 'OFF'}</b>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
        ]])
    )


async def admin_settings_restock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Toggle restock notifications to users."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        current = get_bot_setting(conn, "restock_notify", "on")
        new_val = "off" if current == "on" else "on"
        set_bot_setting(conn, "restock_notify", new_val)
    finally:
        conn.close()

    await query.edit_message_text(
        f"📢 Restock alerts: <b>{'ON 🔔' if new_val == 'on' else 'OFF 🔕'}</b>\n\n"
        f"{'Users will be notified when out-of-stock products are restocked.' if new_val == 'on' else 'Restock alerts are disabled.'}",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
        ]])
    )


# ===========================================================================
# PAYMENT GATEWAY SETTINGS (KHQRPay)
# ===========================================================================
async def admin_settings_payment(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show KHQRPay payment gateway config."""
    query = update.callback_query
    await query.answer()

    from services.khqrpay import get_khqrpay_config

    conn = get_db()
    try:
        cfg = get_khqrpay_config(conn)
    finally:
        conn.close()

    pid = cfg["profile_id"] or "Not set"
    sk = (cfg["secret_key"][:4] + "****" + cfg["secret_key"][-4:]) if cfg["secret_key"] else "Not set"
    aba = cfg["aba_url"] or "Not set"

    keyboard = [
        [InlineKeyboardButton("🆔 Edit Profile ID", callback_data="admin_pay_profile")],
        [InlineKeyboardButton("🔑 Edit Secret Key", callback_data="admin_pay_secret")],
        [InlineKeyboardButton("🔗 Edit ABA Pay URL", callback_data="admin_pay_aba")],
        [InlineKeyboardButton("🔄 Reset All", callback_data="admin_pay_reset")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_settings")],
    ]

    await query.edit_message_text(
        f"💳 <b>Payment Gateway — KHQRPay</b>\n\n"
        f"🆔 Profile ID: <code>{pid}</code>\n"
        f"🔑 Secret Key: <code>{sk}</code>\n"
        f"🔗 ABA Pay URL: <code>{aba}</code>\n\n"
        f"<i>Supports Bakong KHQR, ABA Pay, Binance Pay.</i>",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_pay_set_profile(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to enter new Profile ID."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "pay_profile"
    await query.edit_message_text(
        "🆔 <b>Set KHQRPay Profile ID</b>\n\n"
        "Send the Profile ID from your khqr.cc merchant dashboard:\n"
        "<i>Example: TDS8ztn3Y21bSq3b5J5BXUWDRfzOIzwP</i>",
        parse_mode="HTML", reply_markup=_cancel_button("admin_settings_payment"),
    )


async def admin_pay_set_secret(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to enter new Secret Key."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "pay_secret"
    await query.edit_message_text(
        "🔑 <b>Set KHQRPay Secret Key</b>\n\n"
        "Send the Secret Key from your khqr.cc merchant dashboard:\n"
        "<i>⚠️ This is sensitive — never share it.</i>",
        parse_mode="HTML", reply_markup=_cancel_button("admin_settings_payment"),
    )


async def admin_pay_set_aba(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to enter new ABA Pay URL."""
    query = update.callback_query
    await query.answer()
    context.user_data["admin_state"] = "pay_aba"
    await query.edit_message_text(
        "🔗 <b>Set ABA Pay URL</b>\n\n"
        "Send the ABA Pay link URL:\n"
        "<i>Example: https://link.payway.com.kh/ABAPAY5h478304I</i>",
        parse_mode="HTML", reply_markup=_cancel_button("admin_settings_payment"),
    )


async def admin_pay_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reset all KHQRPay settings."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        set_bot_setting(conn, "khqrpay_profile_id", "")
        set_bot_setting(conn, "khqrpay_secret_key", "")
        set_bot_setting(conn, "khqrpay_aba_url", "")
    finally:
        conn.close()

    await query.edit_message_text(
        "🔄 <b>Payment settings reset!</b>\n\nAll values cleared — will use .env defaults.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("💳 Back", callback_data="admin_settings_payment"),
        ]])
    )


# ===========================================================================
# EXPORT CSV
# ===========================================================================
async def admin_export_csv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Export data as CSV file."""
    query = update.callback_query
    await query.answer()

    conn = get_db()
    try:
        orders_csv = export_orders_csv(conn)
        users_csv = export_users_csv(conn)
    finally:
        conn.close()

    # Send orders CSV
    import io
    orders_file = io.BytesIO(orders_csv.encode("utf-8-sig"))
    orders_file.name = "orders_export.csv"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=orders_file,
        caption="📊 Orders Export",
    )

    # Send users CSV
    users_file = io.BytesIO(users_csv.encode("utf-8-sig"))
    users_file.name = "users_export.csv"
    await context.bot.send_document(
        chat_id=query.message.chat_id,
        document=users_file,
        caption="👥 Users Export",
    )

    await query.edit_message_text(
        "✅ CSV files exported!\n\nCheck above for the download links.",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_panel")
        ]])
    )


# ===========================================================================
# BUTTON STYLES (Color Manager — like Customize)
# ===========================================================================
async def admin_button_styles(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all button sections in a grid (like Customize)."""
    query = update.callback_query
    await query.answer()

    from utils.emoji_manager import BTN_SECTIONS, load_button_config

    section_names = list(BTN_SECTIONS.keys())
    # Auto-append an "Other" section for any button key present in
    # button_config.json but not listed in BTN_SECTIONS, so newly added
    # buttons are always reachable from admin.
    known = {k for keys in BTN_SECTIONS.values() for k in keys}
    cfg_keys = [k for k in load_button_config().keys() if not k.startswith("_") and k not in known]
    if cfg_keys and "🧩 Other" not in section_names:
        section_names.append("🧩 Other")

    buttons = []
    row = []
    for name in section_names:
        row.append(InlineKeyboardButton(name, callback_data=f"btn_sec_{name}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])

    await query.edit_message_text(
        "🎨 <b>Button Styles</b>\n\n"
        "Select a section to customize button <b>premium emoji icons</b>.\n"
        "⚠️ <b>Note:</b> Telegram API does not support button colors.\n"
        "You CAN set premium custom emojis for button icons here.",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_button_section(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all buttons in a section as they appear on the bot (with emoji + name)."""
    query = update.callback_query
    await query.answer()

    from utils.emoji_manager import (
        BTN_SECTIONS, BTN_LABELS, get_button_style, get_plain, get_premium_id,
        load_button_config, strip_leading_emoji,
    )

    section_name = query.data.replace("btn_sec_", "")
    if section_name == "🧩 Other":
        known = {k for ks in BTN_SECTIONS.values() for k in ks}
        keys = [k for k in load_button_config().keys() if not k.startswith("_") and k not in known]
    else:
        keys = BTN_SECTIONS.get(section_name, [])

    if not keys:
        await query.answer("No buttons in this section")
        return

    buttons = []
    for key in keys:
        emoji = get_plain(key)
        label = BTN_LABELS.get(key, key)
        premium_id = get_premium_id(key)
        style = get_button_style(key)

        # Premium: text only (strip unicode). Plain: unicode + label
        if premium_id:
            btn_text = strip_leading_emoji(label) or label
        else:
            btn_text = f"{emoji} {label}".strip()
        btn_kwargs = {"text": btn_text, "callback_data": f"btn_detail_{key}"}
        if premium_id:
            btn_kwargs["icon_custom_emoji_id"] = str(premium_id)
        if style:
            btn_kwargs["style"] = style
        try:
            buttons.append([InlineKeyboardButton(**btn_kwargs)])
        except TypeError:
            buttons.append([InlineKeyboardButton(text=btn_text, callback_data=f"btn_detail_{key}")])

    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_button_styles")])

    await query.edit_message_text(
        f"🎨 <b>{section_name}</b>\n\n"
        f"These buttons appear exactly like this on your bot.\n"
        f"Tap a button to change its color:",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def admin_button_detail(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show color picker for a button — simple color labels, no emoji."""
    query = update.callback_query
    await query.answer()

    from utils.emoji_manager import BTN_LABELS, get_button_style

    key = query.data.replace("btn_detail_", "")
    current = get_button_style(key)
    label = BTN_LABELS.get(key, key)

    color_names = {"primary": "Blue", "success": "Green", "danger": "Red", None: "Default"}
    current_name = color_names.get(current, "Default")

    text = (
        f"🎨 <b>{label}</b>\n\n"
        f"Current color: <b>{current_name}</b>\n\n"
        f"Tap a color to apply it instantly:"
    )

    # Color picker — simple text labels
    keyboard = [
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'primary' else ''}🔵 Blue",
            callback_data=f"btn_set_{key}__primary"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'success' else ''}🟢 Green",
            callback_data=f"btn_set_{key}__success"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current == 'danger' else ''}🔴 Red",
            callback_data=f"btn_set_{key}__danger"
        )],
        [InlineKeyboardButton(
            f"{'✅ ' if current is None else ''}⚪ Default",
            callback_data=f"btn_set_{key}__none"
        )],
        [InlineKeyboardButton("🔄 Update Color", callback_data=f"btn_prompt_{key}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"btn_sec_{_find_btn_section(key)}")],
    ]

    await query.edit_message_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard))


def _find_btn_section(key: str) -> str:
    from utils.emoji_manager import BTN_SECTIONS
    for sec_name, keys in BTN_SECTIONS.items():
        if key in keys:
            return sec_name
    return "🧩 Other"


async def admin_button_style_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Apply a style directly from color picker, or prompt for manual entry."""
    query = update.callback_query
    await query.answer()

    data = query.data  # "btn_set_KEY__STYLE" or "btn_prompt_KEY"

    if data.startswith("btn_set_"):
        # Direct set from color picker — use __ as separator (keys have underscores)
        payload = data.replace("btn_set_", "")
        if "__" in payload:
            key, style = payload.rsplit("__", 1)
        else:
            key, style = payload, "none"
        if style == "none":
            style = None

        from utils.emoji_manager import set_button_style, BTN_LABELS, BTN_STYLE_LABELS
        set_button_style(key, style)
        label = BTN_LABELS.get(key, key)
        style_label = BTN_STYLE_LABELS.get(style, "⚪ Default")

        await query.edit_message_text(
            f"✅ <b>{label}</b> set to {style_label}!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"btn_detail_{key}"),
                InlineKeyboardButton("🎨 All Buttons", callback_data="admin_button_styles"),
            ]])
        )
    elif data.startswith("btn_prompt_"):
        # Prompt for manual style entry
        key = data.replace("btn_prompt_", "")
        context.user_data["admin_state"] = "btn_style_set"
        context.user_data["admin_data"] = {"style_key": key}

        from utils.emoji_manager import BTN_LABELS
        label = BTN_LABELS.get(key, key)

        await query.edit_message_text(
            f"✏️ <b>Set Style for:</b> {label}\n\n"
            f"Send one of:\n"
            f"<code>primary</code> (🔵 Blue)\n"
            f"<code>success</code> (🟢 Green)\n"
            f"<code>danger</code> (🔴 Red)\n"
            f"<code>none</code> (⚪ Default)",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("❌ Cancel", callback_data=f"btn_detail_{key}")
            ]])
        )




# ===========================================================================
# BACKUP & RESTORE
# ===========================================================================
async def admin_backup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show backup & restore menu + auto-backup status from Supabase."""
    query = update.callback_query
    await query.answer()

    auto_line = "<i>No auto-backup yet — first run after deploy.</i>"
    try:
        from services.supabase_sync import get_auto_backup_meta
        meta = get_auto_backup_meta()
        if meta:
            auto_line = (
                f"✅ Last auto-backup: <b>{meta.get('timestamp', '?')}</b>\n"
                f"📊 {meta.get('total_rows', 0)} rows · stored in Supabase"
            )
    except Exception as e:
        auto_line = f"⚠️ Auto-backup status unavailable: {e}"

    keyboard = [
        [InlineKeyboardButton("💾 Create Backup (file)", callback_data="admin_backup_create")],
        [InlineKeyboardButton("☁️ Run Auto-Backup Now", callback_data="admin_backup_auto_now")],
        [InlineKeyboardButton("♻️ Restore Latest Auto-Backup", callback_data="admin_backup_auto_restore")],
        [InlineKeyboardButton("📥 Restore from File", callback_data="admin_backup_restore")],
        [InlineKeyboardButton("🔙 Back", callback_data="admin_panel")],
    ]

    await query.edit_message_text(
        "💾 <b>Backup & Restore</b>\n\n"
        f"{auto_line}\n\n"
        "• <b>Create Backup</b>: Download JSON file from Supabase\n"
        "• <b>Auto-Backup</b>: Saves to Supabase every 24h automatically\n"
        "• <b>Restore</b>: Overwrite live data from backup\n\n"
        "⚠️ Restore will <b>overwrite</b> all existing data!",
        parse_mode="HTML", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def admin_backup_auto_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Run one auto-backup to Supabase immediately."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("☁️ <b>Saving auto-backup to Supabase...</b>", parse_mode="HTML")
    try:
        from services.supabase_sync import save_auto_backup_to_supabase
        meta = save_auto_backup_to_supabase()
        await query.edit_message_text(
            f"✅ <b>Auto-backup saved to Supabase</b>\n\n"
            f"📅 {meta.get('timestamp')}\n"
            f"📊 {meta.get('total_rows', 0)} rows\n"
            f"🔑 key: <code>{meta.get('data_key', 'auto_backup_latest')}</code>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_backup")
            ]]),
        )
    except Exception as e:
        logger.error(f"Manual auto-backup failed: {e}")
        await query.edit_message_text(
            f"❌ Auto-backup failed: {e}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_backup")
            ]]),
        )


async def admin_backup_auto_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirm restore from latest Supabase auto-backup."""
    query = update.callback_query
    await query.answer()

    from services.supabase_sync import get_auto_backup_meta
    meta = get_auto_backup_meta()
    if not meta:
        await query.edit_message_text(
            "❌ No auto-backup found in Supabase yet.",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_backup")
            ]]),
        )
        return

    await query.edit_message_text(
        "⚠️ <b>Restore Latest Auto-Backup?</b>\n\n"
        f"📅 {meta.get('timestamp')}\n"
        f"📊 {meta.get('total_rows', 0)} rows\n\n"
        "This will <b>DELETE</b> current data and replace it.\n"
        "This cannot be undone.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Confirm Restore", callback_data="admin_backup_auto_restore_go")],
            [InlineKeyboardButton("❌ Cancel", callback_data="admin_backup")],
        ]),
    )


async def admin_backup_auto_restore_go(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute restore from latest Supabase auto-backup."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("♻️ Restoring from Supabase auto-backup...", parse_mode="HTML")
    try:
        from services.supabase_sync import restore_latest_auto_backup
        result = restore_latest_auto_backup()
        if result.get("success"):
            try:
                from utils.emoji_manager import reload, reload_button_cache
                reload()
                reload_button_cache()
            except Exception:
                pass
            lines = [f"✅ <b>Restored from auto-backup</b>\n"]
            for table, status in (result.get("results") or {}).items():
                icon = "✅" if str(status).startswith("restored") else "⚠️"
                lines.append(f"  {icon} {table}: {status}")
            text = "\n".join(lines)
        else:
            text = f"❌ Restore failed: {result.get('error', 'Unknown')}"
    except Exception as e:
        logger.error(f"Auto-restore failed: {e}")
        text = f"❌ Restore error: {e}"

    await query.edit_message_text(
        text[:4000],
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back", callback_data="admin_backup")
        ]]),
    )


async def admin_backup_create(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a backup of Supabase data and send as JSON file (+ store auto-backup)."""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text("💾 <b>Creating backup...</b>\n\nReading data from Supabase...", parse_mode="HTML")

    try:
        from services.supabase_sync import backup_to_bytesio, save_auto_backup_to_supabase
        buf, timestamp, total_rows = backup_to_bytesio()

        # Also refresh cloud auto-backup
        try:
            save_auto_backup_to_supabase()
        except Exception as e:
            logger.warning(f"Cloud auto-backup after file export failed: {e}")

        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=buf,
            caption=f"💾 <b>Database Backup</b>\n\n"
                    f"📅 {timestamp}\n"
                    f"📊 {total_rows} rows across {8} tables\n"
                    f"☁️ Also saved to Supabase auto-backup",
            parse_mode="HTML",
        )

        await query.edit_message_text(
            f"✅ <b>Backup created!</b>\n\n"
            f"📅 {timestamp}\n"
            f"📊 {total_rows} rows exported\n"
            f"☁️ Synced to Supabase auto-backup\n\n"
            f"File sent above. Keep it safe!",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_backup")
            ]])
        )
    except Exception as e:
        logger.error(f"Backup failed: {e}")
        await query.edit_message_text(
            f"❌ Backup failed: {e}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_backup")
            ]])
        )


async def admin_backup_restore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prompt admin to upload a backup file for restore."""
    query = update.callback_query
    await query.answer()

    context.user_data["admin_state"] = "backup_restore_upload"

    await query.edit_message_text(
        "⚠️ <b>Restore Backup</b>\n\n"
        "This will <b>DELETE all existing data</b> and replace it with the backup.\n\n"
        "📤 <b>Upload</b> the backup JSON file now to begin restore.\n\n"
        "<i>Send /cancel to abort.</i>",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("❌ Cancel", callback_data="admin_backup")
        ]])
    )


async def admin_backup_restore_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute restore from an uploaded backup file (document)."""
    msg = update.effective_message

    await msg.reply_html("📥 <b>Downloading backup file...</b>")

    try:
        # Download the document with retry
        file_bytes = None
        for attempt in range(1, 4):
            try:
                file = await context.bot.get_file(update.message.document.file_id)
                file_bytes = await file.download_as_bytearray()
                break
            except Exception as e:
                if attempt < 3:
                    await asyncio.sleep(2)
                else:
                    raise e

        await msg.reply_html("🔄 <b>Restoring data to Supabase...</b>")

        from services.supabase_sync import restore_from_bytesio
        result = restore_from_bytesio(bytes(file_bytes))

        if result["success"]:
            lines = [f"✅ <b>Restore Complete!</b>\n"]
            lines.append(f"📊 {result['restored_tables']} tables restored:\n")
            for table, status in result["results"].items():
                icon = "✅" if status.startswith("restored") else "⚠️"
                lines.append(f"  {icon} {table}: {status}")

            # Reload emoji caches
            try:
                from utils.emoji_manager import reload, reload_button_cache
                reload()
                reload_button_cache()
            except Exception:
                pass

            text = "\n".join(lines)
        else:
            text = f"❌ Restore failed: {result.get('error', 'Unknown error')}"

    except Exception as e:
        err_msg = str(e)[:200]
        logger.error(f"Restore error: {e}")
        if "ReadError" in err_msg or "timeout" in err_msg.lower():
            text = f"❌ Network error during restore. Try again.\n\n<i>{err_msg}</i>"
        elif "Too large" in err_msg:
            text = f"❌ Backup file is too large. Try a smaller backup.\n\n<i>{err_msg}</i>"
        else:
            text = f"❌ Restore error: {err_msg}"

    await msg.reply_html(
        text[:4000],
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("🔙 Back to Backup", callback_data="admin_backup")
        ]])
    )


# ===========================================================================
# TEXT INPUT HANDLER for admin states
# ===========================================================================
async def handle_admin_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Process text input based on current admin_state."""
    if not update.message:
        return

    user = update.effective_user
    if user.id != ADMIN_ID:
        return

    state = context.user_data.get("admin_state")
    data = context.user_data.get("admin_data", {})
    text = (update.message.text or "").strip()

    if not state:
        return  # Not in admin mode

    # --- Category Name ---
    if state == "cat_name":
        if not text:
            await update.message.reply_html("❌ Name cannot be empty. Try again:", reply_markup=_cancel_button())
            return
        data["name"] = text
        context.user_data["admin_state"] = "cat_emoji"
        await update.message.reply_html(
            f"✅ Name: <b>{text}</b>\n\n"
            f"Now send the <b>emoji</b> for this category:\n\n"
            f"<i>Example: ✈️ or 🎬 or 💎</i>",
            reply_markup=_cancel_button(),
        )

    # --- Category Emoji ---
    elif state == "cat_emoji":
        emoji = _extract_emoji(update.message)
        name = data.get("name", "Category")
        conn = get_db()
        try:
            add_category(conn, name, emoji)
        finally:
            conn.close()

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        await update.message.reply_html(
            f"✅ Category created!\n\n{emoji_for_html(emoji)} <b>{name}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📂 Back to Categories", callback_data="admin_categories"),
                InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel"),
            ]]),
        )

    # --- Product Name ---
    elif state == "prod_name":
        if not text:
            await update.message.reply_html("❌ Name cannot be empty. Try again:", reply_markup=_cancel_button("admin_products"))
            return
        data["name"] = text
        context.user_data["admin_state"] = "prod_price"
        await update.message.reply_html(
            f"✅ Name: <b>{text}</b>\n\n"
            f"Now send the <b>price</b> (Step 2/4):\n\n"
            f"<i>Example: 1.50</i>",
            reply_markup=_cancel_button("admin_products"),
        )

    # --- Product Price ---
    elif state == "prod_price":
        try:
            price = float(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid price. Please enter a number:", reply_markup=_cancel_button("admin_products"))
            return
        data["price"] = price
        context.user_data["admin_state"] = "prod_emoji"
        await update.message.reply_html(
            f"✅ Price: <b>${price:.2f}</b>\n\n"
            f"Now send the <b>emoji</b> (Step 3/4):\n\n"
            f"<i>Example: 🤖 or ⭐ or 🔥</i>",
            reply_markup=_cancel_button("admin_products"),
        )

    # --- Product Emoji ---
    elif state == "prod_emoji":
        emoji = _extract_emoji(update.message)
        data["emoji"] = emoji
        context.user_data["admin_state"] = "prod_desc"
        await update.message.reply_html(
            f"✅ Emoji set!\n\n"
            f"Now send a <b>description</b> for this product (Step 4/4):\n"
            f"<i>Example: Premium account with 5TB storage, 18 months validity.</i>\n\n"
            f"<b>Formatting:</b> <b>Bold</b>, <i>Italic</i>, <code>Code</code>, premium emoji supported.\n"
            f"Send <b>/skip</b> to skip.",
            reply_markup=_cancel_button("admin_products"),
        )

    # --- Product Description ---
    elif state == "prod_desc":
        # Capture rich formatting + premium emoji mixed with text (via text_html)
        raw_text = (update.message.text or "").strip()
        if raw_text.lower() == "/skip":
            desc = ""
        else:
            desc = _message_to_html(update.message) if update.message.text else ""
        cat_id = data.get("cat_id")
        name = data.get("name", "Product")
        price = data.get("price", 0)
        emoji = data.get("emoji", "📦")

        conn = get_db()
        try:
            add_product(conn, cat_id, name, price, emoji, desc)
        finally:
            conn.close()

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        # desc is already HTML (may include <tg-emoji>); insert as-is under parse_mode=HTML
        await update.message.reply_html(
            f"✅ Product created!\n\n{emoji_for_html(emoji)} <b>{name}</b> — ${price:.2f}"
            + (f"\n📝 {desc}" if desc else ""),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📦 Back to Products", callback_data="admin_products"),
                InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel"),
            ]]),
        )

    # --- Stock Account ---
    elif state == "stock_account":
        prod_id = data.get("prod_id")

        # Check for document upload (txt file with accounts)
        if update.message.document:
            try:
                file = await context.bot.get_file(update.message.document.file_id)
                file_bytes = await file.download_as_bytearray()
                content = file_bytes.decode("utf-8", errors="replace")
                lines = [l.strip() for l in content.split("\n") if l.strip()]
            except Exception as e:
                await update.message.reply_html(
                    f"❌ Failed to read file: {e}",
                    reply_markup=_cancel_button("admin_stock_menu"),
                )
                return
        else:
            # Text input — split by newlines
            lines = [l.strip() for l in text.split("\n") if l.strip()]

        if not lines:
            await update.message.reply_html(
                "❌ No accounts found. Please send text or upload a .txt file.",
                reply_markup=_cancel_button("admin_stock_menu"),
            )
            return

        conn = get_db()
        try:
            stock_before = get_stock_count(conn, prod_id)
            add_stock_bulk(conn, prod_id, lines)
            prod = get_product(conn, prod_id)
            stock = get_stock_count(conn, prod_id)
            was_restock = (stock_before == 0 and stock > 0)
            notify_on = get_bot_setting(conn, "restock_notify", "on") == "on"
        finally:
            conn.close()

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        # ── Build extra info about restock notification status ──
        restock_note = ""
        if was_restock:
            if notify_on:
                restock_note = "\n📢 <i>Restock alert sending to users...</i>"
            else:
                restock_note = "\n🔕 <i>Restock alerts OFF — enable in ⚙️ Settings</i>"

        await update.message.reply_html(
            f"✅ <b>{len(lines)} stock item(s)</b> added to {emoji_for_html(prod['emoji'])} {prod['name']}!\n"
            f"📦 Total stock: <b>{stock}</b>{restock_note}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📥 Add More Stock", callback_data="admin_add_stock"),
                InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel"),
            ]]),
        )

        # ── Restock notification to users ──
        if was_restock and notify_on:
            asyncio.create_task(_send_restock_notify(
                context=context,
                prod=prod,
                stock=stock,
                chat_id=update.effective_chat.id,
            ))

    # --- Promo Code Name ---
    elif state == "promo_code":
        if not text:
            await update.message.reply_html("❌ Code cannot be empty.", reply_markup=_cancel_button("admin_promos"))
            return
        data["code"] = text.upper()
        context.user_data["admin_state"] = "promo_type"
        await update.message.reply_html(
            f"✅ Code: <b>{data['code']}</b>\n\n"
            f"Now choose discount <b>type</b>:\n\n"
            f"Type <b>%</b> for percentage or <b>$</b> for flat amount",
            reply_markup=_cancel_button("admin_promos"),
        )

    # --- Promo Type ---
    elif state == "promo_type":
        t = text.strip()
        if t in ("%", "percent"):
            data["discount_type"] = "percent"
        elif t in ("$", "flat"):
            data["discount_type"] = "flat"
        else:
            await update.message.reply_html(
                "❌ Type <b>%</b> or <b>$</b>",
                reply_markup=_cancel_button("admin_promos"),
            )
            return
        context.user_data["admin_state"] = "promo_value"
        await update.message.reply_html(
            f"✅ Type: <b>{data['discount_type']}</b>\n\n"
            f"Now send the <b>discount value</b>:\n\n"
            f"<i>For %: enter 10 for 10% off</i>\n"
            f"<i>For $: enter 2 for $2 off</i>",
            reply_markup=_cancel_button("admin_promos"),
        )

    # --- Promo Value ---
    elif state == "promo_value":
        try:
            val = float(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid number.", reply_markup=_cancel_button("admin_promos"))
            return
        data["discount_value"] = val
        context.user_data["admin_state"] = "promo_max"
        await update.message.reply_html(
            f"✅ Value: <b>{val}</b>\n\n"
            f"Now send <b>max uses</b> (0 = unlimited):\n\n"
            f"<i>Example: 100</i>",
            reply_markup=_cancel_button("admin_promos"),
        )

    # --- Promo Max Uses ---
    elif state == "promo_max":
        try:
            max_u = int(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid number.", reply_markup=_cancel_button("admin_promos"))
            return
        data["max_uses"] = max_u
        context.user_data["admin_state"] = "promo_min"
        await update.message.reply_html(
            f"✅ Max uses: <b>{max_u if max_u > 0 else 'Unlimited'}</b>\n\n"
            f"Now send <b>minimum order amount</b> (0 = no minimum):\n\n"
            f"<i>Example: 5 for $5 minimum</i>",
            reply_markup=_cancel_button("admin_promos"),
        )

    # --- Promo Min Order ---
    elif state == "promo_min":
        try:
            min_o = float(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid number.", reply_markup=_cancel_button("admin_promos"))
            return
        data["min_order"] = min_o

        conn = get_db()
        try:
            create_promo_code(
                conn, data["code"], data["discount_type"],
                data["discount_value"], data["max_uses"], data["min_order"],
            )
        finally:
            conn.close()

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        d_type = "%" if data["discount_type"] == "percent" else "$"
        await update.message.reply_html(
            f"✅ Promo code created!\n\n"
            f"🎟 <code>{data['code']}</code> — {d_type}{data['discount_value']} off\n"
            f"Min order: ${data['min_order']:.2f} | Max uses: {data['max_uses'] if data['max_uses'] > 0 else 'Unlimited'}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎟 Back to Promos", callback_data="admin_promos"),
                InlineKeyboardButton("🏠 Admin Panel", callback_data="admin_panel"),
            ]]),
        )

    # --- Edit Category Name ---
    elif state == "cat_edit_name":
        cat_id = data.get("edit_cat_id")
        if not text:
            await update.message.reply_html("❌ Name cannot be empty.", reply_markup=_cancel_button("admin_categories"))
            return
        conn = get_db()
        try:
            update_category(conn, cat_id, name=text)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"✅ Category name updated to <b>{text}</b>!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📂 Back to Categories", callback_data="admin_categories"),
            ]]),
        )

    # --- Edit Category Emoji ---
    elif state == "cat_edit_emoji":
        cat_id = data.get("edit_cat_id")
        emoji = _extract_emoji(update.message)
        conn = get_db()
        try:
            update_category(conn, cat_id, emoji=emoji)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"✅ Category emoji updated to {emoji}!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📂 Back to Categories", callback_data="admin_categories"),
            ]]),
        )

    # --- Edit Product Value ---
    elif state == "prod_edit_value":
        prod_id = data.get("edit_prod_id")
        field = data.get("edit_prod_field")
        # Description may be premium-emoji-only (still non-empty text from Telegram)
        if field != "desc" and not text:
            await update.message.reply_html("❌ Cannot be empty.", reply_markup=_cancel_button("admin_products"))
            return
        if field == "desc" and not (update.message.text or "").strip():
            await update.message.reply_html("❌ Cannot be empty.", reply_markup=_cancel_button("admin_products"))
            return

        conn = get_db()
        try:
            if field == "name":
                update_product(conn, prod_id, name=text)
            elif field == "price":
                update_product(conn, prod_id, price=float(text))
            elif field == "emoji":
                update_product(conn, prod_id, emoji=_extract_emoji(update.message))
            elif field == "desc":
                # Premium emoji + bold/italic + text → correct HTML via text_html
                update_product(conn, prod_id, description=_message_to_html(update.message))
            elif field == "cat":
                update_product(conn, prod_id, category_id=int(text))
        except (ValueError, TypeError):
            await update.message.reply_html("❌ Invalid value.", reply_markup=_cancel_button("admin_products"))
            return
        finally:
            conn.close()

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        # Preview saved description so admin can verify premium emoji rendered
        preview = ""
        if field == "desc":
            saved = _message_to_html(update.message)
            if saved:
                preview = f"\n\n📝 {saved}"

        await update.message.reply_html(
            f"✅ Product updated!{preview}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📦 Back to Products", callback_data="admin_products"),
            ]]),
        )

    # --- Stock Edit Detail ---
    elif state == "stock_edit_detail":
        stock_id = data.get("stock_id")
        conn = get_db()
        try:
            update_stock_detail(conn, stock_id, text)
            item = get_stock_item(conn, stock_id)
            prod_id = item["product_id"] if item else 0
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            "✅ Stock updated!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"admin_es_prod_{prod_id}"),
            ]]),
        )

    # --- Stock Replace ---
    elif state == "stock_replace":
        prod_id = data.get("prod_id")

        # Check for document upload
        if update.message.document:
            try:
                file = await context.bot.get_file(update.message.document.file_id)
                file_bytes = await file.download_as_bytearray()
                content = file_bytes.decode("utf-8", errors="replace")
                lines = [l.strip() for l in content.split("\n") if l.strip()]
            except Exception as e:
                await update.message.reply_html(
                    f"❌ Failed to read file: {e}",
                    reply_markup=_cancel_button("admin_stock_menu"),
                )
                return
        else:
            lines = [l.strip() for l in text.split("\n") if l.strip()]

        if not lines:
            await update.message.reply_html(
                "❌ No accounts found. Send text or upload a .txt file.",
                reply_markup=_cancel_button("admin_stock_menu"),
            )
            return

        conn = get_db()
        try:
            cnt = replace_all_stock(conn, prod_id, lines)
            prod = get_product(conn, prod_id)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"🔄 Stock replaced! <b>{cnt} items</b> in {emoji_for_html(prod['emoji'])} {prod['name']}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📥 Stock Menu", callback_data="admin_stock_menu"),
            ]]),
        )

    # --- User Add Balance ---
    elif state == "user_addbal":
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid amount.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"admin_user_detail_{data.get('user_id')}")
                ]]))
            return
        uid = data.get("user_id")
        conn = get_db()
        try:
            new_bal = update_user_balance(conn, uid, amt)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"✅ Added <b>${amt:.2f}</b>. New balance: <b>${new_bal:.2f}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👤 User Detail", callback_data=f"admin_user_detail_{uid}"),
            ]]),
        )
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"💰 <b>Balance Updated</b>\n\n"
                    f"Added: <b>+${amt:.2f}</b>\n"
                    f"New Balance: <b>${new_bal:.2f}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # --- User Deduct ---
    elif state == "user_deduct":
        try:
            amt = float(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid amount.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"admin_user_detail_{data.get('user_id')}")
                ]]))
            return
        uid = data.get("user_id")
        conn = get_db()
        try:
            new_bal = update_user_balance(conn, uid, -amt)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"💸 Deducted <b>${amt:.2f}</b>. New balance: <b>${new_bal:.2f}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("👤 User Detail", callback_data=f"admin_user_detail_{uid}"),
            ]]),
        )
        # Notify user
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=(
                    f"💰 <b>Balance Updated</b>\n\n"
                    f"Deducted: <b>-${amt:.2f}</b>\n"
                    f"New Balance: <b>${new_bal:.2f}</b>"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass

    # --- User DM ---
    elif state == "user_dm":
        uid = data.get("user_id")
        try:
            await context.bot.send_message(
                chat_id=uid,
                text=f"📩 <b>Message from Admin:</b>\n\n{text}",
                parse_mode="HTML",
            )
            success = True
        except Exception:
            success = False

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        if success:
            await update.message.reply_html(
                "✅ Message sent!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👤 Back to User", callback_data=f"admin_user_detail_{uid}"),
                ]]),
            )
        else:
            await update.message.reply_html(
                "❌ Failed to send. User may have blocked the bot.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("👤 Back to User", callback_data=f"admin_user_detail_{uid}"),
                ]]),
            )

    # --- User Search ---
    elif state == "user_search":
        try:
            uid = int(text)
        except ValueError:
            await update.message.reply_html("❌ Invalid User ID.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_users")
                ]]))
            return
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await admin_user_detail(update, context, user_id=uid)

    # --- Custom Emoji Set ---
    elif state == "custom_setval":
        key = data.get("emoji_key")
        if not key:
            await update.message.reply_html("❌ Missing emoji key. Open Customize again.")
            return

        # Premium → dict {"p","f"}; plain → unicode string
        emoji_value, custom_emoji_id = _extract_emoji_value(update.message)
        if not emoji_value and not custom_emoji_id:
            await update.message.reply_html(
                "❌ No emoji detected.\n\n"
                "Send <b>only</b> one premium or normal emoji (no text).",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Try again", callback_data=f"custom_setval_{key}"),
                ]]),
            )
            return

        # Prefer DEFAULTS fallback if premium fallback is empty/weird
        if isinstance(emoji_value, dict) and custom_emoji_id:
            if not emoji_value.get("f"):
                emoji_value["f"] = DEFAULTS.get(key) or "⭐"
            set_emoji(key, emoji_value)
        else:
            set_emoji(key, emoji_value if isinstance(emoji_value, str) else str(emoji_value))

        # Sync icon into button_config for ALL keys (admin + user buttons)
        try:
            from utils.emoji_manager import load_button_config, save_button_config, reload, reload_button_cache
            btn_cfg = load_button_config()
            entry = btn_cfg.get(key)
            if not isinstance(entry, dict):
                btn_cfg[key] = {
                    "text": None,
                    "icon_custom_emoji_id": str(custom_emoji_id) if custom_emoji_id else None,
                    "style": None,
                }
            else:
                entry["icon_custom_emoji_id"] = str(custom_emoji_id) if custom_emoji_id else None
            save_button_config(btn_cfg)
            reload()
            reload_button_cache()
        except Exception as e:
            logger.error(f"Error syncing custom emoji ID in button config for {key}: {e}")

        label = LABELS.get(key, key)
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        # Success: show ONLY the new emoji (premium HTML or plain)
        display = eget(key)
        detail_btn = InlineKeyboardButton("Emoji Details", callback_data=f"custom_emoji_{key}")
        if custom_emoji_id:
            try:
                detail_btn = InlineKeyboardButton(
                    "Emoji Details",
                    callback_data=f"custom_emoji_{key}",
                    icon_custom_emoji_id=str(custom_emoji_id),
                )
            except TypeError:
                pass

        await update.message.reply_html(
            f"✅ <b>{label}</b> updated\n\n"
            f"New emoji: {display}\n"
            f"<i>Old emoji replaced — only this one is used now.</i>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🎨 Customize", callback_data="admin_customize"),
                detail_btn,
            ]]),
        )

    # --- Broadcast Message ---
    elif state == "broadcast":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        conn = get_db()
        try:
            users = get_all_users(conn, include_banned=False)
        finally:
            conn.close()

        msg = update.message
        success = 0
        failed = 0

        # ── Detect message type ──
        if msg.video:
            # Video broadcast
            for u in users:
                try:
                    await context.bot.send_video(
                        chat_id=u["user_id"],
                        video=msg.video.file_id,
                        caption=_message_to_html(msg) if msg.caption else None,
                        parse_mode="HTML" if msg.caption else None,
                    )
                    success += 1
                except Exception:
                    failed += 1

        elif msg.voice:
            # Voice broadcast
            for u in users:
                try:
                    await context.bot.send_voice(
                        chat_id=u["user_id"],
                        voice=msg.voice.file_id,
                        caption=_message_to_html(msg) if msg.caption else None,
                        parse_mode="HTML" if msg.caption else None,
                    )
                    success += 1
                except Exception:
                    failed += 1

        elif msg.photo:
            # Photo broadcast
            for u in users:
                try:
                    await context.bot.send_photo(
                        chat_id=u["user_id"],
                        photo=msg.photo[-1].file_id,
                        caption=_message_to_html(msg) if msg.caption else None,
                        parse_mode="HTML" if msg.caption else None,
                    )
                    success += 1
                except Exception:
                    failed += 1

        elif msg.document or msg.animation:
            # Document / GIF broadcast
            file_id = msg.document.file_id if msg.document else msg.animation.file_id
            for u in users:
                try:
                    await context.bot.send_document(
                        chat_id=u["user_id"],
                        document=file_id,
                        caption=_message_to_html(msg) if msg.caption else None,
                        parse_mode="HTML" if msg.caption else None,
                    )
                    success += 1
                except Exception:
                    failed += 1

        else:
            # Text broadcast (with premium emoji support)
            text_html = _message_to_html(msg)
            for u in users:
                try:
                    await context.bot.send_message(
                        chat_id=u["user_id"],
                        text=f"📢 <b>Announcement</b>\n\n{text_html}",
                        parse_mode="HTML",
                    )
                    success += 1
                except Exception:
                    failed += 1

        await update.message.reply_html(
            f"📢 <b>Broadcast Complete!</b>\n\n"
            f"✅ Sent: <b>{success}</b>\n"
            f"❌ Failed: <b>{failed}</b>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("📢 Send Another", callback_data="admin_broadcast_start"),
                InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel"),
            ]])
        )

    # --- Order Search ---
    elif state == "order_search":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        conn = get_db()
        try:
            orders = search_orders(conn, text, limit=20)
        finally:
            conn.close()

        if not orders:
            await update.message.reply_html(
                "🔍 No orders found.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")
                ]])
            )
            return

        buttons = []
        for o in orders:
            status_icon = {"pending": "⏳", "completed": "✅", "refunded": "↩️"}.get(o["status"], "❓")
            name = o.get("first_name") or f"ID:{o['user_id']}"
            buttons.append([InlineKeyboardButton(
                f"{status_icon} #{o['id']} | {name} | ${o['amount']:.2f}",
                callback_data=f"admin_order_detail_{o['id']}"
            )])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_orders_mgmt")])

        await update.message.reply_html(
            f"🔍 <b>Search Results</b> ({len(orders)} found)",
            reply_markup=InlineKeyboardMarkup(buttons)
        )

    # --- Settings Welcome Message ---
    elif state == "settings_welcome":
        msg = update.message
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        # Check for /default reset
        if msg.text and msg.text.strip().lower() == "/default":
            conn = get_db()
            try:
                set_bot_setting(conn, "welcome_msg", "")
                set_bot_setting(conn, "welcome_photo", "")
            finally:
                conn.close()
            await update.message.reply_html(
                "✅ Welcome message reset to default!",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
                ]])
            )
            return

        conn = get_db()
        try:
            if msg.photo:
                # ── Photo welcome ──
                photo_id = msg.photo[-1].file_id  # highest resolution
                caption_html = _message_to_html(msg) if msg.caption else ""
                set_bot_setting(conn, "welcome_photo", photo_id)
                set_bot_setting(conn, "welcome_msg", caption_html)
                await update.message.reply_html(
                    "✅ Welcome message updated with photo!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
                    ]])
                )
            else:
                # ── Text-only welcome ──
                welcome_html = _message_to_html(msg)
                set_bot_setting(conn, "welcome_msg", welcome_html)
                set_bot_setting(conn, "welcome_photo", "")  # clear any old photo
                await update.message.reply_html(
                    "✅ Welcome message updated!",
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🔙 Back", callback_data="admin_settings")
                    ]])
                )
        finally:
            conn.close()

    # --- Button Style Set ---
    elif state == "btn_style_set":
        style_key = data.get("style_key")
        new_style = text.strip().lower()
        valid_styles = ["primary", "secondary", "success", "danger", "warning", "info", "light", "dark"]

        if new_style == "none":
            new_style = None
        elif new_style not in valid_styles:
            await update.message.reply_html(
                "❌ Invalid style. Use: " + " | ".join(f"<code>{s}</code>" for s in valid_styles) + " | <code>none</code>",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data=f"btn_detail_{style_key}")
                ]])
            )
            return

        from utils.emoji_manager import set_button_style, BTN_LABELS, BTN_STYLE_LABELS
        set_button_style(style_key, new_style)
        label = BTN_LABELS.get(style_key, style_key)
        style_label = BTN_STYLE_LABELS.get(new_style, "⚪ Default")
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        await update.message.reply_html(
            f"✅ <b>{label}</b> set to {style_label}!",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data=f"btn_detail_{style_key}"),
                InlineKeyboardButton("🎨 All Buttons", callback_data="admin_button_styles"),
            ]])
        )

    # --- Payment Gateway: Profile ID ---
    elif state == "pay_profile":
        if not text:
            await update.message.reply_html("❌ Profile ID cannot be empty.", reply_markup=_cancel_button("admin_settings_payment"))
            return
        conn = get_db()
        try:
            set_bot_setting(conn, "khqrpay_profile_id", text)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"✅ Profile ID updated to: <code>{text}</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Back to Payment Settings", callback_data="admin_settings_payment"),
            ]])
        )

    # --- Payment Gateway: Secret Key ---
    elif state == "pay_secret":
        if not text:
            await update.message.reply_html("❌ Secret Key cannot be empty.", reply_markup=_cancel_button("admin_settings_payment"))
            return
        conn = get_db()
        try:
            set_bot_setting(conn, "khqrpay_secret_key", text)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        masked = text[:4] + "****" + text[-4:] if len(text) > 8 else "****"
        await update.message.reply_html(
            f"✅ Secret Key updated to: <code>{masked}</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Back to Payment Settings", callback_data="admin_settings_payment"),
            ]])
        )

    # --- Payment Gateway: ABA URL ---
    elif state == "pay_aba":
        if not text:
            await update.message.reply_html("❌ ABA URL cannot be empty.", reply_markup=_cancel_button("admin_settings_payment"))
            return
        conn = get_db()
        try:
            set_bot_setting(conn, "khqrpay_aba_url", text)
        finally:
            conn.close()
        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)
        await update.message.reply_html(
            f"✅ ABA Pay URL updated to: <code>{text}</code>",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("💳 Back to Payment Settings", callback_data="admin_settings_payment"),
            ]])
        )

    # --- Backup Restore Upload ---
    elif state == "backup_restore_upload":
        if not update.message.document:
            await update.message.reply_html(
                "❌ Please upload a <b>JSON backup file</b> (document), not a text message.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("❌ Cancel", callback_data="admin_backup")
                ]])
            )
            return

        context.user_data.pop("admin_state", None)
        context.user_data.pop("admin_data", None)

        await admin_backup_restore_execute(update, context)


# ===========================================================================
# Cancel button helper
# ===========================================================================
def _cancel_button(target: str = "admin_panel") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data=target),
    ]])


def _back_button(target: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔙 Back", callback_data=target),
    ]])


def _message_to_html(message) -> str:
    """
    Convert a Telegram message (with entities) to safe HTML string.

    Uses python-telegram-bot's built-in text_html / caption_html so that:
      - Premium custom emoji → <tg-emoji emoji-id="...">…</tg-emoji>
      - Bold/italic/code/etc. nest correctly with premium emoji
      - Plain text is HTML-escaped (no broken tags when mixing emoji + text)

    The previous hand-rolled converter broke offsets when premium emoji was
    combined with other formatting or surrounding text.
    """
    if not message:
        return ""

    # Prefer official PTB conversion (supports nested entities + custom emoji).
    try:
        if message.text is not None:
            html = message.text_html
            if html is not None:
                return html
        if message.caption is not None:
            html = message.caption_html
            if html is not None:
                return html
    except Exception as e:
        logger.warning(f"_message_to_html: text_html failed ({e}); falling back")

    # Fallback: plain escape only (no entity formatting)
    from html import escape as html_escape
    text = message.text or message.caption or ""
    return html_escape(text, quote=False) if text else ""


def _extract_emoji_value(message) -> tuple:
    """
    Extract emoji from a message for customize / product / category.

    Returns (value, premium_id_or_None):
      - Premium: ({"p": "id", "f": "⭐"}, "id")
      - Plain:   ("⭐", None)

    Uses MessageEntity.CUSTOM_EMOJI / "custom_emoji" (works with PTB enums).
    """
    text = (message.text or message.caption or "").strip()
    entities = message.entities or message.caption_entities or []

    for entity in entities:
        etype = getattr(entity, "type", None)
        etype_str = str(etype) if etype is not None else ""
        if etype_str in ("custom_emoji", "MessageEntityType.CUSTOM_EMOJI") or (
            getattr(etype, "value", None) == "custom_emoji"
        ) or etype == "custom_emoji":
            emoji_id = getattr(entity, "custom_emoji_id", None) or ""
            if not emoji_id:
                continue
            fallback = "⭐"
            if text:
                try:
                    utf16 = text.encode("utf-16-le")
                    seg = utf16[entity.offset * 2:(entity.offset + entity.length) * 2]
                    decoded = seg.decode("utf-16-le", errors="ignore").strip()
                    if decoded:
                        fallback = decoded
                except Exception:
                    pass
            return {"p": str(emoji_id), "f": fallback}, str(emoji_id)

    # Plain unicode — first grapheme cluster-ish (up to 8 code units for ZWJ)
    if text:
        return text[:8], None
    return None, None


def _extract_emoji(message) -> str:
    """
    Extract emoji for product/category storage.
    - Plain unicode → e.g. "⭐"
    - Premium → JSON string '{"p":"id","f":"⭐"}' (parse_db_emoji compatible)
    """
    value, premium_id = _extract_emoji_value(message)
    if premium_id and isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, str) and value:
        return value[:8]
    return "📦"
