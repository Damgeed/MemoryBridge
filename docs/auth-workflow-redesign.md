# Auth Workflow Redesign — Concrete Execution Plan

**Synthesized from:**
- Henry (Architect): `/.hermes/plans/auth-to-subscription-data-flow.md`
- Rex (Critic): Analysis of 14 edge cases in codebase
- Nova (Visionary): `/.hermes/plans/auth-ux-vision.md`

**Status:** Ready to execute
**Deployability:** Each phase is independently deployable via Railway auto-deploy

---

## Phase Order (Priority)

| Phase | Focus | Deploy Time | Risk |
|-------|-------|-------------|------|
| **P1** | Critical bug fixes — paying users falling through cracks | ~20 min | Low |
| **P2** | Auth state UI bugs — double lock, error handling | ~15 min | Low |
| **P3** | User identity in navbar | ~45 min | Low |
| **P4** | Data model hardening + orphand backfill | ~60 min | Medium |
| **P5** | UX polish — logout confirmation, session timeout, pricing auth | ~45 min | Low |
| **P6** | Clean up deprecated endpoints | ~20 min | Low |

---

## Phase 1: Stop Paying Users Falling Through Cracks (CRITICAL)

### Problem
When a user arrives at `/dashboard?session_id=cs_xxx` after Stripe checkout:
- `handleStripeWelcome()` fires immediately at **line 1667** of `dashboard.html`
- It returns early (line 1669), **skipping all auth initialization** (JWT param check, my-key-value fetch, updateAuthUI)
- The user never gets authenticated — they see a loading spinner forever
- The org_id used during checkout may not have a User record

### Backend Changes

#### 1a. Gate `/billing/checkout/{org_id}` — require JWT (remove unauthenticated path)

**File:** `src/memory_bridge/controllers/billing_controller.py`

Replace `create_checkout` (line 57-76, the unauthenticated path) with a redirect or 401:

```python
@router.post("/checkout/{org_id}")
async def create_checkout(
    org_id: str,
    tier: str = "pro",
    billing: BillingService = Depends(_get_billing_service),
):
    """DEPRECATED: Unauthenticated checkout removed. Use POST /billing/checkout instead."""
    raise HTTPException(
        status_code=401,
        detail="You must be signed in to subscribe. Please register first.",
    )
```

**Why:** This was the primary path for anonymous users to create orphan subscriptions. The authenticated `create_checkout_auth` (line 80) already properly gates.

#### 1b. Add `user_id` and `email` to Stripe checkout metadata

**File:** `src/memory_bridge/services/billing_service.py`

In `create_checkout_session()`, add user metadata:

```python
# Before creating session, get user info from auth
# Add to metadata: user_id, email
checkout_session = stripe.checkout.Session.create(
    ...
    metadata={
        "organization_id": organization_id,
        "user_id": user_id,  # NEW
        "email": user_email,  # NEW
        "tier": tier,
    },
)
```

Also update the method signature and the caller in `create_checkout_auth` (billing_controller.py line 98-104) to pass `user_id` from `request.state.auth`.

**File:** `src/memory_bridge/controllers/billing_controller.py` (line 98-110)

```python
auth = getattr(request.state, "auth", None)
if not auth:
    raise HTTPException(status_code=401, detail="You must be signed in...")
org_id = auth.get("project_id") or auth.get("key_id", "")
user_id = auth.get("user_id", "")  # NEW
url = await billing.create_checkout_session(
    organization_id=org_id,
    user_id=user_id,  # NEW
    tier=tier,
)
```

#### 1c. Fix `_find_sub_by_id` — currently always returns None

**File:** `src/memory_bridge/services/billing_service.py` (line 368-375)

Replace the stub with a real implementation. Add a `get_subscription_by_id` method to the repository interface:

```python
async def _find_sub_by_id(self, sub_id: str) -> Optional[Subscription]:
    """Find a subscription by its Stripe ID across all orgs."""
    if not self.repo:
        return None
    try:
        return await self.repo.get_subscription_by_id(sub_id)
    except Exception:
        return None
```

**File:** `src/memory_bridge/repository/__init__.py` (add to interface)

```python
async def get_subscription_by_id(self, sub_id: str) -> Optional[Subscription]:
    """Get a subscription by its Stripe subscription ID."""
```

Implement in both SQLite and PostgreSQL backends.

### Frontend Changes

#### 1d. Fix `handleStripeWelcome` to initialize auth first

**File:** `src/memory_bridge/static/dashboard.html` (line 1665-1739)

Replace the hard return at line 1669 with an auth-aware flow:

```javascript
// Detect Stripe checkout redirect
const sessionId = params.get('session_id');
if (sessionId) {
    // First, check if we have a JWT from Auth0 callback in the same URL (unlikely but possible)
    // Then check localStorage for existing JWT
    if (!jwt) {
        // No existing auth — user needs to log in first
        // BUT: Stripe might have been paid without auth (legacy path)
        // Show a clear message instead of silently failing
        const statusEl = document.getElementById('dash-status');
        statusEl.textContent = '⏳ Checking your subscription...';
        // Try the welcome flow — it may find an existing subscription
        handleStripeWelcome(sessionId);
    } else {
        // User is authenticated — normal welcome flow
        handleStripeWelcome(sessionId);
    }
    return;
}
```

And in `handleStripeWelcome` itself (line 1706), ensure it handles anonymous gracefully:

```javascript
async function handleStripeWelcome(sessionId) {
    const statusEl = document.getElementById('dash-status');
    statusEl.textContent = '✅ Payment confirmed! Setting up your account...';
    
    // Clear pending state
    localStorage.removeItem('mb_pending_org_id');
    localStorage.removeItem('mb_pending_tier');
    window.history.replaceState({}, '', '/dashboard/');
    
    // NEW: Ensure auth is initialized even for anonymous Stripe returns
    // Try to re-hydrate from localStorage or the welcome endpoint
    if (!jwt && !currentApiKey) {
        // Fallback: call /dashboard/welcome to bootstrap
        try {
            const resp = await fetch(`/dashboard/welcome?session_id=${sessionId}`);
            if (resp.ok) {
                const data = await resp.json();
                if (data.key) {
                    localStorage.setItem('mb_api_key', data.key);
                    currentApiKey = data.key;
                }
                if (data.organization_id) {
                    localStorage.setItem('mb_org_id', data.organization_id);
                }
            }
        } catch (e) {
            console.warn('Welcome endpoint fallback failed:', e);
        }
    }
    
    // Poll for subscription...
    // ... (rest of existing code)
}
```

Also fix the `api()` helper function at lines 1136-1145 — remove the dual-fallback that prefers API key over JWT for dashboard calls:

```javascript
function api(method, url, body) {
    const headers = {'Content-Type': 'application/json'};
    const jwt = localStorage.getItem('mb_jwt');
    if (jwt) {
        headers['Authorization'] = 'Bearer ' + jwt;
    } else {
        const key = currentApiKey || localStorage.getItem('mb_api_key') || '';
        if (key) headers['Authorization'] = 'Bearer ' + key;
    }
    // ... rest unchanged
}
```

#### 1e. Fix `/auth/my-key-value` error handling — don't destroy JWT on transient errors

**File:** `src/memory_bridge/static/dashboard.html` (line 1674-1687)

Replace the catch block that silently removes the JWT:

```javascript
try {
    const kvRes = await fetch('/auth/my-key-value', {
        headers: {'Authorization': 'Bearer ' + jwt, 'Content-Type': 'application/json'},
    });
    if (kvRes.ok) {
        const data = await kvRes.json();
        localStorage.setItem('mb_api_key', data.key);
        currentApiKey = data.key;
    } else if (kvRes.status === 401) {
        // JWT genuinely expired or invalid — clear it
        localStorage.removeItem('mb_jwt');
    } else if (kvRes.status === 404) {
        // No API key yet — that's fine, user can generate one
        console.log('No API key found — user needs to generate one');
    } else {
        // Transient server error — keep the JWT, don't nuke it
        console.warn('Failed to fetch API key (status ' + kvRes.status + '), keeping JWT');
    }
} catch (e) {
    // Network error or timeout — KEEP the JWT, don't destroy auth
    console.warn('Network error fetching API key, keeping JWT:', e);
}
```

**IMPORTANT:** This pattern (the `my-key-value` fetch + catch-all removal) is duplicated across ALL 7 pages:
- `dashboard.html` line 1676
- `index.html` line 1410
- `pricing.html` line 1210
- `playground.html` line 1492
- `demo.html` line 1362
- `api-docs.html` line 894
- `graph.html` line 1336

Apply the same fix to ALL of them.

---

## Phase 2: Fix Auth State UI Bugs

### Problem
`updateAuthUI()` requires BOTH `mb_jwt` AND `mb_api_key` to display "Sign out" — users with only a JWT (e.g., just registered, haven't fetched API key yet) see "Sign in" even though they're logged in.

### Frontend Changes

#### 2a. Fix `updateAuthUI()` — JWT-only should show Sign Out

**File:** `src/memory_bridge/static/dashboard.html` (line 1152-1169)

```javascript
function updateAuthUI() {
    const jwt = localStorage.getItem('mb_jwt');
    const key = localStorage.getItem('mb_api_key');
    const btn = document.querySelector('.auth-btn');
    const navCtn = document.querySelector('.auth-btn-container');
    const label = btn ? btn.querySelector('span') : document.getElementById('auth-btn-label');
    const mobileLabel = document.getElementById('auth-mobile-label');
    
    if (jwt && label) {
        // Signed in — JWT is the primary indicator. API key is optional.
        label.textContent = 'Sign out';
        if (mobileLabel) mobileLabel.textContent = 'Sign out';
        btn.onclick = logout;
        btn.title = 'Sign out';
        
        // NEW: If user name/email is available, show it
        const userNameEl = document.getElementById('user-name-display');
        if (userNameEl) {
            // From session data
        }
        
        // Show user identity — replace icon with avatar if name available
        // (Phase 3 will flesh this out fully)
    } else if (label) {
        label.textContent = 'Sign in';
        if (mobileLabel) mobileLabel.textContent = 'Sign in';
        btn.onclick = openAuth;
        btn.title = 'Sign in / Sign up';
    }
}
```

Apply the same fix to ALL 7 pages (search for `function updateAuthUI()` across all HTML files).

#### 2b. Show user email/name after auth if available

**File:** `src/memory_bridge/static/dashboard.html` — after `updateAuthUI()` call at lines 1688/1696:

Rather than just calling `updateAuthUI()`, also hydrate user info:

```javascript
// After successful JWT login
updateAuthUI();
// NEW: Show user info
const userData = JSON.parse(atob(jwt.split('.')[1]));
const userEmail = userData.email || '';
const userName = userData.name || userEmail.split('@')[0] || 'User';
localStorage.setItem('mb_user_name', userName);
localStorage.setItem('mb_user_email', userEmail);
```

#### 2c. No state shown after logout — interactive controls remain visible

**File:** `src/memory_bridge/static/dashboard.html` — `logout()` (line 1171-1181)

Add additional cleanup:

```javascript
function logout() {
    localStorage.removeItem('mb_jwt');
    localStorage.removeItem('mb_api_key');
    localStorage.removeItem('mb_user_name');
    localStorage.removeItem('mb_user_email');
    localStorage.removeItem('mb_org_id');
    currentApiKey = '';
    
    // Clear all interactive dashboard content
    updateAuthUI();
    document.getElementById('dash-status').textContent = '👋 Signed out. Sign in again to continue.';
    document.getElementById('sub-content').innerHTML =
        '<div style="color:var(--text-muted);font-size:0.85rem;">' + __('dash.sub_none') + '</div>';
    document.getElementById('keys-list').innerHTML =
        '<div class="empty-keys"><div class="icon">🔑</div><div>' + __('dash.keys_no_permission') + '</div></div>';
    document.getElementById('key-reveal-area').innerHTML = '';
    document.getElementById('create-key-btn')?.classList.add('hidden');
    document.getElementById('manage-billing-btn')?.classList.add('hidden');
    showToast('👋 Signed out');
}
```

#### 2d. Auth modal loading state for social login

**File:** `src/memory_bridge/static/dashboard.html` — find the `socialLogin()` handler

Add loading state:

```javascript
async function socialLogin(provider) {
    const btn = event.currentTarget;
    btn.disabled = true;
    btn.innerHTML = '<span class="loading-spinner"></span> Connecting...';
    
    try {
        // ... existing social login logic
    } catch (e) {
        btn.disabled = false;
        btn.innerHTML = originalContent;
        showToast('❌ ' + provider + ' login failed. Please try again.');
    }
}
```

---

## Phase 3: Add User Identity to Navbar

### Problem
After signing in, the user never sees their own name, email, or current plan. The nav just toggles between "Sign in" and "Sign out".

### Backend Changes

#### 3a. Enrich `/dashboard/data` with user profile info

**File:** `src/memory_bridge/controllers/dashboard_controller.py` — `get_dashboard_data()` (line 296-336)

Add user info to the response:

```python
@router.get("/data")
async def get_dashboard_data(
    request: Request,
    storage: MemoryRepository = Depends(get_storage),
):
    org_id = _resolve_org(request)
    
    # Get subscription
    sub = None
    try:
        sub = await storage.get_subscription_by_org(org_id)
    except Exception:
        pass
    
    # Get key count
    keys = await storage.list_api_keys()
    user_keys = [k for k in keys if k.get("project_id") == org_id or not k.get("project_id")]
    active_keys = [k for k in user_keys if k.get("is_active", True)]
    
    # NEW: Get user info
    user_name = ""
    user_email = ""
    try:
        auth = getattr(request.state, "auth", None)
        if auth:
            user_id = auth.get("user_id", "")
            if user_id:
                user = await storage.get_user_by_id(user_id)
                if user:
                    user_name = user.get("name", "")
                    user_email = user.get("email", "")
    except Exception:
        pass
    
    # Get memory count
    mem_count = 0
    try:
        memories = await storage.query_memories(limit=1, offset=0)
        mem_count = len(memories)
    except Exception:
        pass
    
    tier = sub.tier if sub else "free"
    if sub and sub.status == "canceled":
        tier = "free"
    
    return {
        "organization_id": org_id,
        "tier": tier,
        "status": sub.status if sub else "active",
        "active_keys": len(active_keys),
        "total_keys": len(user_keys),
        "current_period_end": sub.current_period_end.isoformat() if sub and sub.current_period_end else None,
        # NEW fields:
        "user_name": user_name,
        "user_email": user_email,
        "member_since": user.get("created_at", "") if user else "",
    }
```

Also need to ensure `get_user_by_id` exists on the repository. If not, add it.

### Frontend Changes

#### 3b. Navbar avatar dropdown (HTML)

**File:** `src/memory_bridge/static/dashboard.html` — replace the `.auth-btn` element (line 446-449)

**Before:**
```html
<button class="auth-btn" id="auth-nav-btn" onclick="openAuth()" title="Sign in / Sign up">
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
        <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
    </svg>
    <span id="auth-btn-label">Sign in</span>
</button>
```

**After:**
```html
<div class="auth-btn-container">
    <button class="auth-btn" id="auth-nav-btn" onclick="openAuth()" title="Sign in / Sign up">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M20 21v-2a4 4 0 0 0-4-4H8a4 4 0 0 0-4 4v2"/><circle cx="12" cy="7" r="4"/>
        </svg>
        <span id="auth-btn-label">Sign in</span>
    </button>
    <!-- NEW: Avatar dropdown (hidden by default, shown when logged in) -->
    <div class="avatar-dropdown-wrapper" id="avatar-dropdown" style="display:none;">
        <button class="avatar-btn" onclick="toggleUserMenu()">
            <div class="avatar-circle" id="avatar-circle">U</div>
            <span class="avatar-name" id="avatar-name">User</span>
            <svg class="chevron-down" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"/></svg>
        </button>
        <div class="user-dropdown" id="user-dropdown">
            <div class="dropdown-header">
                <div class="dropdown-email" id="dropdown-email">user@example.com</div>
                <div class="dropdown-plan"><span class="tier-dot free" id="dropdown-tier-dot"></span> <span id="dropdown-plan-name">Free</span></div>
            </div>
            <div class="dropdown-divider"></div>
            <a href="/dashboard/" class="dropdown-item">📊 Dashboard</a>
            <a href="/dashboard/#api-keys" class="dropdown-item">🔑 API Keys</a>
            <a href="/dashboard/#billing" class="dropdown-item">💳 Billing & Plan</a>
            <div class="dropdown-divider"></div>
            <button class="dropdown-item text-red" onclick="confirmLogout()">🚪 Sign Out</button>
        </div>
    </div>
</div>
```

#### 3c. Avatar dropdown CSS

**File:** `src/memory_bridge/static/style.css` — add after existing auth button styles

```css
/* ── Avatar Dropdown ──────────────────────── */
.auth-btn-container { position: relative; display: inline-flex; align-items: center; }

.avatar-btn {
    display: inline-flex; align-items: center; gap: 0.5rem;
    padding: 0.25rem 0.5rem 0.25rem 0.25rem; border-radius: 999px;
    border: 1px solid var(--border-primary); background: var(--bg-glass);
    cursor: pointer; transition: all var(--transition-fast);
}
.avatar-btn:hover { border-color: var(--accent); background: var(--accent-glow); }
.avatar-circle {
    width: 28px; height: 28px; border-radius: 50%;
    background: var(--accent-gradient); color: #fff;
    display: flex; align-items: center; justify-content: center;
    font-size: 0.8rem; font-weight: 700; flex-shrink: 0;
}
.avatar-name { font-size: 0.82rem; font-weight: 600; color: var(--text-primary); }
.avatar-btn .chevron-down { transition: transform 0.2s; }
.avatar-btn.open .chevron-down { transform: rotate(180deg); }

.user-dropdown {
    position: absolute; top: 100%; right: 0; margin-top: 6px;
    min-width: 220px;
    background: var(--bg-glass-strong); backdrop-filter: blur(20px) saturate(1.5);
    border: 1px solid var(--border-primary); border-radius: var(--radius-md);
    box-shadow: var(--shadow-lg); display: none; z-index: 200; overflow: hidden;
}
.user-dropdown.open { display: block; }
.dropdown-header { padding: 0.75rem 1rem; border-bottom: 1px solid var(--border-subtle); }
.dropdown-email { font-size: 0.82rem; color: var(--text-muted); margin-bottom: 0.2rem; }
.dropdown-plan { font-size: 0.78rem; font-weight: 600; display: flex; align-items: center; gap: 0.35rem; }
.tier-dot { width: 8px; height: 8px; border-radius: 50%; }
.tier-dot.free { background: var(--text-faint); }
.tier-dot.starter { background: var(--accent); }
.tier-dot.pro { background: var(--green); }
.tier-dot.enterprise { background: var(--blue); }
.dropdown-item {
    display: flex; align-items: center; gap: 0.6rem;
    padding: 0.6rem 1rem; font-size: 0.85rem;
    color: var(--text-secondary); cursor: pointer; background: none;
    border: none; width: 100%; text-align: left;
    font-family: var(--font-sans); transition: all var(--transition-fast);
}
.dropdown-item:hover { background: var(--btn-secondary-hover); color: var(--text-primary); text-decoration: none; }
.text-red { color: var(--red) !important; }
.text-red:hover { background: rgba(239, 68, 68, 0.1) !important; }
.dropdown-divider { height: 1px; background: var(--border-subtle); }
```

Also add CSS variables in `style.css`:

```css
/* Auth state colors */
--auth-dot-active: #34d399;
--auth-dot-warning: #f59e0b;
--auth-dot-expired: #ef4444;
--auth-dot-inactive: #505068;
--avatar-size: 28px;
--avatar-font: 0.8rem;
```

#### 3d. Avatar dropdown JS functions

**File:** `src/memory_bridge/static/dashboard.html` — add after `updateAuthUI()` (line 1169)

```javascript
// ── Avatar Dropdown ─────────────────────────
function toggleUserMenu() {
    const menu = document.getElementById('user-dropdown');
    const btn = document.querySelector('.avatar-btn');
    if (menu) {
        menu.classList.toggle('open');
        if (btn) btn.classList.toggle('open');
    }
}

// Close dropdown on outside click
document.addEventListener('click', function(e) {
    const container = document.querySelector('.avatar-dropdown-wrapper');
    const menu = document.getElementById('user-dropdown');
    if (container && menu && !container.contains(e.target)) {
        menu.classList.remove('open');
        document.querySelector('.avatar-btn')?.classList.remove('open');
    }
});

function updateAvatarUI(userName, userEmail, tier) {
    const circle = document.getElementById('avatar-circle');
    const nameEl = document.getElementById('avatar-name');
    const emailEl = document.getElementById('dropdown-email');
    const planEl = document.getElementById('dropdown-plan-name');
    const dotEl = document.getElementById('dropdown-tier-dot');
    
    if (circle) {
        const initial = (userName || userEmail || 'U')[0].toUpperCase();
        circle.textContent = initial;
    }
    if (nameEl) nameEl.textContent = userName || userEmail?.split('@')[0] || 'User';
    if (emailEl) emailEl.textContent = userEmail || '';
    if (planEl) planEl.textContent = tier ? tier.charAt(0).toUpperCase() + tier.slice(1) : 'Free';
    if (dotEl) {
        dotEl.className = 'tier-dot ' + (tier || 'free');
    }
}
```

Replace `updateAuthUI()` call sites to also call `updateAvatarUI()` with user data from the JWT payload or dashboard data.

#### 3e. Update `updateAuthUI()` to switch between Sign In button and avatar

```javascript
function updateAuthUI() {
    const jwt = localStorage.getItem('mb_jwt');
    const authBtn = document.getElementById('auth-nav-btn');
    const avatarDropdown = document.getElementById('avatar-dropdown');
    
    if (jwt) {
        // Show avatar dropdown, hide sign-in button
        if (authBtn) authBtn.style.display = 'none';
        if (avatarDropdown) avatarDropdown.style.display = 'inline-flex';
        
        // Hydrate from localStorage (set by auth success path)
        const userName = localStorage.getItem('mb_user_name') || '';
        const userEmail = localStorage.getItem('mb_user_email') || '';
        const tier = localStorage.getItem('mb_tier') || 'free';
        updateAvatarUI(userName, userEmail, tier);
    } else {
        // Show sign-in button, hide avatar
        if (authBtn) authBtn.style.display = 'inline-flex';
        if (avatarDropdown) avatarDropdown.style.display = 'none';
        
        // Also update the sign-in button label
        const label = document.getElementById('auth-btn-label');
        if (label) label.textContent = 'Sign in';
        if (authBtn) { authBtn.onclick = openAuth; authBtn.title = 'Sign in / Sign up'; }
        
        const mobileLabel = document.getElementById('auth-mobile-label');
        if (mobileLabel) mobileLabel.textContent = 'Sign in';
    }
}
```

#### 3f. Apply navbar changes to all pages

Apply the same avatar dropdown HTML/CSS/JS pattern to:
- `index.html` (~line 446 area)
- `pricing.html` 
- `playground.html`
- `demo.html`
- `api-docs.html`
- `graph.html`

Use `search_files` for `.auth-btn` or `auth-btn-label` to find all instances.

---

## Phase 4: Data Model Hardening + Orphan Backfill

### Problem
No foreign key relationships between User, Subscription, and API Key tables. Orphaned subscriptions exist without matching User records.

### Backend Changes

#### 4a. Create migration script

**New file:** `src/memory_bridge/migrations/v15_add_fk_constraints.py`

```sql
-- Step 1: Find orphaned subscriptions (no matching user)
SELECT s.organization_id
FROM subscriptions s
LEFT JOIN users u ON s.organization_id = u.organization_id
WHERE u.id IS NULL;

-- Step 2: Create placeholder users for orphaned subscriptions
INSERT INTO users (id, email, name, organization_id, password_hash, created_at, updated_at)
SELECT
    gen_random_uuid()::text,
    CONCAT('migrated-', s.organization_id, '@system.local'),
    CONCAT('Migrated User (', LEFT(s.organization_id, 8), ')'),
    s.organization_id,
    '',
    NOW(),
    NOW()
FROM subscriptions s
LEFT JOIN users u ON s.organization_id = u.organization_id
WHERE u.id IS NULL;

-- Step 3: Add NOT VALID FK constraints (no table locks)
ALTER TABLE subscriptions
  ADD CONSTRAINT fk_subscriptions_org
  FOREIGN KEY (organization_id) REFERENCES users(organization_id)
  NOT VALID;

ALTER TABLE api_keys
  ADD CONSTRAINT fk_api_keys_org
  FOREIGN KEY (project_id) REFERENCES users(organization_id)
  NOT VALID;

-- Step 4: Validate constraints (background-safe)
ALTER TABLE subscriptions VALIDATE CONSTRAINT fk_subscriptions_org;
ALTER TABLE api_keys VALIDATE CONSTRAINT fk_api_keys_org;
```

**Important:** Wrap this in a Python migration that:
1. Checks if constraints already exist (idempotent)
2. Handles the SQLite backend (which doesn't support `NOT VALID`)
3. Logs the count of orphaned subscriptions found

For SQLite fallback:
```python
if self.backend_type == "sqlite":
    # SQLite requires table recreation for FK constraints
    # Alternative: enforce in application code + add UNIQUE index
    await connection.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_org_id 
        ON subscriptions(organization_id)
    """)
```

#### 4b. Refactor `passwordless_verify` to be the single Atomic registration path

**File:** `src/memory_bridge/controllers/auth0_controller.py` — `passwordless_verify()` (line 241-378)

Ensure the registration block (line 314-343) creates ALL records atomically:

```python
# Wrap in try/except that rolls back on failure
# Currently each step is separate try/except — consolidate
org_id = str(uuid.uuid4())
try:
    user = User(
        email=email,
        password_hash="",
        name=name,
        organization_id=org_id,
        auth0_sub=auth0_sub,
    )
    result = await storage.create_user(user)
    user_id = result.get("id", "")
    
    # Create free subscription + API key in same logical transaction
    # (storage layer should handle this atomically if possible)
    sub = Subscription(id=f"free-{org_id[:8]}", organization_id=org_id, tier="free", status="active")
    await storage.store_subscription(sub)
    
    key_label = f"auth0-key-{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    key_result = await storage.create_api_key(label=key_label, project_id=org_id)
    
    logger.info("Passwordless signup: new user %s (org=%s)", email, org_id)
except Exception as e:
    logger.error("Passwordless user creation failed: %s", e)
    raise HTTPException(status_code=500, detail="Could not create user account")
```

Same atomic pattern for `auth0_callback()` (line 148-178).

#### 4c. Mark `users.organization_id` as UNIQUE

**File:** Update the User model or migration to add:
```sql
ALTER TABLE users ADD CONSTRAINT users_org_id_unique UNIQUE (organization_id);
```

(Or add to the migration script above.)

---

## Phase 5: UX Polish

### Problem
Logout is instant with no confirmation. No session timeout warning. Pricing page doesn't know if you're logged in.

### Frontend Changes

#### 5a. Logout confirmation modal

**File:** `src/memory_bridge/static/dashboard.html` — Add modal HTML near the auth modal section

```html
<!-- Logout Confirmation Modal -->
<div class="modal-overlay" id="logout-modal" style="display:none;">
    <div class="modal" style="max-width:380px;">
        <div class="modal-header">
            <h3>🚪 Sign Out</h3>
        </div>
        <div class="modal-body">
            <p>Are you sure you want to sign out of Memory Bridge?</p>
            <p style="font-size:0.82rem;color:var(--text-muted);">
                Your API keys will continue to work until revoked.
            </p>
        </div>
        <div class="modal-footer" style="display:flex;gap:0.5rem;justify-content:flex-end;">
            <button class="btn btn-secondary" onclick="closeLogoutModal()">Cancel</button>
            <button class="btn btn-danger" onclick="executeLogout()">Sign Out →</button>
        </div>
    </div>
</div>
```

```css
.btn-danger { background: var(--red); color: white; border: none; }
.btn-danger:hover { background: var(--red-dark); }
```

**JS:**
```javascript
function confirmLogout() {
    document.getElementById('user-dropdown')?.classList.remove('open');
    document.getElementById('logout-modal').style.display = 'flex';
}
function closeLogoutModal() {
    document.getElementById('logout-modal').style.display = 'none';
}
function executeLogout() {
    closeLogoutModal();
    logout();
}
```

Replace `btn.onclick = logout;` in `updateAuthUI()` with `btn.onclick = confirmLogout;`.

#### 5b. Session timeout warning (background JWT monitor)

**File:** `src/memory_bridge/static/dashboard.html` — Add after auth init section

```javascript
// ── Session Monitor ─────────────────────────
function startSessionMonitor() {
    const jwt = localStorage.getItem('mb_jwt');
    if (!jwt) return;
    
    try {
        const payload = JSON.parse(atob(jwt.split('.')[1]));
        const exp = payload.exp * 1000;
        const now = Date.now();
        const remaining = exp - now;
        
        if (remaining <= 0) {
            handleSessionExpired();
            return;
        }
        
        // Warn at 5 minutes
        const warnAt = Math.max(0, remaining - 5 * 60 * 1000);
        setTimeout(() => showSessionWarning(), warnAt);
        
        // Expire
        setTimeout(() => handleSessionExpired(), remaining);
    } catch (e) {
        // Invalid JWT — ignore
    }
}

function showSessionWarning() {
    showToast('⏰ Your session will expire in 5 minutes. Please save your work.');
    const avatarBtn = document.querySelector('.avatar-btn');
    if (avatarBtn) {
        avatarBtn.style.borderColor = 'var(--auth-dot-warning)';
    }
}

function handleSessionExpired() {
    showToast('⌛ Your session has expired. Please sign in again.');
    localStorage.removeItem('mb_jwt');
    updateAuthUI();
}

// Call after successful auth
```

Call `startSessionMonitor()` in the DOMContentLoaded handler after successful auth init.

#### 5c. Pricing page auth awareness

**File:** `src/memory_bridge/static/pricing.html` — Add auth check in DOMContentLoaded

```javascript
document.addEventListener('DOMContentLoaded', () => {
    const jwt = localStorage.getItem('mb_jwt');
    const key = localStorage.getItem('mb_api_key');
    
    if (jwt || key) {
        fetch('/dashboard/data', {
            headers: { 'Authorization': 'Bearer ' + (jwt || key) }
        })
        .then(r => r.json())
        .then(data => {
            renderPricingWithPlan(data.tier);
        })
        .catch(() => {
            renderPricingAnonymous();
        });
    } else {
        renderPricingAnonymous();
    }
});

function renderPricingWithPlan(currentTier) {
    // For each pricing card:
    // - If card tier == currentTier: show "Your Current Plan ✓" (disabled)
    // - If card tier > currentTier: show "Upgrade →" (direct to Stripe)
    // - If card tier < currentTier: show "Downgrade" (confirm modal)
    document.querySelectorAll('.pricing-card').forEach(card => {
        const cardTier = card.dataset.tier;
        const cta = card.querySelector('.pricing-cta');
        if (cardTier === currentTier) {
            cta.textContent = '✓ Your Plan';
            cta.classList.add('current-plan');
            cta.disabled = true;
        } else if (isHigherTier(cardTier, currentTier)) {
            cta.textContent = 'Upgrade →';
            cta.onclick = () => upgradeTo(cardTier);
        } else {
            cta.textContent = 'Downgrade';
            cta.classList.add('downgrade-btn');
            cta.onclick = () => confirmDowngrade(cardTier);
        }
    });
}

function renderPricingAnonymous() {
    document.querySelectorAll('.pricing-card').forEach(card => {
        const cta = card.querySelector('.pricing-cta');
        const cardTier = card.dataset.tier;
        if (cardTier === 'free') {
            cta.textContent = 'Try Free →';
            cta.onclick = () => openAuth();
        } else {
            cta.textContent = 'Subscribe';
            cta.onclick = () => openAuth();  // Must sign in first
        }
    });
}
```

#### 5d. Extract shared auth modal (de-duplication)

**New file:** `src/memory_bridge/static/auth-modal.html`

Extract the ~200+ lines of auth modal HTML from `dashboard.html` (or any page) into this shared partial. Include the entire modal markup (div#auth-modal and all inner content).

Then in each page, replace the inline auth modal with:

```html
<div id="auth-mount"></div>
<script>
    // Load shared auth modal on DOMContentLoaded
    fetch('/static/auth-modal.html')
        .then(r => r.text())
        .then(html => {
            document.getElementById('auth-mount').innerHTML = html;
            initAuthModal();  // Bind event handlers
        })
        .catch(() => {
            // Fallback: auth modal already inlined
        });
</script>
```

**Pages to modify:**
- `index.html`
- `pricing.html`
- `dashboard.html`
- `playground.html`
- `demo.html`
- `api-docs.html`
- `graph.html`

---

## Phase 6: Clean Up Deprecated Endpoints

### Problem
`/dashboard/free-signup` creates unclaimable accounts. `/dashboard/welcome` creates orgs without User records.

### Backend Changes

#### 6a. Deprecate `/dashboard/free-signup`

**File:** `src/memory_bridge/controllers/dashboard_controller.py` (line 354)

Step 1 — Add deprecation header:
```python
@router.post("/free-signup")
async def free_signup(...):
    from fastapi.responses import JSONResponse
    resp = JSONResponse(content={...})
    resp.headers["X-Deprecated"] = "true"
    resp.headers["X-Deprecation-Message"] = "Free signup is deprecated. Use Auth0 passwordless registration at POST /auth/auth0/passwordless/verify"
    return resp
```

Step 2 (after 1 week) — Change to redirect:
```python
@router.post("/free-signup")
async def free_signup(...):
    raise HTTPException(
        status_code=410,
        detail="Free signup endpoint is removed. Please use the Auth0 passwordless flow at /auth/auth0/passwordless/start",
    )
```

Step 3 (after 2 weeks) — Remove the endpoint and its handler entirely.

#### 6b. Deprecate `/dashboard/welcome`

**File:** `src/memory_bridge/controllers/dashboard_controller.py` (line 94)

Step 1 — Add deprecation logging:
```python
@router.get("/welcome")
async def welcome_setup(...):
    logger.warning("Deprecated /dashboard/welcome called — session=%s org=%s", session_id, organization_id)
    # ... existing logic stays for backward compat
```

Also update the frontend `handleStripeWelcome()` (dashboard.html line 1706) to NOT call `/dashboard/welcome` when the user is already authenticated — let the Stripe webhook handle it:

```javascript
async function handleStripeWelcome(sessionId) {
    // NEW: If authenticated, skip /dashboard/welcome and just poll
    const jwt = localStorage.getItem('mb_jwt');
    if (jwt) {
        // Webhook will set the subscription — just poll /dashboard/data
        statusEl.textContent = '✅ Payment confirmed! Setting up your account...';
        pollForUpgrade(sessionId);
        return;
    }
    // Legacy fallback for anonymous users
    // ... existing code
}
```

Step 2 (after full migration) — Remove the endpoint.

---

## File Change Summary

### Backend Files

| File | Phase | Change |
|------|-------|--------|
| `controllers/billing_controller.py` | P1 | Gate `/checkout/{org_id}`, pass user_id to checkout |
| `services/billing_service.py` | P1 | Fix `_find_sub_by_id`, add user_id/email to Stripe metadata |
| `controllers/dashboard_controller.py` | P1 | Deprecate welcome (step 1) |
| `controllers/dashboard_controller.py` | P3 | Enrich `/dashboard/data` with user_name, user_email |
| `controllers/dashboard_controller.py` | P6 | Deprecate free-signup, remove welcome |
| `controllers/auth0_controller.py` | P4 | Atomic registration in passwordless_verify + auth0_callback |
| `repository/__init__.py` | P1 | Add `get_subscription_by_id()` to interface |
| `repository/sqlite_backend.py` | P1 | Implement `get_subscription_by_id()` |
| `repository/postgres_backend.py` | P1 | Implement `get_subscription_by_id()` |
| `migrations/v15_add_fk_constraints.py` | P4 | FK constraints + orphan backfill |
| `auth.py` | P2 | (No changes needed — middleware works correctly already) |

### Frontend Files

| File | Phase | Change |
|------|-------|--------|
| `static/dashboard.html` | P1 | Fix my-key-value error handling, fix handleStripeWelcome auth skip |
| `static/dashboard.html` | P2 | Fix updateAuthUI double-lock, logout cleanup |
| `static/dashboard.html` | P3 | Avatar dropdown HTML/JS/CSS, updateAuthUI rewrite |
| `static/dashboard.html` | P5 | Logout confirm modal, session monitor |
| `static/index.html` | P1+P2+P3 | Same fixes as dashboard.html (auth, avatar, updateAuthUI) |
| `static/pricing.html` | P1+P2+P3+P5 | Same fixes + auth-aware pricing CTAs |
| `static/playground.html` | P1+P2+P3 | Same fixes as dashboard.html |
| `static/demo.html` | P1+P2+P3 | Same fixes as dashboard.html |
| `static/api-docs.html` | P1+P2+P3 | Same fixes as dashboard.html |
| `static/graph.html` | P1+P2+P3 | Same fixes as dashboard.html |
| `static/style.css` | P3+P5 | Avatar dropdown CSS, session colors, logout modal |
| `static/auth-modal.html` | P5 | NEW: Shared auth modal partial (extracted from 7 pages) |

---

## Deployment Notes

### Railway Auto-Deploy
Each phase is independently deployable. The recommended deployment order:

1. **Phase 1** → Deploy immediately (fixes money-losing bug)
2. **Phase 2** → Deploy after Phase 1 (improves auth state correctness)
3. **Phase 3** → Deploy after Phase 2 (relies on auth state being correct)
4. **Phase 4** → Deploy after Phase 3 (schema migration — run in maintenance window)
5. **Phase 5** → Deploy after Phase 4 (UI polish on stable foundation)
6. **Phase 6** → Deploy after Phase 4+ (safe to remove after data is clean)

### Backward Compatibility
- Phase 1 + Phase 2 are safe to deploy together (no schema changes)
- Phase 4 requires a migration script — run manually via Railway shell or admin endpoint
- Phase 6 should wait 1-2 weeks after Phase 4 to ensure all users have been migrated

### Testing Checklist (per phase)
- [ ] Existing users can sign in via Auth0 passwordless
- [ ] New users can sign up and see their dashboard
- [ ] Stripe checkout flow completes end-to-end
- [ ] API keys still work for programmatic access
- [ ] JWT expiration → graceful re-auth prompt
- [ ] Logout clears all state properly
- [ ] `/dashboard/data` returns user_name and user_email (Phase 3+)
