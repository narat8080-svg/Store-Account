-- ===========================================================================
-- Supabase PostgreSQL Schema for Rat Store Bot
-- Run this in Supabase SQL Editor (https://supabase.com/dashboard)
-- ===========================================================================

-- ⚠️ IMPORTANT: Run DROPs first if re-creating:
-- DROP TABLE IF EXISTS stock, orders, payments, products, promo_codes, categories, bot_settings, users CASCADE;

-- Users table (PK = user_id, not id)
CREATE TABLE IF NOT EXISTS users (
    user_id         BIGINT PRIMARY KEY,
    username        TEXT,
    first_name      TEXT,
    balance         DOUBLE PRECISION DEFAULT 0.0,
    is_banned       INTEGER DEFAULT 0,
    last_active     TIMESTAMPTZ,
    vip_tier        TEXT DEFAULT 'regular',
    discount_percent DOUBLE PRECISION DEFAULT 0,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Categories table
CREATE TABLE IF NOT EXISTS categories (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    emoji       TEXT DEFAULT '📂',
    is_active   INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Products table (FK → categories)
CREATE TABLE IF NOT EXISTS products (
    id            BIGSERIAL PRIMARY KEY,
    category_id   BIGINT NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    name          TEXT NOT NULL,
    price         DOUBLE PRECISION NOT NULL DEFAULT 0,
    emoji         TEXT DEFAULT '📦',
    description   TEXT DEFAULT '',
    is_active     INTEGER DEFAULT 1,
    is_unlimited  INTEGER DEFAULT 0,
    auto_delivery INTEGER DEFAULT 1,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

-- Add is_unlimited column if upgrading existing table
-- ALTER TABLE products ADD COLUMN IF NOT EXISTS is_unlimited INTEGER DEFAULT 0;

-- Stock table (FK → products)
CREATE TABLE IF NOT EXISTS stock (
    id          BIGSERIAL PRIMARY KEY,
    product_id  BIGINT NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    detail      TEXT NOT NULL,
    is_sold     INTEGER DEFAULT 0,
    order_id    BIGINT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Orders table (FK → users)
CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    product_id      BIGINT,
    stock_id        BIGINT,
    amount          DOUBLE PRECISION NOT NULL,
    original_amount DOUBLE PRECISION,
    promo_code      TEXT,
    status          TEXT DEFAULT 'pending',
    notify_pending  INTEGER DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Payments table (FK → users)
CREATE TABLE IF NOT EXISTS payments (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    amount      DOUBLE PRECISION NOT NULL,
    qr_text     TEXT,
    qr_md5      TEXT,
    status      TEXT DEFAULT 'pending',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    paid_at     TIMESTAMPTZ
);

-- Payment credit ledger. A payment ID can create at most one wallet credit,
-- even if multiple bot workers poll the same gateway transaction.
ALTER TABLE payments ADD COLUMN IF NOT EXISTS credited_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS payment_credits (
    payment_id  BIGINT PRIMARY KEY REFERENCES payments(id) ON DELETE CASCADE,
    user_id     BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    amount      NUMERIC(18,2) NOT NULL CHECK (amount > 0),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE OR REPLACE FUNCTION credit_payment_once(
    p_payment_id BIGINT,
    p_user_id BIGINT,
    p_amount NUMERIC,
    p_final_status TEXT DEFAULT 'credited'
)
RETURNS TABLE(credited BOOLEAN, new_balance NUMERIC)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
    v_payment_user BIGINT;
    v_payment_amount NUMERIC;
    v_balance NUMERIC;
    v_inserted INTEGER;
BEGIN
    IF p_final_status NOT IN ('credited', 'wallet_credited') THEN
        RAISE EXCEPTION 'Invalid payment credit status';
    END IF;
    IF p_amount IS NULL OR p_amount = 'NaN'::NUMERIC OR p_amount <= 0 THEN
        RAISE EXCEPTION 'Invalid payment credit amount';
    END IF;

    -- Lock the payment row while validating the user and exact cents value.
    SELECT user_id, ROUND(amount::NUMERIC, 2)
      INTO v_payment_user, v_payment_amount
      FROM payments
     WHERE id = p_payment_id
       AND status IN ('pending', 'paid')
     FOR UPDATE;

    IF NOT FOUND
       OR v_payment_user <> p_user_id
       OR v_payment_amount <> ROUND(p_amount, 2) THEN
        RETURN QUERY SELECT FALSE, NULL::NUMERIC;
        RETURN;
    END IF;

    INSERT INTO payment_credits(payment_id, user_id, amount)
    VALUES (p_payment_id, p_user_id, v_payment_amount)
    ON CONFLICT (payment_id) DO NOTHING;
    GET DIAGNOSTICS v_inserted = ROW_COUNT;

    IF v_inserted = 0 THEN
        SELECT balance::NUMERIC INTO v_balance
          FROM users WHERE user_id = p_user_id;
        RETURN QUERY SELECT FALSE, v_balance;
        RETURN;
    END IF;

    UPDATE users
       SET balance = COALESCE(balance::NUMERIC, 0) + v_payment_amount
     WHERE user_id = p_user_id
     RETURNING balance::NUMERIC INTO v_balance;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'User % does not exist', p_user_id;
    END IF;

    UPDATE payments
       SET status = p_final_status,
           paid_at = COALESCE(paid_at, NOW()),
           credited_at = NOW()
     WHERE id = p_payment_id;

    RETURN QUERY SELECT TRUE, v_balance;
END;
$$;

REVOKE ALL ON FUNCTION credit_payment_once(BIGINT, BIGINT, NUMERIC, TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION credit_payment_once(BIGINT, BIGINT, NUMERIC, TEXT) TO service_role;

-- Promo codes table
CREATE TABLE IF NOT EXISTS promo_codes (
    id              BIGSERIAL PRIMARY KEY,
    code            TEXT NOT NULL UNIQUE,
    discount_type   TEXT NOT NULL DEFAULT 'percent',
    discount_value  DOUBLE PRECISION NOT NULL DEFAULT 0,
    max_uses        INTEGER DEFAULT 0,
    current_uses    INTEGER DEFAULT 0,
    min_order       DOUBLE PRECISION DEFAULT 0,
    is_active       INTEGER DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Bot settings table (PK = key, not id)
CREATE TABLE IF NOT EXISTS bot_settings (
    key     TEXT PRIMARY KEY,
    value   TEXT
);

-- ===========================================================================
-- Indexes for performance
-- ===========================================================================
CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_stock_product_id ON stock(product_id);
CREATE INDEX IF NOT EXISTS idx_stock_is_sold ON stock(is_sold);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_id);
CREATE INDEX IF NOT EXISTS idx_payments_user_id ON payments(user_id);

-- ===========================================================================
-- Enable Realtime for live sync (optional)
-- ===========================================================================
-- ALTER PUBLICATION supabase_realtime ADD TABLE orders;
-- ALTER PUBLICATION supabase_realtime ADD TABLE payments;

-- ===========================================================================
-- Fix auto-increment sequences (run if you get "duplicate key" errors)
-- ===========================================================================
-- SELECT setval('payments_id_seq', COALESCE((SELECT MAX(id) FROM payments), 1));
-- SELECT setval('orders_id_seq',    COALESCE((SELECT MAX(id) FROM orders), 1));
-- SELECT setval('products_id_seq',  COALESCE((SELECT MAX(id) FROM products), 1));
-- SELECT setval('categories_id_seq',COALESCE((SELECT MAX(id) FROM categories), 1));
-- SELECT setval('stock_id_seq',     COALESCE((SELECT MAX(id) FROM stock), 1));
