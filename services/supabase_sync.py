"""
Backup & Restore — Cloud backup for Rat Store bot.
All data lives in Supabase (PostgreSQL). Backup = export to JSON file.
Restore = import from JSON backup file back to Supabase.
"""
import io
import json
import logging
import os
from datetime import datetime

from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

# Tables to include in backup/restore
BACKUP_TABLES = [
    "categories",
    "products",
    "stock",
    "users",
    "orders",
    "payments",
    "promo_codes",
    "bot_settings",
]

# Delete order — children first to satisfy FK constraints
DELETE_ORDER = [
    "orders",       # FK → users, products
    "payments",     # FK → users
    "stock",        # FK → products
    "products",     # FK → categories
    "categories",   # parent
    "users",        # parent (referenced by orders, payments)
    "promo_codes",  # no FKs
    "bot_settings", # no FKs
]

# Primary key column per table (for the "delete all" hack)
TABLE_PK = {
    "categories":   "id",
    "products":     "id",
    "stock":        "id",
    "users":        "user_id",
    "orders":       "id",
    "payments":     "id",
    "promo_codes":  "id",
    "bot_settings": "key",
}


def get_supabase():
    """Get a Supabase client (lazy import)."""
    from supabase import create_client
    return create_client(SUPABASE_URL, SUPABASE_KEY)


# ===========================================================================
# BACKUP — Export all Supabase data to JSON
# ===========================================================================
def create_backup() -> dict:
    """
    Export all Supabase tables to a JSON-serializable dict.
    Returns {"success": True, "data": {...}, "timestamp": "..."} or error dict.
    """
    supabase = get_supabase()
    backup_data = {}
    timestamp = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    for table in BACKUP_TABLES:
        try:
            result = supabase.table(table).select("*").execute()
            rows = result.data if result.data else []
            backup_data[table] = rows
        except Exception as e:
            logger.warning(f"Backup: failed to read table {table}: {e}")
            backup_data[table] = []

    return {
        "success": True,
        "timestamp": timestamp,
        "table_count": len(BACKUP_TABLES),
        "total_rows": sum(len(v) for v in backup_data.values()),
        "data": backup_data,
    }


def backup_to_file() -> str:
    """
    Create a backup JSON file and return its path.
    The file can be downloaded/sent via Telegram.
    """
    result = create_backup()
    if not result["success"]:
        raise RuntimeError("Backup failed: " + result.get("error", "Unknown"))

    backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "backups")
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ratstore_backup_{timestamp}.json"
    filepath = os.path.join(backup_dir, filename)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(result["data"], f, ensure_ascii=False, indent=2, default=str)

    logger.info(f"Backup saved: {filepath} ({result['total_rows']} rows)")
    return filepath


def backup_to_bytesio() -> io.BytesIO:
    """
    Create a backup JSON in memory and return as BytesIO (for Telegram upload).
    Returns (BytesIO, timestamp_str) tuple.
    """
    result = create_backup()
    if not result["success"]:
        raise RuntimeError("Backup failed: " + result.get("error", "Unknown"))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"ratstore_backup_{timestamp}.json"

    json_bytes = json.dumps(result["data"], ensure_ascii=False, indent=2, default=str).encode("utf-8")
    buf = io.BytesIO(json_bytes)
    buf.name = filename

    return buf, timestamp, result["total_rows"]


# ===========================================================================
# RESTORE — Import from JSON backup back to Supabase
# ===========================================================================
def restore_from_backup(backup_dict: dict) -> dict:
    """
    Restore Supabase tables from a backup dict (the "data" portion of a backup).
    WARNING: This DELETES all existing data in each table before restoring!
    Returns {"success": True, "results": {...}} or error dict.
    """
    supabase = get_supabase()
    results = {}

    # ── Phase 1: DELETE all rows in reverse-dependency order ──
    for table in DELETE_ORDER:
        pk = TABLE_PK.get(table, "id")
        try:
            # Delete all rows by matching on a non-existent PK value
            # For numeric PKs: .neq(pk, -1) deletes all (since PK is never negative)
            # For text PKs: .neq(pk, "___NONEXISTENT___") deletes all
            sentinel = -1 if pk != "key" else "___NONEXISTENT___"
            supabase.table(table).delete().neq(pk, sentinel).execute()
            results.setdefault("_deletes", {})[table] = "deleted"
        except Exception as e:
            logger.warning(f"Restore: delete failed for {table}: {e}")
            # Try truncate via upsert with empty filter (may still fail on FK)
            results.setdefault("_deletes", {})[table] = f"delete_warn: {str(e)[:80]}"

    # ── Phase 2: INSERT backup rows in dependency order ──
    for table in BACKUP_TABLES:
        rows = backup_dict.get(table, [])
        if not rows:
            results[table] = "empty (skipped)"
            continue

        try:
            batch_size = 50  # smaller batches to avoid timeouts
            for i in range(0, len(rows), batch_size):
                batch = rows[i:i + batch_size]
                supabase.table(table).insert(batch).execute()

            results[table] = f"restored {len(rows)} rows"
        except Exception as e:
            logger.error(f"Restore failed for table {table}: {e}")
            results[table] = f"failed: {str(e)[:100]}"

    success_count = sum(1 for v in results.values()
                        if isinstance(v, str) and v.startswith("restored"))
    results.pop("_deletes", None)  # internal key, not for display
    return {
        "success": success_count > 0,
        "results": results,
        "restored_tables": success_count,
    }


def restore_from_file(filepath: str) -> dict:
    """Restore from a local backup JSON file."""
    if not os.path.exists(filepath):
        return {"success": False, "error": "Backup file not found"}

    with open(filepath, "r", encoding="utf-8") as f:
        backup_dict = json.load(f)

    return restore_from_backup(backup_dict)


def restore_from_bytesio(file_bytes: bytes) -> dict:
    """Restore from uploaded backup file bytes."""
    try:
        backup_dict = json.loads(file_bytes.decode("utf-8"))
        return restore_from_backup(backup_dict)
    except json.JSONDecodeError as e:
        return {"success": False, "error": f"Invalid backup file: {e}"}
