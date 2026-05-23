-- v2: Multi-tenant tables
--
-- ✅ Backward compatible — CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS,
--    CREATE INDEX IF NOT EXISTS only

CREATE TABLE IF NOT EXISTS public.organizations (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    slug TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT,
    tier TEXT NOT NULL DEFAULT 'free',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.users (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    organization_id TEXT REFERENCES public.organizations(id) ON DELETE SET NULL,
    role TEXT NOT NULL DEFAULT 'member',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS public.projects (
    id TEXT PRIMARY KEY DEFAULT gen_random_uuid()::text,
    name TEXT NOT NULL,
    slug TEXT NOT NULL,
    organization_id TEXT NOT NULL REFERENCES public.organizations(id) ON DELETE CASCADE,
    tenant_schema TEXT NOT NULL,  -- e.g. 'tenant_abc123'
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    max_memories INTEGER,
    max_sessions INTEGER,
    memory_ttl_default INTEGER,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (organization_id, slug)
);

-- Add project_id column to api_keys (idempotent)
ALTER TABLE public.api_keys ADD COLUMN IF NOT EXISTS project_id TEXT REFERENCES public.projects(id) ON DELETE SET NULL;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_users_email ON public.users(email);
CREATE INDEX IF NOT EXISTS idx_users_org ON public.users(organization_id);
CREATE INDEX IF NOT EXISTS idx_projects_org ON public.projects(organization_id);
CREATE INDEX IF NOT EXISTS idx_projects_slug ON public.projects(organization_id, slug);
