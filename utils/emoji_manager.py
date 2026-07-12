"""
Emoji Manager — Load/save customizable emojis from JSON.
All bot messages reference emojis by name via E.get("name").
Admin can customize any emoji via /admin → 🎨 Customize → Set Emoji.

Storage format (emoji_config.json):
  - Plain unicode:  "key": "⭐"
  - Premium emoji:  "key": {"p": "5258011929993026890", "f": "⭐"}
                     p = premium custom_emoji_id,  f = fallback unicode char
  - Legacy HTML tag (auto-migrated on load):
                     "key": "<tg-emoji emoji-id=\"...\">⭐</tg-emoji>"
"""
import json
import os
import re

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "emoji_config.json")
BUTTON_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "button_config.json")

DEFAULTS = {
    # Main menu
    "menu_profile": "👤",
    "menu_product": "📦",
    "menu_wallet": "💰",
    "menu_myorder": "📋",
    "welcome": "🎉",
    "back": "🔙",
    # Sections
    "profile": "👤",
    "product": "📦",
    "wallet": "💰",
    "orders": "📋",
    "category": "📂",
    "deposit": "💵",
    # Admin
    "admin": "🛠",
    "dashboard": "📊",
    "categories": "📂",
    "products": "📦",
    "stock": "📥",
    "promos": "🎟",
    "users": "👥",
    "reports": "📈",
    # Actions
    "add": "➕",
    "edit": "✏️",
    "delete": "🗑",
    "view": "📋",
    "replace": "🔄",
    "buy": "🛒",
    "apply_promo": "🎟",
    "refresh": "🔄",
    "search": "🔍",
    "close": "🔙",
    "cancel": "❌",
    "confirm": "✅",
    # Status
    "success": "✅",
    "error": "❌",
    "warning": "⚠️",
    "info": "ℹ️",
    "ban": "🚫",
    "unban": "✅",
    "active": "✅",
    "dm": "📩",
    "balance_add": "💰",
    "balance_deduct": "💸",
    "stock_label": "📦",
    "price_label": "💰",
    "id_label": "🆔",
    "name_label": "📛",
    "username_label": "🔗",
    "balance_label": "💰",
    "order_label": "🛍",
    "out_of_stock": "📦",
    "in_stock": "📦",
    "delivery": "📦",
    "timer": "⏳",
    "expired": "⏰",
    "promo_code": "🎟",
    # Quantity & deposit
    "qty_minus": "➖",
    "qty_plus": "➕",
    "qty_custom": "🔢",
    "deposit_custom": "✏️",
    # Notifications & files
    "restock_alert": "📢",
    "file_download": "📄",
    "order_detail": "📋",
    "description": "📝",
    # Stock modes
    "unlimited": "♾️",
    # Payment methods
    "pay_wallet": "💳",
    "pay_bakong": "🏦",
}

# Sections grouping for admin UI
SECTIONS = {
    "🏠 Main Menu": ["menu_profile", "menu_product", "menu_wallet", "menu_myorder", "welcome", "back"],
    "👤 Profile": ["profile", "id_label", "name_label", "username_label", "balance_label", "order_label"],
    "📦 Product": ["product", "category", "stock_label", "price_label", "buy", "apply_promo", "out_of_stock", "in_stock", "delivery", "description", "qty_minus", "qty_plus", "qty_custom", "unlimited"],
    "💰 Wallet": ["wallet", "deposit", "deposit_custom", "balance_add", "balance_deduct", "timer", "expired"],
    "💳 Payment": ["pay_wallet", "pay_bakong"],
    "📋 Orders": ["orders", "order_detail", "file_download"],
    "🛠 Admin": ["admin", "dashboard", "categories", "products", "stock", "promos", "users", "reports"],
    "⚡ Actions": ["add", "edit", "delete", "view", "replace", "refresh", "search", "close", "cancel", "confirm", "dm"],
    "📊 Status": ["success", "error", "warning", "info", "ban", "unban", "active"],
    "🎟 Promo": ["promo_code"],
}

# Friendly labels for admin display
LABELS = {
    "menu_profile": "Profile Button",
    "menu_product": "Product Button",
    "menu_wallet": "Wallet Button",
    "menu_myorder": "My Order Button",
    "welcome": "Welcome Message",
    "back": "Back Button",
    "profile": "Profile Section",
    "product": "Product Section",
    "wallet": "Wallet Section",
    "orders": "Orders Section",
    "category": "Category Default",
    "deposit": "Deposit",
    "admin": "Admin Panel",
    "dashboard": "Dashboard",
    "categories": "Categories",
    "products": "Products",
    "stock": "Stock",
    "promos": "Promo Codes",
    "users": "Users",
    "reports": "Reports",
    "add": "Add",
    "edit": "Edit",
    "delete": "Delete",
    "view": "View",
    "replace": "Replace",
    "buy": "Buy Button",
    "apply_promo": "Apply Promo",
    "refresh": "Refresh",
    "search": "Search",
    "close": "Close",
    "cancel": "Cancel",
    "confirm": "Confirm",
    "success": "Success",
    "error": "Error",
    "warning": "Warning",
    "info": "Info",
    "ban": "Ban",
    "unban": "Unban",
    "active": "Active",
    "dm": "Send DM",
    "balance_add": "Add Balance",
    "balance_deduct": "Deduct Balance",
    "stock_label": "Stock Label",
    "price_label": "Price Label",
    "id_label": "ID Label",
    "name_label": "Name Label",
    "username_label": "Username Label",
    "balance_label": "Balance Label",
    "order_label": "Order Label",
    "out_of_stock": "Out of Stock",
    "in_stock": "In Stock",
    "unlimited": "Unlimited Stock",
    "delivery": "Delivery",
    "timer": "Timer",
    "expired": "Expired",
    "promo_code": "Promo Code",
    "qty_minus": "Decrease Qty (-)",
    "qty_plus": "Increase Qty (+)",
    "qty_custom": "Custom Quantity",
    "deposit_custom": "Custom Deposit",
    "restock_alert": "Restock Alert",
    "file_download": "File Download",
    "order_detail": "Order Detail",
    "description": "Description",
    "pay_wallet": "Pay with Wallet",
    "pay_bakong": "Pay with Bakong",
}


# ---------------------------------------------------------------------------
# Legacy HTML tag parser
# ---------------------------------------------------------------------------
_LEGACY_TAG_RE = re.compile(r'<tg-emoji\s+emoji-id="(\d+)"[^>]*>([^<]*)</tg-emoji>')

# ---------------------------------------------------------------------------
# Universal emoji parser — handles DB emoji strings in any format
# ---------------------------------------------------------------------------
def parse_db_emoji(raw) -> tuple[str, str | None]:
    """
    Parse an emoji string from the database (categories/products).
    Supports 3 formats:
      - Plain unicode: "⭐" → ("⭐", None)
      - Premium HTML: '<tg-emoji emoji-id="123">⭐</tg-emoji>' → ("⭐", "123")
      - Premium JSON: '{"p":"123","f":"⭐"}' → ("⭐", "123")
    Returns (fallback_char, premium_custom_emoji_id_or_None).
    Fallback is guaranteed non-empty (Telegram rejects empty <tg-emoji> text).
    """
    if not raw:
        return "📦", None

    s = str(raw)

    # 1) Premium JSON dict format: {"p": "...", "f": "..."}
    if isinstance(raw, dict) and "p" in raw:
        return (raw.get("f") or "⭐"), str(raw["p"])

    # 2) Legacy <tg-emoji> HTML string
    m = _LEGACY_TAG_RE.search(s)
    if m:
        return (m.group(2) or "⭐"), m.group(1)

    # 3) JSON string of dict
    if s.startswith("{") and s.endswith("}"):
        try:
            d = json.loads(s)
            if isinstance(d, dict) and "p" in d:
                return (d.get("f") or "⭐"), str(d["p"])
        except (json.JSONDecodeError, TypeError):
            pass

    # 4) Plain unicode
    return s[:4] if s else "📦", None


def emoji_for_html(raw) -> str:
    """
    Render a DB emoji for HTML message text (parse_mode="HTML").
    Premium emojis become <tg-emoji> tags; plain emojis stay as-is.
    """
    plain, premium_id = parse_db_emoji(raw)
    if premium_id:
        # Telegram rejects empty <tg-emoji>...</tg-emoji>; guard against blank fallback.
        return f'<tg-emoji emoji-id="{premium_id}">{plain or "⭐"}</tg-emoji>'
    return plain or "📦"


def emoji_for_button(raw) -> str:
    """
    Render a DB emoji for button text.
    Returns the plain fallback character only (premium tags don't work in buttons).
    """
    plain, _ = parse_db_emoji(raw)
    return plain


def emoji_premium_id(raw) -> str | None:
    """
    Get the premium custom_emoji_id from a DB emoji, or None.
    Use for InlineKeyboardButton icon_custom_emoji_id.
    """
    _, premium_id = parse_db_emoji(raw)
    return premium_id


def _normalize_value(value) -> dict | str:
    """
    Normalize a stored emoji value to the current format.
    - If it's a dict with 'p' key → premium emoji (already normalized)
    - If it's a legacy <tg-emoji> HTML string → convert to {"p": id, "f": fallback}
    - Otherwise → plain unicode string
    Returns the normalized value (dict for premium, str for plain).
    """
    if isinstance(value, dict):
        if "p" in value:
            return {"p": str(value["p"]), "f": (value.get("f") or "⭐")}
        return value
    if isinstance(value, str) and value.startswith("<tg-emoji"):
        m = _LEGACY_TAG_RE.search(value)
        if m:
            return {"p": m.group(1), "f": (m.group(2) or "⭐")}
    return value


_sb_client = None


def _sb() -> object | None:
    """Return a cached Supabase client, or None if unavailable."""
    global _sb_client
    if _sb_client is not None:
        return _sb_client
    try:
        from config import SUPABASE_URL, SUPABASE_KEY
        if not SUPABASE_URL:
            return None
        from supabase import create_client
        _sb_client = create_client(SUPABASE_URL, SUPABASE_KEY)
        return _sb_client
    except Exception:
        return None


def _supabase_store(key: str, data: dict) -> None:
    """Store a JSON-serializable dict to Supabase bot_settings using upsert."""
    s = _sb()
    if not s:
        return
    try:
        # Upsert (single atomic op) — safer than delete+insert which can end up empty on partial failure.
        s.table('bot_settings').upsert(
            {'key': key, 'value': json.dumps(data, ensure_ascii=False)},
            on_conflict='key'
        ).execute()
    except Exception:
        pass  # Supabase unavailable, local file is fallback


def _supabase_load(key: str) -> dict | None:
    """Load a JSON dict from Supabase bot_settings."""
    s = _sb()
    if not s:
        return None
    try:
        r = s.table('bot_settings').select('value').eq('key', key).execute()
        if r.data:
            return json.loads(r.data[0]['value'])
    except Exception:
        pass
    return None


def _load() -> dict:
    """Load emoji config from Supabase (primary) or local JSON (fallback)."""
    # Load local file first so we can merge it as a safety net
    local_data = {}
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                local_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            local_data = {}

    # Try Supabase (cloud source of truth)
    sb_data = _supabase_load("emoji_config")

    if sb_data:
        # Supabase has data — use it, then sync to local file
        data = sb_data
        try:
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(sb_data, f, ensure_ascii=False, indent=2)
        except IOError:
            pass
    elif local_data:
        # Supabase empty/unreachable — use local file AND push it back to Supabase
        # so a fresh server restart won't start from DEFAULTS.
        data = local_data
        _supabase_store("emoji_config", local_data)
    else:
        data = {}

    # Merge: use saved values, fall back to defaults, then normalize all
    merged = dict(DEFAULTS)
    merged.update(data)

    # Normalize any legacy <tg-emoji> strings to the new object format
    changed = False
    for k, v in merged.items():
        normalized = _normalize_value(v)
        if normalized != v:
            merged[k] = normalized
            changed = True

    # Auto-save if we migrated legacy entries
    if changed:
        _save(merged)

    return merged


def _save(data: dict) -> None:
    """Save emoji config to both Supabase and local file."""
    # Local file (always works)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    # Supabase (cloud backup)
    _supabase_store("emoji_config", data)


# Singleton cache
_emoji_cache: dict | None = None


def get_all() -> dict:
    """Return all current emojis (merged with defaults)."""
    global _emoji_cache
    if _emoji_cache is None:
        _emoji_cache = _load()
    return _emoji_cache


def get(name: str) -> str:
    """
    Get an emoji as a string ready for HTML messages.
    For premium emojis, returns the <tg-emoji> HTML tag (renders natively in Telegram).
    For plain unicode, returns the character as-is.
    """
    raw = get_all().get(name, DEFAULTS.get(name, "❓"))
    if isinstance(raw, dict) and "p" in raw:
        # Premium emoji → return HTML tag for rendering in messages.
        # Fallback must be non-empty or Telegram raises Entity_text_invalid.
        fallback = raw.get("f") or DEFAULTS.get(name) or "⭐"
        return f'<tg-emoji emoji-id="{raw["p"]}">{fallback}</tg-emoji>'
    return str(raw) if raw else (DEFAULTS.get(name) or "❓")


def get_plain(name: str) -> str:
    """
    Get the plain-text fallback character for buttons and non-HTML contexts.
    For premium emojis, returns the fallback unicode character.
    For plain unicode, returns as-is.
    """
    raw = get_all().get(name, DEFAULTS.get(name, "❓"))
    if isinstance(raw, dict) and "p" in raw:
        return raw.get("f") or DEFAULTS.get(name) or "⭐"
    return str(raw) if raw else (DEFAULTS.get(name) or "❓")


def get_premium_id(name: str) -> str | None:
    """
    Get the Telegram custom_emoji_id if this is a premium emoji.
    Returns None for plain unicode emojis.
    Use this for InlineKeyboardButton icon_custom_emoji_id.
    """
    raw = get_all().get(name, DEFAULTS.get(name, "❓"))
    if isinstance(raw, dict) and "p" in raw:
        return raw["p"]
    return None


def set_emoji(name: str, value) -> None:
    """
    Set a custom emoji and persist.
    value can be:
      - A dict: {"p": "custom_emoji_id", "f": "⭐"} for premium emoji
      - A string: plain unicode emoji (e.g. "⭐") or legacy <tg-emoji> HTML
    """
    global _emoji_cache
    data = _load()
    data[name] = _normalize_value(value)
    _save(data)
    _emoji_cache = data


def reset(name: str) -> None:
    """Reset an emoji to its default."""
    global _emoji_cache
    data = _load()
    data.pop(name, None)
    if name in DEFAULTS:
        data[name] = DEFAULTS[name]
    _save(data)
    _emoji_cache = data


def reset_all() -> None:
    """Reset all emojis to defaults."""
    global _emoji_cache
    _save(dict(DEFAULTS))
    _emoji_cache = dict(DEFAULTS)


def reload() -> None:
    """Force reload from disk."""
    global _emoji_cache
    _emoji_cache = None


def reload_button_cache() -> None:
    """Force reload button config from disk."""
    global _button_cache
    _button_cache = None


def load_button_config() -> dict:
    """Load button configs (style and custom emoji ID) from JSON."""
    global _button_cache
    if _button_cache is not None:
        return _button_cache

    # Load local file first as a safety net
    local_data = None
    if os.path.exists(BUTTON_CONFIG_PATH):
        try:
            with open(BUTTON_CONFIG_PATH, "r", encoding="utf-8") as f:
                local_data = json.load(f)
        except (json.JSONDecodeError, IOError):
            local_data = None

    # Try Supabase (cloud source of truth)
    sb_data = _supabase_load("button_config")

    if sb_data:
        _button_cache = sb_data
        try:
            with open(BUTTON_CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(sb_data, f, ensure_ascii=False, indent=2)
        except IOError:
            pass
        return _button_cache

    if local_data:
        # Supabase empty/unreachable — use local file AND push it back
        _button_cache = local_data
        _supabase_store("button_config", local_data)
        return _button_cache

    # Default configs for the main menu buttons
    _button_cache = {
        "menu_profile": {"text": "Profile", "icon_custom_emoji_id": None, "style": None},
        "menu_product": {"text": "Product", "icon_custom_emoji_id": None, "style": None},
        "menu_wallet": {"text": "Wallet", "icon_custom_emoji_id": None, "style": None},
        "menu_myorder": {"text": "My Order", "icon_custom_emoji_id": None, "style": None}
    }
    return _button_cache


_button_cache: dict | None = None


def save_button_config(config: dict) -> None:
    """Save button configs to both Supabase and local JSON."""
    global _button_cache
    # Local file
    with open(BUTTON_CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    # Supabase cloud backup
    _supabase_store("button_config", config)
    _button_cache = config


# ---------------------------------------------------------------------------
# Button Style Manager — Sections, Labels, Helpers
# ---------------------------------------------------------------------------
BTN_SECTIONS = {
    "🏠 Main Menu": ["menu_profile", "menu_product", "menu_wallet", "menu_myorder"],
    "🛒 Shop Actions": ["buy", "apply_promo", "deposit", "deposit_custom", "qty_minus", "qty_plus", "qty_custom"],
    "💳 Payment": ["pay_wallet", "pay_bakong"],
    "↩️ Navigation": ["back", "cancel", "confirm"],
    "📦 Stock Status": ["in_stock", "out_of_stock", "low_stock"],
    "📋 Orders": ["order_detail"],
}

BTN_LABELS = {
    "menu_profile": "Profile Button",
    "menu_product": "Shop Now Button",
    "menu_wallet": "Wallet Button",
    "menu_myorder": "My Order Button",
    "buy": "Buy Button",
    "apply_promo": "Apply Promo Button",
    "deposit": "Deposit Button",
    "deposit_custom": "Custom Deposit Button",
    "back": "Back Button",
    "cancel": "Cancel Button",
    "confirm": "Confirm Button",
    "in_stock": "In Stock",
    "out_of_stock": "Out of Stock",
    "low_stock": "Low Stock",
    "qty_minus": "Qty Minus (-) Button",
    "qty_plus": "Qty Plus (+) Button",
    "qty_custom": "Qty Custom Button",
    "order_detail": "Order Detail Button",
    "pay_wallet": "Pay with Wallet Button",
    "pay_bakong": "Pay with Bakong Button",
}

BTN_STYLE_LABELS = {
    "primary": "🔵 Blue",
    "success": "🟢 Green",
    "danger": "🔴 Red",
    None: "⚪ Default",
}

VALID_BTN_STYLES = ["primary", "success", "danger"]


def get_button_style(key: str) -> str | None:
    """Get the style for a button key from config."""
    cfg = load_button_config()
    entry = cfg.get(key)
    if isinstance(entry, dict):
        return entry.get("style")
    return None


def set_button_style(key: str, style: str | None) -> None:
    """Set the style for a button key and persist."""
    cfg = load_button_config()
    if key not in cfg:
        cfg[key] = {"text": None, "icon_custom_emoji_id": None, "style": None}
    cfg[key]["style"] = style
    save_button_config(cfg)


def reset_button_style(key: str) -> None:
    """Reset a button style to null."""
    set_button_style(key, None)
