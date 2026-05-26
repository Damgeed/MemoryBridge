-- v6: Add stripe_customer_id to users table for bidirectional recovery
--
-- Allows linking a Stripe customer directly to a user account without
-- going through the subscription record. Enables recovery even if the
-- subscription record is lost.

ALTER TABLE users ADD COLUMN stripe_customer_id TEXT NOT NULL DEFAULT '';
