# Rex ⚡ Production Audit — Memory Bridge SaaS v1.1.0

**Auditor:** Rex ⚡ — The Critic
**Scope:** Full stack audit of 8-phase SaaS transformation
**Tests:** 232 passing, 56 skipped (PG), 0 failures
**Date:** 2026-05-23

---

## SEVERITY KEY

| Icon | Severity | Definition |
|---|---|---|
| 🔴 CRITICAL | Ship-blocker | Will cause data loss, security breach, or production outage in real traffic |
| 🟠 HIGH | Must fix before GA | Will fail under moderate load; exploitable with modest effort |
| 🟡 MEDIUM | Should fix | Unlikely to trigger in light traffic, but will bite at scale |
| 🔵 LOW | Nice to fix | Cosmetic, observability, or code quality |

---

# 🔴 CRITICAL FINDINGS

## C1 — Webhook Subscriptions LIVE in Memory Only (Data Loss on Restart)

**File:** `webhooks/webhook_controller.py:24`
> *"In-memory registry (will be shared across requests) — In production, this would be persisted in the database."*

```python
_service: Optional[WebhookService] = None
```

All webhook subscriptions, secrets, and delivery history are stored in `dict[str, WebhookSubscription]` in `WebhookService._subscriptions`. There is zero database persistence.

**Impact:**
- **Any restart (deploy, crash, scale event) permanently deletes ALL webhook subscriptions.**
- Users must re-register every webhook after every deploy.
- Webhook secrets are re-transmitted to users on every deploy.
- Zero delivery history survives restart.

**Fix:** Persist subscriptions to the `api_keys` table or a dedicated `webhook_subscriptions` table. Add lifecycle hooks to reload on startup.

---

## C2 — S3 Offloading is a NO-OP in Production (Stub Implementation)

**File:** `repository/s3_store.py:52-55`

```python
if self.enabled:
    # In production: use aioboto3 to upload to S3
    logger.info("S3 store: would upload %d bytes to %s/%s", len(serialized), self._bucket, key)
```

The S3 store **logs but does not actually upload** when S3 is configured. The `retrieve` method also returns `None` for S3-backed values (line 76: `return None`).

**Impact:**
- **All memories >64KB are silently lost.** They get a reference key stored in the DB, but the value is never uploaded to S3.
- Retrieval returns `None` — agents get empty data with no error.
- The `delete` method is also a no-op (`return True`).

**Fix:** Implement `aioboto3` or `s3fs` for actual S3 upload/download. Add integration tests with MinIO. Add validation that S3.put succeeds before returning the key.

---

## C3 — Stripe Webhook Signature is NEVER Verified

**File:** `controllers/billing_controller.py:22-26`

```python
payload = await request.body()
signature = request.headers.get("stripe-signature", "")
service = BillingService()
result = await service.handle_webhook(payload, signature)
```

**File:** `services/billing_service.py:87-89`

```python
# In production, verify signature with stripe.Webhook.construct_event()
# For now, log and return
logger.info("Received Stripe webhook (signature: %s...)", signature[:20] if signature else "none")
return {"status": "received", "event": "unknown"}
```

**Impact:**
- **Anyone with the endpoint URL can send fake Stripe webhooks.**
- Attacker can forge `customer.subscription.deleted` events to downgrade paying customers to free tier.
- Attacker can forge `invoice.paid` events to give themselves free Pro/Enterprise access.
- No idempotency key checking — replay attacks are trivially possible.

**Fix:** Implement `stripe.Webhook.construct_event()` with `STRIPE_WEBHOOK_SECRET`. Add idempotency key (`Idempotency-Key` / `Idempotency-Key` header dedup) before Stripe's webhook processing. This is **not** phase-4 work — this is ship-blocking.

---

## C4 — `generate_token` Falls Back to `"dev-secret"` When JWT Not Configured

**File:** `services/user_service.py:87-89`

```python
return jwt.encode(
    payload,
    settings.jwt_secret or "dev-secret",
    algorithm=settings.jwt_algorithm or "HS256",
)
```

Line 98 has the same issue in `refresh_token`.

**Impact:**
- If an operator deploys without setting `MEMORY_BRIDGE_JWT_SECRET`, all JWT tokens are signed with the **hardcoded string `"dev-secret"`**. Anyone who reads the source can forge tokens.
- Since `APIKeyMiddleware` accepts JWT tokens (line 105 of `auth.py`), this bypasses ALL authentication.

**Fix:** Raise `ConfigurationError` if `jwt_secret` is empty AND the `/auth` endpoints are mounted. Never fall back to a hardcoded dev secret.

---

## C5 — Unbounded `asyncio.create_task` in Webhook Dispatch (Task Leak → OOM)

**File:** `webhooks/webhook_service.py:218`

```python
asyncio.create_task(self._deliver(sub, payload_data))
```

Each matching subscription spawns a fire-and-forget task. With 100 subscriptions and 1,000 events/second (completely reasonable), that's **100,000 concurrent tasks**.

**Impact:**
- Unbounded task growth exhausts the event loop and memory.
- No backpressure, no semaphore, no queue limit.
- If the subscriber endpoint is slow (e.g., 5s HTTP timeout), tasks pile up faster than they complete.
- Eventually `asyncio.create_task` itself throws `RuntimeError: Task <...> got Future <...> attached to a different loop` or OOM.

**Fix:** Use `asyncio.Semaphore(max_concurrent=50)` or a bounded task pool. Consider moving to a proper job queue (Redis RQ, Celery, or at least an `asyncio.Queue` with capped worker count). The `_retry_queue` is bounded by Queue's default (infinite) — also unbounded.

---

## C6 — No Input Validation on Memory `value` Size Before DB Write

Memory entries accept `value: Any` (Pydantic `Any` type). While there's a 10MB global body limit, a 9MB JSON value fits within that — and it's stored directly in `JSONB` in PostgreSQL.

**Impact:**
- Storing a 9MB value in `JSONB` every 100ms = massive table bloat, slow queries, TOAST issues.
- No warning, no compression check, no size-based routing to S3 during the actual DB write.
- The S3 offloading's `needs_offloading()` method sizes the value AFTER serialization but the route to S3 is never triggered because `memory_service.py` calls `repo.store_memory(entry)` directly — S3Store is never consulted.

**Fix:** Add a `max_value_size` config option (e.g., 1MB default for free tier). Enforce in `MemoryService.create_memory()` before the DB write. Wire S3Store into the write path.

---

## C7 — Concurrent Session-Dependent Migration Can Deadlock

**File:** `repository/postgres_repo.py:192-221`

The `_migrate` method runs inside a single connection's transaction (`async with self.pool.acquire() as conn`), but multiple workers can initialize simultaneously on startup. Migration statements like `ALTER TABLE ... ADD COLUMN` acquire `ACCESS EXCLUSIVE` locks.

**Impact:**
- If two app instances start simultaneously (Kubernetes rolling update, multiple replicas), they both try to `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE`, etc.
- `ALTER TABLE ... ADD COLUMN` locks wait for each other. With 3+ workers, this can deadlock.
- The migration error handling on line 218-221 (`logger.warning ... skipped`) means workers silently skip migrations if they fail, potentially leaving the schema in an inconsistent state.

**Fix:** Use advisory locks (`pg_advisory_xact_lock`) to serialize migrations across workers. Or use a dedicated startup migration job/init container pattern.

---

# 🟠 HIGH

## H1 — Rate Limiter Window is STATIC Per-Minute (Can Be Gamed)

**File:** `middleware/rate_limit.py:41`

```python
window_key = f"ratelimit:{key}:{int(now // 60)}"
```

This uses a fixed 1-minute window per key. A burst of 60 requests at `:59` and 60 more at `:01` are both allowed = **120 requests in 2 seconds**.

**Impact:**
- Trivial to bypass: send 60 reqs at `XX:59:59`, 60 more at `XX:00:01`.
- No penalty for burst — the entire minute quota can be exhausted in <1s.

**Fix:** Use a proper sliding window (sorted set with ZREMRANGEBYSCORE + ZCOUNT) or a token bucket with per-second refill.

---

## H2 — JWT Has No Refresh Token Rotation

**File:** `services/user_service.py:92-109`

```python
async def refresh_token(self, token: str) -> Optional[str]:
    try:
        payload = jwt.decode(token, ..., options={"verify_exp": True})
        if payload.get("sub"):
            return await self.generate_token(payload)
    except jwt.ExpiredSignatureError:
        pass  # Can't refresh expired tokens
```

- Refresh endpoint accepts the same JWT as the auth token — it's not a separate refresh token.
- No token revocation list (no blacklist, no token version in DB).
- JWT expiration is configurable (default 60 minutes) but there's no mechanism to revoke stolen tokens.
- The `refresh_token` endpoint has **no rate limiting** — an attacker can brute-force JWT secrets offline.

**Fix:** Implement refresh tokens (opaque, stored in DB, single-use with rotation). Add token version column to users table for server-side revocation.

---

## H3 — No TLS Between App ↔ PostgreSQL or App ↔ Redis

**File:** `docker-compose.yml:36-37`

```yaml
MEMORY_BRIDGE_DATABASE_URL: postgres://mb:mb_dev@postgres/memory_bridge
MEMORY_BRIDGE_REDIS_URL: redis://redis:6379
```

- No `?sslmode=require` on the Postgres DSN.
- No `rediss://` (TLS) on the Redis URL.
- The password `mb_dev` is in plaintext in the compose file.

**Impact:** On any network (shared Kubernetes cluster, VPC with a compromised pod), all traffic between services is in cleartext. API keys and memory values are sniffable.

**Fix:** Enforce TLS for inter-service communication. Use Docker secrets or a vault for credentials, not environment variables in compose files.

---

## H4 — S3 Bucket May Be Public / No Server-Side Encryption

**File:** `repository/s3_store.py:27`

```python
self._bucket = os.environ.get("MEMORY_BRIDGE_S3_BUCKET", "memory-bridge")
```

- Default bucket name is generic and guessable.
- No `aws:kms` or `AES256` server-side encryption header.
- No bucket policy enforcement (block public access).
- No access logging configured.

**Impact:** If the bucket name is `memory-bridge` (default) on a shared AWS account, data is at risk. Even with correct credentials, data at rest in S3 is unencrypted.

**Fix:** Default bucket name should be random per-deployment. Enforce SSE-S3 or SSE-KMS. Add bucket policy that denies non-TLS access (aws:SecureTransport).

---

## H5 — Rate Limiter Skips `/auth/register` and `/auth/login`

**File:** `main.py:164`

```python
if request.url.path not in ("/health", "/metrics"):
    client_ip = request.client.host if request.client else "unknown"
    allowed = await _limiter.check(client_ip)
```

Auth endpoints are NOT in the skip list, BUT... the rate limiter is IP-based and uses `request.client.host`. Behind any reverse proxy (nginx, ALB, Cloudflare), `request.client.host` is the **proxy's IP**, not the client. Every user shares the same rate limit bucket.

Additionally, login/register endpoints have **no dedicated, aggressive rate limiting** for brute-force attacks. An attacker can try 10,000 passwords/min before hitting the global 60 req/min limit.

**Fix:** Add `X-Forwarded-For` or `X-Real-IP` support to the rate limiter. Add aggressive per-IP limits on `/auth/login` (e.g., 5 req/min). Add account lockout after N failed attempts.

---

## H6 — No Dependency Pinning / No Supply Chain Security

**File:** `pyproject.toml:10-20`

```toml
dependencies = [
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    ...
]
```

Every dependency uses `>=` (minimum version with no upper bound). The `uv.lock` file exists but is not verified.

**Impact:** A `pip install` tomorrow might pull a backdoored version of `bcrypt`, `PyJWT`, `httpx`, or any transitive dependency via a supply chain attack on PyPI. No `pip audit`, no `pip hash` pins, no `safety` check in CI.

**Fix:** Pin exact versions or at least `~=` (compatible release). Add `pip-audit` or `safety` to CI pipeline. Sign releases.

---

## H7 — Webhook Secrets Sent in Cleartext Over The Wire on Create/Update

**File:** `webhooks/webhook_controller.py:50-51`

The `WebhookCreate` schema includes `secret: str = Field(..., min_length=8, max_length=256)`.

The secret is:
1. Sent by the user in the HTTP request body (cleartext over the wire unless HTTPS).
2. Stored in **memory only** (see C1), but when persisted in the future, will be stored in cleartext.
3. Returned to the user on subsequent GET requests? Actually no — `WebhookResponse` doesn't include `secret`. Good. But there's no masking of secrets in logs.

**Fix:** Hash webhook secrets with bcrypt before storage (same as API keys). Never log secret values.

---

## H8 — No Backup / Restore System

Nowhere in the codebase is there backup functionality:

- No `pg_dump` automation.
- No WAL archiving configuration in docker-compose.
- No point-in-time recovery (PITR).
- No backup verification test.
- No backup retention policy.

**Impact:** A data-corrupting bug (see C5, C6), a malicious actor, or a storage failure = permanent data loss. The company has no way to recover customer data.

**Fix:** Add WAL archiving to the Postgres docker-compose. Write a `scripts/backup.sh` and `scripts/restore.sh`. Add backup verification to CI. Document RPO and RTO.

---

# 🟡 MEDIUM

## M1 — RLS Policies Don't Cover `memory_tags` Table

**File:** `repository/rls_repo.py:56-58`

```python
# memory_tags does not have a project column; isolation is
# inherited through the FK relationship to memories, so RLS on
# memories cascades automatically.
```

RLS does **not** cascade through foreign keys. The policy says `project = current_setting('app.current_project_id')`, but `memory_tags` has no `project` column. A user in project A can query `memory_tags` directly (if they have raw SQL access) and see tags from project B.

While the API layer prevents direct queries, any future migration that reads `memory_tags` directly will bypass isolation.

**Fix:** Add a `project` column to `memory_tags` (or don't add RLS to `memory_tags` — remove the `FORCE ROW LEVEL SECURITY` if not needed).

---

## M2 — EventBus `publish()` Calls Local Subscribers Synchronously

**File:** `events/event_bus.py:53-57`

```python
for cb in self._local_subscribers.get(event_type, []):
    try:
        await cb(data)
    except Exception:
        logger.warning(...)
```

If any subscriber callback is slow (e.g., a webhook delivery), it blocks the entire `publish()` call. This means creating a memory includes synchronous webhook dispatch time.

**Impact:** High-latency webhook subscribers degrade memory write performance for everyone.

**Fix:** Wrap local subscriber calls in `asyncio.create_task()` or use the retry queue pattern.

---

## M3 — `_deliver()` Blocks During Retry Sleeps

**File:** `webhooks/webhook_service.py:315`

```python
await asyncio.sleep(delay)
```

The `_deliver()` method sleeps **synchronously** during exponential backoff. Since `dispatch_event` creates tasks for each subscription, sleep blocks that specific task. However, with many subscriptions, each task sleeps independently — no shared retry deduplication.

**Impact:** If 50 subscriptions all timeout on the same event, that's 50 concurrent tasks each sleeping 1s, 2s, 4s before giving up. This ties up the event loop and delays other work.

**Fix:** Move retry logic to the `_retry_worker` (which already exists but is barely used). The worker is already started but `_deliver` does its own retries inline rather than delegating.

---

## M4 — Prometheus Metrics Not Multi-Worker Safe (Process-Memory Registration)

**File:** `main.py:91-93`

```python
uptime_gauge.set_function(
    lambda: (datetime.now(timezone.utc) - _start_time).total_seconds()
)
```

`_start_time` is module-level. With multiple workers (gunicorn uvicorn workers), each has its own `_start_time`. Prometheus `/metrics` is scraped from one worker per scrape, giving inconsistent uptime readings.

Also, `request_counter.inc()` is per-process. Prometheus dedupes only when metric labels include an instance identifier — these don't.

**Fix:** Use a push-based approach (Pushgateway) or ensure Prometheus metrics are instance-labeled.

---

## M5 — Shard Router Uses Direct Hash, Not Consistent Hashing

**File:** `repository/shard_router.py:47-51`

```python
hash_bytes = hashlib.sha256(project_id.encode()).digest()
hash_int = int.from_bytes(hash_bytes[:8], "big")
shard_index = hash_int % len(self.shards)
```

This is `hash % N`, **not** consistent hashing. When you add one shard to a 4-shard cluster, 80% of projects move (not the ideal 20%).

Meanwhile, `hash_ring.py` has a proper `HashRing` implementation with virtual nodes (consistent hashing) — but it's **never imported or used** anywhere.

**Impact:** Re-sharding means moving 80%+ of data, not the 25% that consistent hashing would provide. Takes 4x longer, 4x more risk.

**Fix:** Wire `HashRing` into `ShardRouter` or replace the `%` with proper ring lookups. The code already exists!

---

## M6 — Backend Rejects All Requests When No Auth Is Configured and Open Mode Is Off (Bricked Deploy)

**File:** `auth.py:69-73`

```python
return JSONResponse(
    status_code=401,
    content={"detail": "Authentication required. No API keys configured..."},
)
```

If an operator deploys without `MEMORY_BRIDGE_API_KEY` and without `MEMORY_BRIDGE_ALLOW_OPEN=true`, the **entire API returns 401 for every request** — including the admin endpoints that are supposed to create API keys.

**Impact:** Catch-22: You can't create an API key because the API rejects you, and the API rejects you because you have no API key. The server is bricked until you either add the env var or enable open mode, then restart.

**Fix:** Add an `admin/keys` initial-seed endpoint that works without auth if no keys exist and if the request comes from localhost. Or print the seed key on stdout at startup.

---

## M7 — No Telemetry / Observability (No Structured Logging, No Traces)

- Logging uses `logging.warning/exception` — no structured JSON logging.
- No OpenTelemetry instrumentation.
- No request tracing across services.
- No error tracking (Sentry, etc.).
- No health check on database connectivity (the `/health` endpoint returns "ok" even if the DB is gone — it reads from process metrics).

**Impact:** Debugging production incidents requires SSH access and grepping unstructured logs. Impossible to trace a single request across the stack.

**Fix:** Add structured JSON logging (`python-json-logger` or `structlog`). Add OpenTelemetry middleware. Add Sentry for error tracking. Make `/health` probe actual DB connectivity.

---

## M8 — Webhook Delivery History is In-Memory Only

**File:** `webhooks/webhook_service.py:99`

```python
self._last_deliveries: dict[str, WebhookDelivery] = {}
```

Delivery history is stored in a `dict` keyed by subscription ID. This means:
- You can only see the LAST delivery per subscription.
- All history is lost on restart.
- There's no way to audit delivery success/failure over time.

**Fix:** Persist delivery history to the database. Add a `webhook_deliveries` table.

---

# 🔵 LOW

## L1 — No Rate Limiting on Admin Endpoints

The admin controller uses `get_storage()` directly (line 34 of `admin_controller.py`), which creates a fresh connection from the pool on every call. Admin key creation is not rate-limited.

## L2 — `Storage` Module-Level Singleton Can Leak Across Tests

**File:** `dependencies.py:20`

```python
storage = SQLiteMemoryRepository(db_path="memory_bridge.db")
```

Tests swap `storage.db_path` (test_server.py:14-15) which works because `MemoryStorage` is a singleton. But parallel tests (pytest-xdist) would corrupt each other's state.

## L3 — No Database Migration Tests

56 tests are skipped with `@pytest.mark.postgres`. The migration runner has zero tests. If someone adds a migration that breaks the schema, it won't be caught until deploy.

## L4 — HandoffPayload includes `context` in Request Body

**File:** `models.py:39`

The `HandoffPayload` accepts `context: dict[str, Any]` with no size limits. A handoff with a 100MB context dict is accepted, processed, and stored. No truncation, no validation.

## L5 — Default CORS is `*` (Allow All Origins)

**File:** `main.py:47`

```python
_CORS_ORIGINS = os.environ.get("MEMORY_BRIDGE_CORS_ORIGINS", "*").split(",")
```

In production, this means any website can make authenticated requests from a user's browser. While this is an API (not a browser app), it enables CSRF on endpoints that accept cookie-based auth (if added later).

## L6 — Dockerfile `python:3.12-slim` Has No Security Scanning

The Docker image pins no package versions. No `safety check`, no `trivy`, no `grype`. A base image update could introduce vulnerabilities.

---

# SUMMARY OF FINDINGS

| Severity | Count | Key Issues |
|---|---|---|
| 🔴 CRITICAL | 7 | Webhooks lost on restart, S3 is a no-op, Stripe webhook unverified, `dev-secret` fallback, unbounded task creation, no value-size enforcement, migration deadlock |
| 🟠 HIGH | 8 | Rate limiter bypass, no JWT revocation, no TLS inter-service, S3 public default, auth brute-force, dependency supply chain, webhook secrets in cleartext, no backup/restore |
| 🟡 MEDIUM | 8 | RLS gap on memory_tags, synchronous event bus, retry blocking, Prometheus per-worker, `hash % N` not consistent hashing, bricked deploy catch-22, no observability, in-memory delivery history |
| 🔵 LOW | 6 | Admin rate limiting, test singletons, no migration tests, handoff size, default CORS `*`, Docker security |

---

**Rex's Verdict:** This code is well-structured and the architecture is sound, but there are **7 ship-blocking critical issues** that WILL cause data loss, security breaches, or production outages. The top 3 to fix before any production deployment:

1. **C2 — S3 offloading is a no-op.** Every memory >64KB is silently discarded.
2. **C3 — Stripe webhooks are unverified.** Anyone can forge billing events.
3. **C1 — Webhooks are ephemeral.** Every deploy destroys all subscriptions.

Build time: ~8 hours. Fix time: ~2-3 days for all criticals.
