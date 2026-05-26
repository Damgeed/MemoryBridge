# Auth Flow Fix — Implementation Plan

**Audience:** Engineering
**Status:** Approved for implementation
**Date:** 2026-05-27
**Files affected:** 20+ (backend: ~10, frontend: ~10)

---

## 1. Problem Summary

| # | Problem | Root Cause | Impact |
|---|---------|------------|--------|
| 1 | **No sign-out** | `logout()` only clears localStorage; Auth0 session persists; no backend session invalidation | Users can't truly end a session; shared/public machines leak access |
| 2 | **No session indicator** | `updateAuthUI()` shows session-dot color + "Signed in since" but **not** email/plan/tier persistently | Users don't know if they're logged in or what plan they're on |
| 3 | **Subscribe-without-account** | Stripe checkout works without JWT; `/billing/pre-checkout` creates `pending-*` org; webhook stores sub, but user has no Auth0 identity | User can pay but never log in again if they clear cookies/switch devices |
| 4 | **Email recovery requires API key** | `/dashboard/recover` only returns key_value or key_id; no auth0/JWT login path | Users need the key text they likely lost |

---

## 2. Design Decisions

### Decision 1: Sign-Out MUST invalidate Auth0 session
**Rationale:** LocalStorage clearing alone is not a sign-out. Without Auth0 logout, the user's Auth0 SSO session persists, meaning "Sign In" immediately re-logs them without a password prompt. The frontend MUST redirect to Auth0's `/v2/logout` endpoint and clear all local state.

### Decision 2: Subscribe REQUIRES JWT (no more pending-org pattern)
**Rationale:** The entire `pre-checkout` → `pending-org` → `link-subscription` pattern is fragile and creates orphaned subscriptions. Instead:
- Pricing page CTA buttons check `localStorage.getItem('mb_jwt')`
- If no JWT → open auth modal (sign-up/login first), then proceed to checkout
- `/billing/checkout` already requires auth (line 94-100 in billing_controller.py) — this is correct
- **Remove** `/billing/pre-checkout` endpoint
- **Remove** `/auth/link-subscription` endpoint
- The Stripe webhook `_handle_checkout_completed` already uses `client_reference_id` (the org_id from JWT) — that's sufficient

### Decision 3: Session Tracking via new `user_sessions` table
**Rationale:** Need to track active sessions for:
- Showing "last active" timestamp in navbar
- Server-side session termination on sign-out
- Rate limiting per session (future)

### Decision 4: Email Recovery becomes "Forgot Password" → Auth0 Passwordless
**Rationale:** Instead of recovering an API key string (which users lose), make the recovery flow send an Auth0 passwordless email OTP. After OTP verification, the user gets a fresh JWT + their API key ID displayed in the dashboard (not the key value). If they need a new key, they generate one from the dashboard.

---

## 3. Architecture Diagram

```
┌─────────────────────────────────────────────────────────┐
│                      BROWSER                            │
│                                                         │
│  localStorage:                                          │
│    mb_jwt          ← JWT string (app-issued, HS256)     │
│    mb_api_key_id   ← key ID (not the secret value)      │
│    mb_session_id   ← UUID for this browser session      │
│    mb_org_id       ← organization_id (cached)           │
│                                                         │
│  updateAuthUI():                                         │
│    - Shows email + plan name + "last active X min ago"  │
│    - Green/amber/red session dot                        │
│    - "Sign Out" button → Auth0 logout redirect           │
└──────────────┬──────────────────────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────────────────────┐
│                    BACKEND                              │
│                                                         │
│  /auth/session   GET    → return session info           │
│  /auth/logout    POST   → invalidate session            │
│  /auth/me        GET    → user email + plan + keys info │
│                                                         │
│  New table: user_sessions                               │
│    id, user_id, org_id, token_jti, created_at,          │
│    last_active_at, ip_address, user_agent, active       │
│                                                         │
│  JWT now includes jti (JWT ID) claim for invalidation  │
└─────────────────────────────────────────────────────────┘
```

---

## 4. Detailed Changes

### 4.1 Database Migration — `user_sessions` table

**File:** `src/memory_bridge/migrations/sqlite/006_user_sessions.sql` (new)
**File:** `src/memory_bridge/migrations/postgresql/007_user_sessions.sql` (new)

```sql
-- Track user browser sessions for sign-out and activity monitoring
CREATE TABLE IF NOT EXISTS user_sessions (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    organization_id TEXT NOT NULL,
    token_jti TEXT NOT NULL UNIQUE,      -- JWT ID for invalidation
    ip_address TEXT DEFAULT '',
    user_agent TEXT DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE INDEX IF NOT EXISTS idx_user_sessions_user ON user_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_user_sessions_jti ON user_sessions(token_jti);
CREATE INDEX IF NOT EXISTS idx_user_sessions_active ON user_sessions(active);
```

**Repository methods to add** (`repository/__init__.py`, `postgres_repo.py`, `sqlite_repo.py`):

```python
# Abstract
async def create_user_session(self, user_id: str, org_id: str, jti: str, ip: str, ua: str) -> str: ...
async def get_user_session(self, session_id: str) -> Optional[dict]: ...
async def deactivate_user_session(self, session_id: str) -> bool: ...
async def deactivate_all_user_sessions(self, user_id: str) -> int: ...
async def touch_user_session(self, session_id: str) -> None: ...
async def get_active_sessions_for_user(self, user_id: str) -> list[dict]: ...
```

### 4.2 User Model — Add `jti` to JWT payload

**File:** `src/memory_bridge/services/user_service.py`

**Changes to `generate_token()`:**
```python
async def generate_token(self, user: dict) -> str:
    settings = self.settings
    jwt_secret = self._get_jwt_secret()
    now = datetime.now(timezone.utc)
    jti = str(uuid.uuid4())  # ← NEW: unique JWT ID
    payload = {
        "sub": user.get("id", user.get("email")),
        "email": user.get("email"),
        "name": user.get("name", ""),
        "role": user.get("role", "member"),
        "project_id": user.get("organization_id"),
        "jti": jti,              # ← NEW
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes or 60),
    }
    token = jwt.encode(payload, jwt_secret, algorithm=settings.jwt_algorithm or "HS256")
    
    # ← NEW: persist session record
    if self.repo:
        await self.repo.create_user_session(
            user_id=payload["sub"],
            org_id=user.get("organization_id", ""),
            jti=jti,
            ip=user.get("_ip", ""),      # caller must set this
            ua=user.get("_ua", ""),
        )
    return token
```

**Update `auth.py` middleware** to validate `jti` isn't revoked (optional, future):

```python
# In APIKeyMiddleware.dispatch(), after JWT decode:
if payload.get("jti"):
    storage = await get_storage()
    session = await storage.get_user_session_by_jti(payload["jti"])
    if session and not session.get("active"):
        return JSONResponse(
            status_code=401,
            content={"detail": "Session has been revoked. Please sign in again."},
        )
```

### 4.3 New Controller Routes

#### 4.3.1 `POST /auth/logout` — Backend session invalidation

**File:** `src/memory_bridge/controllers/auth_controller.py`

```python
@router.post("/logout")
async def logout(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Invalidate the current JWT session server-side.
    
    Sets the session record to inactive and returns an Auth0 logout URL
    (if Auth0 is configured) so the frontend can redirect there.
    """
    auth = getattr(request.state, "auth", None)
    if not auth:
        # Already unauthenticated — nothing to do
        return {"status": "ok", "auth0_logout_url": None}
    
    jti = auth.get("jti", "")
    if jti:
        await storage.deactivate_user_session_by_jti(jti)
    
    # Build Auth0 logout URL if configured
    auth0_logout_url = None
    from ..services.auth0_service import get_auth0_service
    svc = get_auth0_service()
    if svc.enabled and svc.domain and svc.client_id:
        base = os.environ.get("APP_URL", str(request.base_url).rstrip("/"))
        auth0_logout_url = (
            f"https://{svc.domain}/v2/logout?"
            f"client_id={svc.client_id}&"
            f"returnTo={base}/pricing"
        )
    
    return {"status": "ok", "auth0_logout_url": auth0_logout_url}
```

#### 4.3.2 `GET /auth/session` — Session indicator data

**File:** `src/memory_bridge/controllers/auth_controller.py`

```python
@router.get("/session")
async def get_session_info(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    """Return session info: email, plan name, last active timestamp.
    
    Requires valid JWT. Used by the navbar session indicator.
    """
    auth = getattr(request.state, "auth", None)
    if not auth:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    user_email = auth.get("user_email", "")
    org_id = auth.get("project_id", "")
    jti = auth.get("jti", "")
    
    # Get subscription tier
    tier = "free"
    try:
        sub = await storage.get_subscription_by_org(org_id)
        if sub:
            tier = sub.tier
    except Exception:
        pass
    
    # Get session last_active
    last_active = None
    if jti:
        try:
            session = await storage.get_user_session_by_jti(jti)
            if session:
                last_active = session.get("last_active_at")
        except Exception:
            pass
    
    # Update last_active_at
    if jti:
        try:
            await storage.touch_user_session_by_jti(jti)
        except Exception:
            pass
    
    return {
        "email": user_email,
        "name": auth.get("user_name", ""),
        "tier": tier,
        "organization_id": org_id,
        "last_active": last_active.isoformat() if last_active else None,
        "session_id": jti[:8] if jti else "",
    }
```

### 4.4 Frontend Changes

#### 4.4.1 `logout()` — Full sign-out with Auth0 redirect

**File pattern:** All HTML pages (faq.html, playground.html, demo.html, dashboard.html, index.html, api-docs.html, graph.html, pricing.html)

**Replace the current `logout()` function:**

```javascript
async function logout() {
    closeLogoutConfirm();
    const jwt = localStorage.getItem('mb_jwt');
    
    // 1. Server-side session invalidation
    if (jwt) {
        try {
            const res = await fetch('/auth/logout', {
                method: 'POST',
                headers: { 'Authorization': 'Bearer ' + jwt },
            });
            if (res.ok) {
                const data = await res.json();
                // 2. Clear ALL local state
                localStorage.removeItem('mb_jwt');
                localStorage.removeItem('mb_api_key_id');
                localStorage.removeItem('mb_org_id');
                localStorage.removeItem('mb_session_id');
                
                // 3. Redirect to Auth0 logout if configured
                if (data.auth0_logout_url) {
                    window.location.href = data.auth0_logout_url;
                    return;
                }
            }
        } catch (e) {
            console.warn('Server logout failed, clearing local state', e);
        }
    }
    
    // Fallback: clear and redirect
    localStorage.removeItem('mb_jwt');
    localStorage.removeItem('mb_api_key_id');
    localStorage.removeItem('mb_org_id');
    localStorage.removeItem('mb_session_id');
    updateAuthUI();
    // Redirect to pricing page
    window.location.href = '/pricing';
}
```

#### 4.4.2 `updateAuthUI()` — Rich session indicator

**File pattern:** All HTML pages

**Enhanced version — shows email, plan name, last active:**

```javascript
async function updateAuthUI() {
    const jwt = localStorage.getItem('mb_jwt');
    const key = localStorage.getItem('mb_api_key_id');
    const signInBtn = document.getElementById('auth-nav-btn');
    const userMenu = document.getElementById('user-menu');
    const avatarName = document.getElementById('user-avatar-name');
    const avatarCircle = document.getElementById('user-avatar-circle');
    const dropdownEmail = document.getElementById('user-dropdown-email');
    const planLabel = document.getElementById('user-dropdown-plan');
    const sinceEl = document.getElementById('user-dropdown-since');
    const sessionDot = document.getElementById('session-dot');
    const mobileCard = document.getElementById('mobile-user-card');
    const mobileBtn = document.getElementById('auth-mobile-btn');
    const mobileCircle = document.getElementById('mobile-user-circle');
    const mobileEmail = document.getElementById('mobile-user-email');
    const mobilePlan = document.getElementById('mobile-user-plan');
    const mobileSessionDot = document.getElementById('mobile-session-dot');
    const mobileSince = document.getElementById('mobile-user-since');

    if (jwt) {
        try {
            const res = await fetch('/auth/session', {
                headers: { 'Authorization': 'Bearer ' + jwt },
            });
            if (res.ok) {
                const data = await res.json();
                const displayName = data.email || data.name || 'User';
                const initial = displayName[0].toUpperCase();
                
                if (signInBtn) signInBtn.style.display = 'none';
                if (userMenu) userMenu.style.display = 'inline-flex';
                if (avatarName) avatarName.textContent = displayName;
                if (avatarCircle) avatarCircle.textContent = initial;
                if (dropdownEmail) dropdownEmail.textContent = displayName;
                
                // Plan name
                const tierDisplay = data.tier ? data.tier.charAt(0).toUpperCase() + data.tier.slice(1) : 'Free';
                if (planLabel) planLabel.textContent = tierDisplay + ' Plan';
                
                // Last active / session dot
                if (sessionDot) {
                    if (data.last_active) {
                        const lastActive = new Date(data.last_active);
                        const now = new Date();
                        const diffMin = Math.floor((now - lastActive) / 60000);
                        if (diffMin < 5) {
                            sessionDot.className = 'session-dot green';
                            if (sinceEl) sinceEl.textContent = 'Active now';
                        } else if (diffMin < 60) {
                            sessionDot.className = 'session-dot amber';
                            if (sinceEl) sinceEl.textContent = diffMin + ' min ago';
                        } else {
                            sessionDot.className = 'session-dot amber';
                            const hours = Math.floor(diffMin / 60);
                            if (sinceEl) sinceEl.textContent = hours + 'h ago';
                        }
                    } else {
                        sessionDot.className = 'session-dot green';
                        if (sinceEl) sinceEl.textContent = 'Just signed in';
                    }
                }
                
                // Mobile card sync
                if (mobileCircle) mobileCircle.textContent = initial || '👤';
                if (mobileEmail) mobileEmail.textContent = displayName;
                if (mobilePlan) mobilePlan.textContent = tierDisplay + ' Plan';
                if (mobileSessionDot && sessionDot) 
                    mobileSessionDot.className = sessionDot.className;
                if (mobileSince && sinceEl) 
                    mobileSince.textContent = sinceEl.textContent;
                if (mobileCard) mobileCard.style.display = 'block';
                if (mobileBtn) mobileBtn.style.display = 'none';
                
                return;
            }
        } catch (e) {
            console.warn('Session fetch failed, falling back to JWT decode', e);
        }
    }
    
    // Fallback: no JWT or fetch failed — show sign-in button
    if (signInBtn) signInBtn.style.display = 'inline-flex';
    if (userMenu) userMenu.style.display = 'none';
    if (mobileCard) mobileCard.style.display = 'none';
    if (mobileBtn) {
        mobileBtn.style.display = 'flex';
        mobileBtn.onclick = function() { closeMobileNav(); openAuth(); };
    }
    const mobileLabel = document.getElementById('auth-mobile-label');
    if (mobileLabel) mobileLabel.textContent = 'Sign in';
}
```

#### 4.4.3 Pricing Page — Gate subscribe behind auth

**File:** `src/memory_bridge/static/pricing.html`

**Current behavior** (line 690-711):
```javascript
const jwt = localStorage.getItem('mb_jwt');
if (!jwt) {
    window.postAuthCallback = () => checkout(tier);
    openAuth();
    return;
}
```

**Enhanced — detect auth state, show gate message:**
```javascript
async function pricingCta(tier) {
    const jwt = localStorage.getItem('mb_jwt');
    if (!jwt) {
        // Gate: require sign-up before subscribe
        openAuth();
        // After successful auth, postAuthCallback triggers checkout
        window.postAuthCallback = async () => {
            // After sign-up, fetch API key and proceed
            await ensureValidJWT();
            checkout(tier);
        };
        return;
    }
    // ... existing upgrade/downgrade logic
}
```

**Remove `/billing/pre-checkout` call from pricing page init** — the page should never call `pre-checkout` since it requires authentication.

**Update initPricing to show sign-in gate on pricing cards:**
```javascript
(async function initPricing() {
    const jwt = localStorage.getItem('mb_jwt');
    if (!jwt) {
        document.querySelectorAll('.pricing-cta').forEach(btn => {
            btn.textContent = 'Sign in to Subscribe';
            btn.onclick = (e) => { e.preventDefault(); openAuth(); };
        });
        return;
    }
    // ... existing tier detection logic
})();
```

#### 4.4.4 Dashboard — Auth gate before subscribe

**File:** `src/memory_bridge/static/dashboard.html`

The dashboard already has a `dash-auth-gate` element. Ensure that:
- If no JWT is present, the auth gate is shown
- The "Subscribe" / "Upgrade" buttons on the dashboard also check for JWT
- After subscription via Stripe redirect back, the dashboard fetches new tier from `/dashboard/data`

### 4.5 Remove Deprecated Code

#### 4.5.1 Remove `/billing/pre-checkout` endpoint

**File:** `src/memory_bridge/controllers/billing_controller.py` (lines 56-80)

Delete the `create_pre_checkout()` function entirely. The unauthenticated checkout flow is no longer supported.

#### 4.5.2 Remove `/auth/link-subscription` endpoint

**File:** `src/memory_bridge/controllers/auth_controller.py` (lines 291-338)

Delete the `link_subscription()` function entirely. With the new flow, subscriptions are always created for authenticated users.

#### 4.5.3 Remove pending-org from Stripe checkout

**File:** `src/memory_bridge/services/billing_service.py`

The `_handle_checkout_completed` webhook handler already uses `client_reference_id` (which is the org_id from JWT). No changes needed here — just make sure `client_reference_id` is always a real org_id from a JWT, never a `pending-*` org.

#### 4.5.4 Update `/dashboard/recover` endpoint

**File:** `src/memory_bridge/controllers/dashboard_controller.py` (lines 608-708)

Change the recovery flow:
1. User enters email on recovery form
2. Backend sends Auth0 passwordless email with OTP (reuse `/auth/auth0/passwordless/start`)
3. User enters OTP → gets JWT (reuse `/auth/auth0/passwordless/verify`)
4. Return JWT + API key ID (not key value) — user goes to dashboard to manage keys

Remove the Stripe-based recovery path (lines 621-708) and replace with Auth0 passwordless flow.

### 4.6 Auth0 Logout — Required Configuration

**Environment variable addition:**

```
AUTH0_LOGOUT_REDIRECT_URL=https://memorybridge.io/pricing
```

**File:** `src/memory_bridge/services/auth0_service.py`

Add method:
```python
def get_logout_url(self, return_to: str = "") -> str:
    """Build Auth0 logout URL that also ends the SSO session."""
    if not self.enabled:
        return ""
    redirect = return_to or os.environ.get("AUTH0_LOGOUT_REDIRECT_URL", "")
    return (
        f"https://{self.domain}/v2/logout?"
        f"client_id={self.client_id}&"
        f"returnTo={redirect}"
    )
```

### 4.7 i18n Updates

**File:** `src/memory_bridge/static/i18n.js`

Add translation keys:

```javascript
// Session indicator
"session.plan": "{tier} Plan",
"session.active_now": "Active now",
"session.min_ago": "{n} min ago",
"session.hours_ago": "{n}h ago",
"session.last_active": "Last active: {time}",

// Subscribe gate
"pricing.sign_in_to_subscribe": "Sign in to Subscribe",
"pricing.gate_subtitle": "Create an account to subscribe to the {tier} plan.",
"pricing.already_subscribed": "You're already subscribed to {tier}.",

// Logout
"logout.auth0_redirect": "Signing out of Auth0...",
```

---

## 5. Implementation Order

| Phase | Task | Files | Est. Effort |
|-------|------|-------|-------------|
| **1** | Database migration — `user_sessions` table | 2 `.sql` files, 3 repo files | 2h |
| **2** | Add `jti` to JWT + create session in `generate_token()` | `user_service.py` | 1h |
| **3** | `POST /auth/logout` + `GET /auth/session` endpoints | `auth_controller.py` | 2h |
| **4** | Update `logout()` frontend — Auth0 redirect | All 7 HTML files | 2h |
| **5** | Update `updateAuthUI()` — rich session indicator | All 7 HTML files | 2h |
| **6** | Pricing page gating — JWT check before subscribe | `pricing.html` | 1h |
| **7** | Remove deprecated endpoints (`pre-checkout`, `link-subscription`) | `billing_controller.py`, `auth_controller.py` | 0.5h |
| **8** | Update recovery flow — Auth0 passwordless instead of Stripe | `dashboard_controller.py`, `auth0_controller.py` | 2h |
| **9** | i18n key additions | `i18n.js` | 0.5h |
| **10** | Auth0 logout URL config + service method | `auth0_service.py` | 0.5h |
| **11** | Test full flow end-to-end | Manual + integration tests | 3h |

**Total estimated effort: ~16 hours**

---

## 6. Security Considerations

1. **JWT invalidation is best-effort.** Since JWTs are stateless, a stolen token remains valid until expiry. The `jti` check in middleware adds a DB lookup per request — consider caching or rate-limiting the check.

2. **Auth0 logout only clears Auth0 SSO session.** The app-issued JWT in localStorage is cleared client-side. For true server-side invalidation, the `user_sessions.active` flag is the source of truth.

3. **Rate limit `/auth/session`.** This endpoint is called on every page load and `updateAuthUI()` invocation. Cache the response for 30s on the client side to avoid hammering the server.

4. **Never return the API key secret value.** The `/auth/session` endpoint must never expose the plaintext API key. Only return `key_id` and `key_count`.

---

## 7. Backward Compatibility

| Change | Compat? | Mitigation |
|--------|---------|------------|
| Remove `/billing/pre-checkout` | **Breaking** — old frontend clients will get 404 | Update all frontend code first, then remove endpoint in same deploy |
| Remove `/auth/link-subscription` | **Breaking** — old callback URLs | Old `pending_org_id` in URL will show "Page not found" gracefully; redirect old path to `/dashboard` |
| Add `jti` to JWT payload | **Compatible** — existing tokens without `jti` work fine | Middleware treats missing `jti` as "no session tracking" — no error |
| New `user_sessions` table | **Compatible** — existing users have no rows | Session tracking starts from next login only |
| `POST /auth/logout` | **Compatible** — new endpoint | Old `logout()` JS will work but won't call it until updated |

---

## 8. Testing Plan

### 8.1 Sign-Out Flow
1. Sign in via Auth0 (email/passwordless)
2. Verify session dot shows green + email + "Active now"
3. Click Sign Out
4. Verify: Auth0 logout page appears OR redirect to `/pricing`
5. Verify: `localStorage` is empty
6. Revisit `/dashboard` — should show auth gate, not dashboard content

### 8.2 Subscribe Gate
1. Without JWT: click "Subscribe" on pricing page → auth modal opens (not Stripe)
2. Sign up → API key is auto-generated → JWT stored
3. Click "Subscribe" again → Stripe checkout opens (now with auth)
4. Complete checkout → redirect to dashboard → shows new tier immediately

### 8.3 Subscribe-without-Account Prevention
1. Try `POST /billing/checkout` without `Authorization` header → 401
2. Try `POST /billing/pre-checkout` (if not removed yet) → should still work but frontend never calls it
3. After removal: `POST /billing/pre-checkout` → 404, frontend shows "Sign in to Subscribe"

### 8.4 Session Indicator
1. Sign in → navbar shows email + plan name + green dot
2. Wait 10 minutes → amber dot + "10 min ago"
3. Refresh page → info persists
4. Open in private window → sign-in button shows (not logged in)

### 8.5 Recovery Flow
1. Have an existing account with API keys
2. Go to dashboard → click "Forgot key?"
3. Enter email → receive OTP
4. Enter OTP → signed in automatically → dashboard shows keys (not key values)
5. Generate new key if needed

---

## 9. Files Changed Summary

| Category | File | Change Type |
|----------|------|-------------|
| **Migration** | `migrations/sqlite/006_user_sessions.sql` | **NEW** |
| **Migration** | `migrations/postgresql/007_user_sessions.sql` | **NEW** |
| **Repository** | `repository/__init__.py` | Add session methods (abstract) |
| **Repository** | `repository/postgres_repo.py` | Implement session methods |
| **Repository** | `repository/sqlite_repo.py` | Implement session methods |
| **Service** | `services/user_service.py` | Add `jti` to JWT, create session on token generation |
| **Service** | `services/auth0_service.py` | Add `get_logout_url()` method |
| **Controller** | `controllers/auth_controller.py` | Add `POST /logout`, `GET /session`; remove `link-subscription` |
| **Controller** | `controllers/dashboard_controller.py` | Rewrite `/recover` to use Auth0 passwordless |
| **Controller** | `controllers/billing_controller.py` | Remove `pre-checkout` endpoint |
| **Middleware** | `auth.py` | Add optional `jti` revocation check |
| **Frontend** | `static/faq.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/playground.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/demo.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/dashboard.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/index.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/api-docs.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/graph.html` | Update `logout()`, `updateAuthUI()` |
| **Frontend** | `static/pricing.html` | Subscribe gating logic, init updates |
| **Frontend** | `static/i18n.js` | Add session + gate translation keys |
| **Config** | `.env.example` (or docs) | Document `AUTH0_LOGOUT_REDIRECT_URL` |
