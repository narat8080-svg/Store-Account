"""
Deploy bot to server via SFTP (with retry).
Usage: python scripts/deploy.py

───────────────────────────────────────────────────────────
 IMPORTANT: What gets deployed vs what stays on server
───────────────────────────────────────────────────────────
  DEPLOYED (code files):
    bot.py, config.py, requirements.txt, supabase_schema.sql
    admin/__init__.py
    services/database.py, services/payment.py,
    services/khqrpay.py, services/supabase_sync.py
    utils/emoji_manager.py
    scripts/supabase_setup.py

  NEVER DEPLOYED (server-customized — persists across updates):
    emoji_config.json    ← custom emojis set via admin panel
    button_config.json   ← button colors set via admin panel

  OPTIONAL:
    .env                 ← credentials (deployed only if present)
───────────────────────────────────────────────────────────
"""
import os
import sys
import time
import paramiko

# Add parent directory to path for local run support
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ===========================================================================
# Server Connection
# ===========================================================================
HOST = "node2.serverix.cloud"
PORT = 2022
USERNAME = "narat_nyfy.b91aad0f"
PASSWORD = "Narat(5656)"
REMOTE_DIR = "."

# ===========================================================================
# Code files to deploy (Python source + configs only)
# ===========================================================================
CODE_FILES = [
    "bot.py",
    "config.py",
    "requirements.txt",
    "supabase_schema.sql",
    "admin/__init__.py",
    "services/database.py",
    "services/payment.py",
    "services/khqrpay.py",
    "services/supabase_sync.py",
    "utils/emoji_manager.py",
    "scripts/supabase_setup.py",
]

# ===========================================================================
# Server-persistent files (NEVER overwritten by deploy)
# ===========================================================================
#   emoji_config.json   — customized via Admin → Customize
#   button_config.json  — customized via Admin → Button Styles
#   data/               — runtime data directory

DIRS = ["data", "admin", "services", "utils", "scripts"]


def deploy():
    # ── Connect with retry ──────────────────────────────────────────
    for attempt in range(1, 6):
        try:
            print(f"Connecting to {HOST}:{PORT} (attempt {attempt})...")
            transport = paramiko.Transport((HOST, PORT))
            transport.connect(username=USERNAME, password=PASSWORD)
            sftp = paramiko.SFTPClient.from_transport(transport)
            break
        except Exception as e:
            print(f"  Failed: {e}")
            if attempt < 5:
                print(f"  Retrying in 5s...")
                time.sleep(5)
            else:
                print("  All attempts failed.")
                return

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # ── Create remote directories if needed ─────────────────────────
    for d in DIRS:
        remote_path = f"{REMOTE_DIR}/{d}"
        try:
            sftp.stat(remote_path)
        except FileNotFoundError:
            sftp.mkdir(remote_path)
            print(f"  Created: {remote_path}")

    # Upload code files
    print("-- Code files --")
    for f in CODE_FILES:
        local = os.path.join(base_dir, f)
        remote = f"{REMOTE_DIR}/{f}"
        if os.path.exists(local):
            sftp.put(local, remote)
            print(f"  Uploaded: {f}")
        else:
            print(f"  SKIP (not found): {f}")

    # ── Upload .env (credentials) ───────────────────────────────────
    env_path = os.path.join(base_dir, ".env")
    if os.path.exists(env_path):
        sftp.put(env_path, f"{REMOTE_DIR}/.env")
        print(f"  Uploaded: .env")

    # Bootstrap config files (upload ONLY if missing on server)
    # Never overwrites - if server already has custom versions they persist.
    print("-- Config files (upload only if missing on server) --")
    for cfg in ("emoji_config.json", "button_config.json"):
        local = os.path.join(base_dir, cfg)
        remote = f"{REMOTE_DIR}/{cfg}"
        if not os.path.exists(local):
            print(f"  SKIP (no local): {cfg}")
            continue
        try:
            sftp.stat(remote)
            print(f"  KEEP (server has it): {cfg}")
        except FileNotFoundError:
            sftp.put(local, remote)
            print(f"  Bootstrapped: {cfg}")

    sftp.close()
    transport.close()

    print(f"\nDeploy complete! -> {HOST}:{REMOTE_DIR}")
    print(f"Restart bot on server:")
    print(f"  pip install -r requirements.txt && python bot.py")


if __name__ == "__main__":
    deploy()
