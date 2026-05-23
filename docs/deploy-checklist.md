# Memory Bridge Deploy Checklist

> Use this checklist for every production deployment to ensure a smooth,
> zero-downtime release.

---

## Pre-Deploy

- [ ] All tests pass (run `pytest` — 247+ tests expected)
- [ ] Migration guard passes (no destructive operations — run `check_backward_compatible()` on all new migration files)
- [ ] CHANGELOG updated with new version and changes
- [ ] Version bumped in `pyproject.toml`
- [ ] Git tag created (`vX.Y.Z`)
- [ ] Code reviewed and approved

## Deploy

- [ ] Build Docker image (`docker build -t memory-bridge:X.Y.Z .`)
- [ ] Push to registry (`docker push registry/memory-bridge:X.Y.Z`)
- [ ] Run migrations against staging environment
- [ ] Smoke test on staging:
  - [ ] `GET /health` returns 200
  - [ ] `GET /health/ready` returns 200
  - [ ] `GET /health/live` returns 200
  - [ ] Can create and retrieve a memory
- [ ] Deploy green version to production
- [ ] Health check green version:
  - [ ] `/health` responds
  - [ ] `/health/ready` responds (database connected)
  - [ ] `/health/live` responds
- [ ] Switch traffic from blue to green (e.g., load balancer target group update)
- [ ] Monitor for 5 minutes:
  - [ ] Error rates stable
  - [ ] Latency normal
  - [ ] No 5xx responses

## Post-Deploy

- [ ] Verify billing webhook still processes (if Stripe enabled)
- [ ] Verify API keys still authenticate
- [ ] Verify webhook subscriptions loaded
- [ ] Verify rate limiting active
- [ ] Run smoke test against production
- [ ] Tag release in GitHub
- [ ] Announce deployment in team channel
- [ ] After 24h with no issues, decommission blue environment

---

## Rollback

If the green deployment has issues:

1. Switch traffic back to blue via load balancer
2. No database rollback needed — schema changes are additive
3. Investigate and fix issues on green
4. Deploy fixed green
5. Switch traffic back to green

## Migration Guard Reference

Run the migration guard locally to check new migration files:

```python
from memory_bridge.migrations.runner import check_backward_compatible

warnings = check_backward_compatible("migrations/postgresql/005_new_feature.sql")
if warnings:
    for w in warnings:
        print(w)
else:
    print("✅ Migration is backward compatible")
```

### Safe operations (no warnings):
- `CREATE TABLE IF NOT EXISTS`
- `CREATE INDEX IF NOT EXISTS`
- `CREATE EXTENSION IF NOT EXISTS`
- `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` (nullable or with default)
- `INSERT`, `UPDATE`, `DELETE` data changes

### Unsafe operations (warnings emitted):
- `DROP COLUMN`, `DROP TABLE`, `DROP SCHEMA`
- `ALTER COLUMN ... SET NOT NULL`
- `ALTER COLUMN ... DROP DEFAULT`
- `RENAME COLUMN`, `RENAME TABLE`
