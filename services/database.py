"""
Database layer — Supabase (PostgreSQL) backend.
Same function signatures as the old SQLite version.
All bot/admin code works unchanged.
"""
import logging
import sys
from config import SUPABASE_URL, SUPABASE_KEY

logger = logging.getLogger(__name__)

_supabase = None


def _get_supabase():
    global _supabase
    if _supabase is None:
        if not SUPABASE_URL or not SUPABASE_KEY:
            logger.critical(
                "SUPABASE_URL and SUPABASE_KEY must be set in environment. "
                "The bot cannot run without a database. "
                "Add them on Railway under Variables, or in your .env file."
            )
            sys.exit(1)
        from supabase import create_client
        _supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    return _supabase

class _DummyConn:
    def close(self): pass
    def commit(self): pass
    def execute(self, *a, **k): pass

def get_db():
    return _DummyConn()

def _row(d):
    return dict(d) if d else None

def _rows(data):
    return [dict(r) for r in data] if data else []

# === USERS ===
def get_or_create_user(conn, user_id, username=None, first_name=None):
    s = _get_supabase()
    r = s.table('users').select('*').eq('user_id', user_id).execute()
    if r.data:
        u = dict(r.data[0])
        if (username and u.get('username') != username) or (first_name and u.get('first_name') != first_name):
            s.table('users').update({'username': username, 'first_name': first_name}).eq('user_id', user_id).execute()
        return u
    s.table('users').insert({'user_id': user_id, 'username': username, 'first_name': first_name, 'balance': 0.0}).execute()
    r = s.table('users').select('*').eq('user_id', user_id).execute()
    return dict(r.data[0]) if r.data else {'user_id': user_id, 'balance': 0.0}

def get_user_balance(conn, user_id):
    s = _get_supabase()
    r = s.table('users').select('balance').eq('user_id', user_id).execute()
    return float(r.data[0]['balance']) if r.data else 0.0

def add_balance(conn, user_id, amount):
    s = _get_supabase()
    bal = get_user_balance(conn, user_id)
    nb = bal + amount
    s.table('users').update({'balance': nb}).eq('user_id', user_id).execute()
    return nb

def get_order_count(conn, user_id):
    s = _get_supabase()
    r = s.table('orders').select('*', count='exact').eq('user_id', user_id).execute()
    return r.count or 0

# === PAYMENTS ===
def create_payment(conn, user_id, amount, qr_text='', qr_md5=''):
    s = _get_supabase()
    r = s.table('payments').insert({'user_id': user_id, 'amount': amount, 'qr_text': qr_text, 'qr_md5': qr_md5, 'status': 'pending'}).execute()
    return r.data[0]['id'] if r.data else 0

def mark_payment_paid(conn, payment_id):
    _get_supabase().table('payments').update({'status': 'paid', 'paid_at': 'now()'}).eq('id', payment_id).execute()

def mark_payment_expired(conn, payment_id):
    _get_supabase().table('payments').update({'status': 'expired'}).eq('id', payment_id).execute()

# === CATEGORIES ===
def add_category(conn, name, emoji='📂'):
    try:
        r = _get_supabase().table('categories').insert({'name': name, 'emoji': emoji, 'is_active': 1}).execute()
        return r.data[0]['id'] if r.data else 0
    except Exception as e:
        err = str(e)
        if '23505' in err or 'duplicate key' in err.lower():
            logger.warning(f"Duplicate category name ignored: {name}")
            return 0
        raise

def get_all_categories(conn):
    r = _get_supabase().table('categories').select('*').eq('is_active', 1).order('id').execute()
    return _rows(r.data)

def get_category(conn, category_id):
    r = _get_supabase().table('categories').select('*').eq('id', category_id).execute()
    return _row(r.data[0]) if r.data else None

def delete_category(conn, category_id):
    _get_supabase().table('categories').update({'is_active': 0}).eq('id', category_id).execute()

# === PRODUCTS ===
def add_product(conn, category_id, name, price, emoji='📦', description=''):
    r = _get_supabase().table('products').insert({
        'category_id': category_id, 'name': name, 'price': price,
        'emoji': emoji, 'description': description, 'is_active': 1, 'is_unlimited': 0
    }).execute()
    return r.data[0]['id'] if r.data else 0

def get_products_by_category(conn, category_id):
    s = _get_supabase()
    r = s.table('products').select('*').eq('category_id', category_id).eq('is_active', 1).order('id').execute()
    prods = _rows(r.data)
    for p in prods:
        sr = s.table('stock').select('*', count='exact').eq('product_id', p['id']).eq('is_sold', 0).execute()
        p['stock_count'] = sr.count or 0
    return prods

def get_product(conn, product_id):
    s = _get_supabase()
    r = s.table('products').select('*').eq('id', product_id).execute()
    if not r.data: return None
    p = dict(r.data[0])
    sr = s.table('stock').select('*', count='exact').eq('product_id', p['id']).eq('is_sold', 0).execute()
    p['stock_count'] = sr.count or 0
    return p

def get_all_products(conn):
    s = _get_supabase()
    r = s.table('products').select('*').eq('is_active', 1).order('id').execute()
    prods = _rows(r.data)
    for p in prods:
        cr = s.table('categories').select('name').eq('id', p['category_id']).execute()
        p['category_name'] = cr.data[0]['name'] if cr.data else 'Unknown'
        sr = s.table('stock').select('*', count='exact').eq('product_id', p['id']).eq('is_sold', 0).execute()
        p['stock_count'] = sr.count or 0
    return prods

def delete_product(conn, product_id):
    _get_supabase().table('products').update({'is_active': 0}).eq('id', product_id).execute()

# === STOCK ===
def add_stock_bulk(conn, product_id, details):
    rows = [{'product_id': product_id, 'detail': d} for d in details]
    _get_supabase().table('stock').insert(rows).execute()
    return len(rows)

def get_stock_for_product(conn, product_id, unsold_only=True):
    s = _get_supabase()
    q = s.table('stock').select('*').eq('product_id', product_id)
    if unsold_only: q = q.eq('is_sold', 0)
    return _rows(q.order('id').execute().data)

def mark_stock_sold(conn, stock_id, order_id=None):
    _get_supabase().table('stock').update({'is_sold': 1, 'order_id': order_id}).eq('id', stock_id).execute()

def get_stock_count(conn, product_id):
    r = _get_supabase().table('stock').select('*', count='exact').eq('product_id', product_id).eq('is_sold', 0).execute()
    return r.count or 0

def delete_stock(conn, stock_id):
    _get_supabase().table('stock').delete().eq('id', stock_id).eq('is_sold', 0).execute()

# === ORDERS ===
def create_order(conn, user_id, product_id, amount, stock_id=None, promo_code=None, original_amount=None):
    r = _get_supabase().table('orders').insert({
        'user_id': user_id, 'product_id': product_id, 'amount': amount,
        'stock_id': stock_id, 'promo_code': promo_code,
        'original_amount': original_amount or amount, 'status': 'completed'
    }).execute()
    return r.data[0]['id'] if r.data else 0

def get_user_orders(conn, user_id, limit=20):
    s = _get_supabase()
    r = s.table('orders').select('*').eq('user_id', user_id).order('created_at', desc=True).limit(limit).execute()
    orders = _rows(r.data)
    for o in orders:
        if o.get('product_id'):
            pr = s.table('products').select('name,emoji').eq('id', o['product_id']).execute()
            if pr.data:
                o['product_name'] = pr.data[0].get('name','')
                o['product_emoji'] = pr.data[0].get('emoji','📦')
        if o.get('stock_id'):
            sr = s.table('stock').select('detail').eq('id', o['stock_id']).execute()
            if sr.data: o['stock_detail'] = sr.data[0].get('detail','')
    return orders

def get_orders_today(conn):
    from datetime import datetime
    today = datetime.utcnow().strftime('%Y-%m-%d')
    s = _get_supabase()
    r = s.table('orders').select('*').gte('created_at', today).order('created_at', desc=True).execute()
    orders = _rows(r.data)
    for o in orders:
        if o.get('product_id'):
            pr = s.table('products').select('name,emoji').eq('id', o['product_id']).execute()
            if pr.data: o['product_name'] = pr.data[0].get('name','')
        if o.get('user_id'):
            ur = s.table('users').select('first_name').eq('user_id', o['user_id']).execute()
            if ur.data: o['first_name'] = ur.data[0].get('first_name','')
    return orders

# === PROMO CODES ===
def create_promo_code(conn, code, discount_type, discount_value, max_uses=0, min_order=0):
    r = _get_supabase().table('promo_codes').insert({
        'code': code.upper(), 'discount_type': discount_type,
        'discount_value': discount_value, 'max_uses': max_uses, 'min_order': min_order
    }).execute()
    return r.data[0]['id'] if r.data else 0

def get_promo_code(conn, code):
    r = _get_supabase().table('promo_codes').select('*').eq('code', code.upper()).eq('is_active', 1).execute()
    return _row(r.data[0]) if r.data else None

def use_promo_code(conn, code_id):
    s = _get_supabase()
    r = s.table('promo_codes').select('current_uses').eq('id', code_id).execute()
    if r.data:
        s.table('promo_codes').update({'current_uses': r.data[0].get('current_uses',0)+1}).eq('id', code_id).execute()

def get_all_promo_codes(conn):
    return _rows(_get_supabase().table('promo_codes').select('*').eq('is_active', 1).order('id').execute().data)

def delete_promo_code(conn, promo_id):
    _get_supabase().table('promo_codes').update({'is_active': 0}).eq('id', promo_id).execute()

def calculate_discount(promo, amount):
    if promo['discount_type'] == 'flat':
        discount = min(promo['discount_value'], amount)
    else:
        discount = round(amount * promo['discount_value'] / 100, 2)
    return discount, max(round(amount - discount, 2), 0)

# === DASHBOARD ===
def get_dashboard_stats(conn):
    s = _get_supabase()
    today_orders = get_orders_today(conn)
    total_users = s.table('users').select('*', count='exact').execute().count or 0
    total_products = s.table('products').select('*', count='exact').eq('is_active', 1).execute().count or 0
    total_stock = s.table('stock').select('*', count='exact').eq('is_sold', 0).execute().count or 0
    ur = s.table('users').select('balance').execute()
    total_balance = sum(float(u.get('balance',0)) for u in (ur.data or []))
    pr = s.table('payments').select('amount').eq('status','paid').execute()
    total_deposits = sum(float(p.get('amount',0)) for p in (pr.data or []))
    return {
        'total_users': total_users, 'total_balance': total_balance,
        'total_products': total_products, 'total_stock': total_stock,
        'total_deposits': total_deposits, 'today_orders': len(today_orders),
        'today_revenue': sum(o['amount'] for o in today_orders), 'top_product': 'N/A'
    }

# === EDIT ===
def update_category(conn, cat_id, name=None, emoji=None):
    d = {}
    if name: d['name'] = name
    if emoji: d['emoji'] = emoji
    if d: _get_supabase().table('categories').update(d).eq('id', cat_id).execute()

def update_product(conn, prod_id, name=None, price=None, emoji=None, category_id=None):
    d = {}
    if name is not None: d['name'] = name
    if price is not None: d['price'] = price
    if emoji is not None: d['emoji'] = emoji
    if category_id is not None: d['category_id'] = category_id
    if d: _get_supabase().table('products').update(d).eq('id', prod_id).execute()

def set_product_unlimited(conn, product_id, is_unlimited=True):
    """Set a product to unlimited stock mode (no individual stock items needed)."""
    _get_supabase().table('products').update({'is_unlimited': 1 if is_unlimited else 0}).eq('id', product_id).execute()

def update_stock_detail(conn, stock_id, detail):
    _get_supabase().table('stock').update({'detail': detail}).eq('id', stock_id).eq('is_sold', 0).execute()

def replace_all_stock(conn, product_id, details):
    s = _get_supabase()
    s.table('stock').delete().eq('product_id', product_id).eq('is_sold', 0).execute()
    rows = [{'product_id': product_id, 'detail': d} for d in details]
    s.table('stock').insert(rows).execute()
    return len(rows)

def get_stock_item(conn, stock_id):
    r = _get_supabase().table('stock').select('*').eq('id', stock_id).execute()
    return _row(r.data[0]) if r.data else None

# === USER MANAGEMENT ===
def get_all_users(conn, include_banned=False):
    q = _get_supabase().table('users').select('*')
    if not include_banned: q = q.eq('is_banned', 0)
    return _rows(q.order('created_at', desc=True).execute().data)

def get_user_by_id(conn, user_id):
    r = _get_supabase().table('users').select('*').eq('user_id', user_id).execute()
    return _row(r.data[0]) if r.data else None

def ban_user(conn, user_id):
    _get_supabase().table('users').update({'is_banned': 1}).eq('user_id', user_id).execute()

def unban_user(conn, user_id):
    _get_supabase().table('users').update({'is_banned': 0}).eq('user_id', user_id).execute()

def update_user_balance(conn, user_id, amount):
    bal = get_user_balance(conn, user_id)
    nb = bal + amount
    _get_supabase().table('users').update({'balance': nb}).eq('user_id', user_id).execute()
    return nb

# === REPORTS ===
def get_sales_report(conn, period='daily'):
    from collections import defaultdict
    r = _get_supabase().table('orders').select('*').order('created_at', desc=True).limit(200).execute()
    groups = defaultdict(lambda: {'order_count': 0, 'revenue': 0.0})
    for o in (r.data or []):
        ts = o.get('created_at','')
        key = ts[:10] if period=='daily' else (ts[:7]+'-W' if period=='weekly' else ts[:7])
        groups[key]['order_count'] += 1
        groups[key]['revenue'] += float(o.get('amount',0))
    return [{'period': k, 'order_count': v['order_count'], 'revenue': v['revenue']}
            for k, v in sorted(groups.items(), reverse=True)[:30]]

def get_best_sellers(conn, limit=10):
    s = _get_supabase()
    r = s.table('orders').select('product_id,amount').execute()
    from collections import defaultdict
    counts = defaultdict(lambda: {'sold': 0, 'revenue': 0.0})
    for o in (r.data or []):
        pid = o.get('product_id')
        if pid:
            counts[pid]['sold'] += 1
            counts[pid]['revenue'] += float(o.get('amount',0))
    result = []
    for pid, st in sorted(counts.items(), key=lambda x: -x[1]['sold'])[:limit]:
        pr = s.table('products').select('name,emoji,price').eq('id', pid).execute()
        if pr.data:
            p = pr.data[0]
            result.append({'name': p.get('name','?'), 'emoji': p.get('emoji','📦'),
                          'price': p.get('price',0), 'sold': st['sold'], 'revenue': st['revenue']})
    return result

def get_revenue_by_payment(conn):
    s = _get_supabase()
    pr = s.table('payments').select('amount').eq('status','paid').execute()
    bakong = sum(float(p.get('amount',0)) for p in (pr.data or []))
    or_ = s.table('orders').select('amount').execute()
    orders = sum(float(o.get('amount',0)) for o in (or_.data or []))
    ur = s.table('users').select('balance').eq('is_banned',0).execute()
    balances = sum(float(u.get('balance',0)) for u in (ur.data or []))
    return {'bakong_deposits': bakong, 'order_revenue': orders, 'user_balances': balances}

def get_stock_sold_report(conn):
    s = _get_supabase()
    pr = s.table('products').select('id,name,emoji').eq('is_active',1).execute()
    result = []
    for p in (pr.data or []):
        sr = s.table('stock').select('*', count='exact').eq('product_id', p['id']).execute()
        total = sr.count or 0
        sld = s.table('stock').select('*', count='exact').eq('product_id', p['id']).eq('is_sold',1).execute()
        sold = sld.count or 0
        result.append({'name': p['name'], 'emoji': p['emoji'], 'total_stock': total, 'sold': sold, 'remaining': total - sold})
    return sorted(result, key=lambda x: -x['total_stock'])

def get_user_growth(conn, days=30):
    from datetime import datetime, timedelta
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    r = _get_supabase().table('users').select('created_at').gte('created_at', since).execute()
    from collections import defaultdict
    by_date = defaultdict(int)
    for u in (r.data or []):
        ts = u.get('created_at','')
        if ts: by_date[ts[:10]] += 1
    return [{'date': k, 'new_users': v} for k, v in sorted(by_date.items(), reverse=True)]

# === BOT SETTINGS ===
def get_bot_setting(conn, key, default=None):
    r = _get_supabase().table('bot_settings').select('value').eq('key', key).execute()
    return r.data[0]['value'] if r.data else default

def set_bot_setting(conn, key, value):
    s = _get_supabase()
    s.table('bot_settings').delete().eq('key', key).execute()
    s.table('bot_settings').insert({'key': key, 'value': value}).execute()

# === ENHANCED ORDERS ===
def get_all_orders(conn, status=None, limit=50):
    s = _get_supabase()
    q = s.table('orders').select('*')
    if status: q = q.eq('status', status)
    orders = _rows(q.order('created_at', desc=True).limit(limit).execute().data)
    for o in orders:
        if o.get('product_id'):
            pr = s.table('products').select('name,emoji').eq('id', o['product_id']).execute()
            if pr.data: o['product_name'] = pr.data[0].get('name',''); o['product_emoji'] = pr.data[0].get('emoji','📦')
        if o.get('user_id'):
            ur = s.table('users').select('first_name,username').eq('user_id', o['user_id']).execute()
            if ur.data: o['first_name'] = ur.data[0].get('first_name',''); o['user_username'] = ur.data[0].get('username','')
        if o.get('stock_id'):
            sr = s.table('stock').select('detail').eq('id', o['stock_id']).execute()
            if sr.data: o['stock_detail'] = sr.data[0].get('detail','')
    return orders

def search_orders(conn, query, limit=30):
    s = _get_supabase()
    try:
        uid = int(query)
        r = s.table('orders').select('*').eq('user_id', uid).order('created_at', desc=True).limit(limit).execute()
    except ValueError:
        r = s.table('orders').select('*').order('created_at', desc=True).limit(limit).execute()
    orders = _rows(r.data)
    for o in orders:
        if o.get('product_id'):
            pr = s.table('products').select('name,emoji').eq('id', o['product_id']).execute()
            if pr.data: o['product_name'] = pr.data[0].get('name',''); o['product_emoji'] = pr.data[0].get('emoji','📦')
        if o.get('user_id'):
            ur = s.table('users').select('first_name,username').eq('user_id', o['user_id']).execute()
            if ur.data: o['first_name'] = ur.data[0].get('first_name',''); o['user_username'] = ur.data[0].get('username','')
    return orders[:limit]

def refund_order(conn, order_id):
    s = _get_supabase()
    r = s.table('orders').select('*').eq('id', order_id).execute()
    if not r.data or r.data[0].get('status') == 'refunded': return None
    order = dict(r.data[0])
    bal = get_user_balance(conn, order['user_id'])
    nb = bal + float(order['amount'])
    s.table('users').update({'balance': nb}).eq('user_id', order['user_id']).execute()
    s.table('orders').update({'status': 'refunded'}).eq('id', order_id).execute()
    if order.get('stock_id'):
        s.table('stock').update({'is_sold': 0, 'order_id': None}).eq('id', order['stock_id']).execute()
    order['new_balance'] = nb
    return order

# === ENHANCED USERS ===
def get_users_with_stats(conn, limit=50):
    s = _get_supabase()
    r = s.table('users').select('*').order('created_at', desc=True).limit(limit).execute()
    users = _rows(r.data)
    for u in users:
        or_ = s.table('orders').select('amount').eq('user_id', u['user_id']).execute()
        u['total_spent'] = sum(float(o.get('amount',0)) for o in (or_.data or []))
        u['order_count'] = len(or_.data) if or_.data else 0
    return sorted(users, key=lambda x: -x['total_spent'])

def set_user_vip(conn, user_id, tier, discount_pct=0):
    _get_supabase().table('users').update({'vip_tier': tier, 'discount_percent': discount_pct}).eq('user_id', user_id).execute()


# === EXPORT ===
def export_orders_csv(conn):
    r = _get_supabase().table('orders').select('*').order('created_at', desc=True).execute()
    lines = ['id,user_id,product_id,amount,status,created_at']
    for o in (r.data or []):
        lines.append(f'{o.get("id","")},{o.get("user_id","")},{o.get("product_id","")},{o.get("amount","")},{o.get("status","")},{o.get("created_at","")}')
    return '\n'.join(lines)

def export_users_csv(conn):
    r = _get_supabase().table('users').select('*').order('created_at', desc=True).execute()
    lines = ['user_id,first_name,username,balance,vip_tier,discount_percent,is_banned']
    for u in (r.data or []):
        lines.append(f'{u.get("user_id","")},"{u.get("first_name","")}","{u.get("username","")}",{u.get("balance","")},{u.get("vip_tier","")},{u.get("discount_percent","")},{u.get("is_banned","")}')
    return '\n'.join(lines)
