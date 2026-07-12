import os

from dotenv import load_dotenv

# Load .env from the same directory as this config file (not CWD)
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

# ---------------------------------------------------------------------------
# Telegram
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN", "8640787378:AAGYsXT5iEYysTbrfkHEmRn2aMNQYHU5yu0")
ADMIN_ID = int(os.getenv("ADMIN_ID", "7322712989"))

# ---------------------------------------------------------------------------
# Bakong KHQR
# ---------------------------------------------------------------------------
BAKONG_API_TOKEN = os.getenv("BAKONG_API_TOKEN", "rbk8nBLKCjMVAiPwoJmg1MtMfEOMseLOPaM6eqP2TbHtE4")
BAKONG_ACCOUNT = os.getenv("BAKONG_ACCOUNT", "chheak_narat@bkrt")
MERCHANT_NAME = os.getenv("MERCHANT_NAME", "NARAT CHHEAK")
MERCHANT_PHONE = os.getenv("MERCHANT_PHONE", "0975867586")

# Bakong API endpoints (fallback if SDK fails)
BAKONG_CREATE_QR_URL = "https://api-bakong.nbc.gov.kh/v1/khqr/create"
BAKONG_CHECK_PAYMENT_URL = "https://api-bakong.nbc.gov.kh/v1/khqr/check"

# ---------------------------------------------------------------------------
# Payment settings
# ---------------------------------------------------------------------------
QR_EXPIRE_SECONDS = 180      # QR code expires in 3 minutes
PAYMENT_CHECK_INTERVAL = 5   # Check payment every 5 seconds
DEPOSIT_AMOUNTS = [1, 5, 10, 20, 50, 100]  # USD amounts for quick deposit

# ---------------------------------------------------------------------------
# Notification Groups
# ---------------------------------------------------------------------------
ORDER_GROUP_ID = int(os.getenv("ORDER_GROUP_ID", "-5492405806"))
PAYMENT_GROUP_ID = int(os.getenv("PAYMENT_GROUP_ID", "-5374461237"))

# ---------------------------------------------------------------------------
# Supabase (Cloud Database)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")
