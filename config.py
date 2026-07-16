import os

from dotenv import load_dotenv

# Load .env from the same directory as this config file (not CWD)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "")
ADMIN_ID = int(os.getenv("ADMIN_ID", "0"))

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
ORDER_GROUP_ID = os.getenv("ORDER_GROUP_ID", "-1003729530722")
PAYMENT_GROUP_ID = os.getenv("PAYMENT_GROUP_ID", "-1004352483292")
NEW_USER_GROUP_ID = os.getenv("NEW_USER_GROUP_ID", "-5570553246")

# Support contact
SUPPORT_USERNAME = os.getenv("SUPPORT_USERNAME", "@Ratofficer")

# ---------------------------------------------------------------------------
# Supabase (Cloud Database)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
