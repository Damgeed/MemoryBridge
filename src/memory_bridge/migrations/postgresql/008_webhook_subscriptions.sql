-- v8: Webhook subscriptions and delivery log
--
-- Persists webhook subscription registrations and delivery history
-- so that subscriptions survive service restarts.
--
-- ✅ Backward compatible — CREATE TABLE IF NOT EXISTS only

CREATE TABLE IF NOT EXISTS webhook_subscriptions (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    event_types JSONB NOT NULL,        -- JSON array stored as JSONB
    secret TEXT NOT NULL,
    project TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    subscription_id TEXT NOT NULL REFERENCES webhook_subscriptions(id) ON DELETE CASCADE,
    event_type TEXT NOT NULL,
    url TEXT NOT NULL,
    status TEXT NOT NULL,
    status_code INTEGER,
    error TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_subscription
    ON webhook_deliveries(subscription_id, timestamp DESC);
