"""
Supabase diagnostic — run this to test connectivity & bot_settings permissions.
Usage: python scripts/test_supabase.py
"""
import json
import os
import sys

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

print("=" * 60)
print("Supabase Diagnostic")
print("=" * 60)
print(f"URL: {SUPABASE_URL}")
print(f"Key: {SUPABASE_KEY[:30]}...")
print()

# Test 1: Create client
print("[1] Creating Supabase client...")
try:
    from supabase import create_client
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    print("    ✅ Client created")
except Exception as e:
    print(f"    ❌ Failed: {e}")
    sys.exit(1)

# Test 2: Read from bot_settings
print("[2] Reading bot_settings...")
try:
    r = supabase.table('bot_settings').select('*').execute()
    print(f"    ✅ Read {len(r.data)} rows")
    for row in r.data:
        val_preview = str(row.get('value', ''))[:80]
        print(f"       key={row['key']}, value={val_preview}...")
except Exception as e:
    print(f"    ❌ Failed: {e}")

# Test 3: Write to bot_settings (upsert)
print("[3] Writing test key to bot_settings...")
try:
    supabase.table('bot_settings').upsert(
        {'key': '_diagnostic_test', 'value': json.dumps({"test": True, "timestamp": "now"})},
        on_conflict='key'
    ).execute()
    print("    ✅ Upsert succeeded")
except Exception as e:
    print(f"    ❌ Upsert failed: {e}")
    print()
    print("    🔧 FIX: Run this SQL in Supabase SQL Editor:")
    print("    ---------------------------------------------------")
    print("    ALTER TABLE bot_settings ENABLE ROW LEVEL SECURITY;")
    print("    DROP POLICY IF EXISTS \"Allow all\" ON bot_settings;")
    print("    CREATE POLICY \"Allow all\" ON bot_settings FOR ALL")
    print("    USING (true) WITH CHECK (true);")
    print("    ---------------------------------------------------")

# Test 4: Clean up test key
print("[4] Cleaning up test key...")
try:
    supabase.table('bot_settings').delete().eq('key', '_diagnostic_test').execute()
    print("    ✅ Cleaned up")
except Exception as e:
    print(f"    ⚠️ Cleanup failed (harmless): {e}")

# Test 5: Read emoji_config
print("[5] Reading emoji_config...")
try:
    r = supabase.table('bot_settings').select('value').eq('key', 'emoji_config').execute()
    if r.data:
        emoji_data = json.loads(r.data[0]['value'])
        premium_count = sum(1 for v in emoji_data.values() if isinstance(v, dict) and 'p' in v)
        print(f"    ✅ Found emoji_config with {len(emoji_data)} keys, {premium_count} premium emojis")
    else:
        print("    ⚠️ No emoji_config found — will be created on first admin customization")
except Exception as e:
    print(f"    ❌ Failed: {e}")

# Test 6: Read button_config
print("[6] Reading button_config...")
try:
    r = supabase.table('bot_settings').select('value').eq('key', 'button_config').execute()
    if r.data:
        btn_data = json.loads(r.data[0]['value'])
        with_icon = sum(1 for v in btn_data.values() if isinstance(v, dict) and v.get('icon_custom_emoji_id'))
        print(f"    ✅ Found button_config with {len(btn_data)} buttons, {with_icon} with premium icons")
    else:
        print("    ⚠️ No button_config found — will be created on first customization")
except Exception as e:
    print(f"    ❌ Failed: {e}")

print()
print("=" * 60)
print("Diagnostic complete. Check [3] above — if it failed, run the SQL fix.")
print("=" * 60)
