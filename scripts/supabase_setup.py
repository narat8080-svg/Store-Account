"""
Supabase Setup — Run this ONCE to create all tables on Supabase.
Usage: python supabase_setup.py
"""
import sys
import os
# Add parent directory to path so we can import config and services
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from config import SUPABASE_URL
from services.supabase_sync import get_supabase

def setup():
    """Create all tables on Supabase by running the schema SQL."""
    supabase = get_supabase()
    
    # Read the SQL schema file
    schema_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "supabase_schema.sql")
    with open(schema_path, "r", encoding="utf-8") as f:
        sql = f.read()

    # Split by semicolons and execute each statement
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    
    print(f"Connecting to: {SUPABASE_URL}")
    print(f"Executing {len(statements)} SQL statements...\n")

    for i, stmt in enumerate(statements, 1):
        # Skip pure comments
        if stmt.startswith("--"):
            continue
        try:
            # Use raw SQL via REST — Supabase Python client supports .rpc() or raw SQL via management API
            # For DDL, we use the Supabase Management API SQL endpoint
            result = supabase.rpc("run_sql", {"query": stmt}).execute()
            print(f"  [{i}/{len(statements)}] OK: {stmt.split()[0]} {stmt.split()[1] if len(stmt.split())>1 else ''}...")
        except Exception as e:
            # Try alternative: use supabase.sql() if available, or just log
            err_msg = str(e)[:100]
            # Many statements like CREATE INDEX IF NOT EXISTS may fail on unique violations — that's OK
            if "already exists" in err_msg.lower() or "duplicate" in err_msg.lower():
                print(f"  [{i}/{len(statements)}] SKIP (already exists): {stmt.split()[0]}...")
            else:
                print(f"  [{i}/{len(statements)}] WARN: {err_msg}")
                # Try raw SQL via REST API directly
                try:
                    import httpx
                    resp = httpx.post(
                        f"{SUPABASE_URL}/rest/v1/rpc/exec_sql",
                        headers={
                            "apikey": supabase.supabase_key,
                            "Authorization": f"Bearer {supabase.supabase_key}",
                            "Content-Type": "application/json",
                        },
                        json={"query": stmt},
                        timeout=30,
                    )
                    if resp.status_code == 200:
                        print(f"  [{i}/{len(statements)}] OK (via REST)")
                    else:
                        print(f"  [{i}/{len(statements)}] FAIL: {resp.status_code} {resp.text[:100]}")
                except Exception as e2:
                    print(f"  [{i}/{len(statements)}] FAIL: {e2}")

    print("\n✅ Setup complete! Tables should now exist on Supabase.")
    print("   Verify at: https://supabase.com/dashboard/project/stgnulkjqjvodvnwuitu")


if __name__ == "__main__":
    setup()
