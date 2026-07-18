import os

from dotenv import load_dotenv

# Load .env from the same directory as this config file (not CWD)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
# Only this Telegram user may access admin + backup info
ADMIN_ID = int(os.getenv("ADMIN_ID", "7322712989") or "7322712989")

# ---------------------------------------------------------------------------
# Bakong KHQR (legacy SDK — kept for backward compatibility)
# ---------------------------------------------------------------------------
BAKONG_API_TOKEN = os.getenv("BAKONG_API_TOKEN", "")
BAKONG_ACCOUNT = os.getenv("BAKONG_ACCOUNT", "")
MERCHANT_NAME = os.getenv("MERCHANT_NAME", "")
MERCHANT_PHONE = os.getenv("MERCHANT_PHONE", "")

# Bakong API endpoints (fallback if SDK fails)
BAKONG_CREATE_QR_URL = "https://api-bakong.nbc.gov.kh/v1/khqr/create"
BAKONG_CHECK_PAYMENT_URL = "https://api-bakong.nbc.gov.kh/v1/khqr/check"

# ---------------------------------------------------------------------------
# KHQRPay Payment Gateway (khqr.cc) — primary payment method
# Supports Bakong KHQR + ABA Pay + Binance via unified checkout
# Admin can override these via bot_settings → 💳 Payment Gateway
# ---------------------------------------------------------------------------
KHQRPAY_PROFILE_ID = os.getenv("KHQRPAY_PROFILE_ID", "")
KHQRPAY_SECRET_KEY = os.getenv("KHQRPAY_SECRET_KEY", "")
KHQRPAY_ABA_URL = os.getenv("KHQRPAY_ABA_URL", "")

# ---------------------------------------------------------------------------
# Payment settings
# ---------------------------------------------------------------------------
QR_EXPIRE_SECONDS = 180      # QR code expires in 3 minutes
PAYMENT_CHECK_INTERVAL = 5   # Check payment every 5 seconds
DEPOSIT_AMOUNTS = [1, 5, 10, 20, 50, 100]  # USD amounts for quick deposit

# ---------------------------------------------------------------------------
# Notification Groups
# ---------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default

# Orders (product purchased) → order alert group
ORDER_GROUP_ID = _env_int("ORDER_GROUP_ID", -1003729530722)
# Deposits / payments completed → payment alert group
PAYMENT_GROUP_ID = _env_int("PAYMENT_GROUP_ID", -1004352483292)
# New users joining the bot
NEW_USER_GROUP_ID = _env_int("NEW_USER_GROUP_ID", -1004490564374)

# Support contact
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Ratofficer")

# ---------------------------------------------------------------------------
# Webhook (Railway Web Service — eliminates 409 Conflict)
# Set this on Railway to your app's public URL. Leave empty for polling.
# ---------------------------------------------------------------------------
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
WEBHOOK_PORT = int(os.getenv("PORT", "8080"))
WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")

# ---------------------------------------------------------------------------
# Supabase (Cloud Database)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
