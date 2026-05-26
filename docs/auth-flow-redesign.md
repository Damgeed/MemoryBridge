# Memory Bridge Auth Flow Redesign

> **Authors**: Henry (AI Expert), Fred (Co-founder), Nova (Visionary), Rex (Critic)
> **Date**: 2026-05-26
> **Status**: Proposed Design Document

---

## 1. Existing Flow Analysis

### 1.1 End-to-End Flow Map

#### Auth → Subscribe Flow (Current)

```
User clicks "Subscribe" on /pricing
  → pricingCta(tier) checks localStorage for mb_jwt
      ├─ No JWT → openAuth() modal
      │        → User enters email
      │        → POST /auth/auth0/passwordless/start
      │        → Auth0 sends 6-digit code
      │        → User enters code
      │        → POST /auth/auth0/passwordless/verify
      │        │  Backend: exchanges code w/ Auth0 → gets id_token
      │        │  Backend: get_user_by_email(email)
      │        │     ├─ Found → existing user, return JWT
      │      ──  └─ Not found → create User + org_id + free Subscription + API key → return JWT
      │        → Frontend: stores mb_jwt + mb_api_key in localStorage
      │        → updateAuthUI() shows avatar dropdown
      │        → User must manually click "Subscribe" again (CTAs reset)
      └─ Has JWT → POST /billing/checkout?tier=X
                → Requires Auth middleware → JWT decoded → project_id (org_id) extracted
                → Stripe checkout URL returned
                → User pays on Stripe
                → Stripe redirects to /dashboard?session_id=cs_xxx
                → handleStripeWelcome(sessionId) polls dashboard/data
                → Stripe webhook checkout.session.completed fires
                → Subscription stored in DB linked to org_id
```

#### Recovery Flow (Current)

```
User loses JWT (cleared browser data, different device, JWT expired)
  → Goes to /pricing or /
  → No "Sign In" button visible on nav (only inside auth modal)
  → Clicks "Subscribe" → auth modal opens
  → Enters email → gets code → verifies
  → Backend: find or create → if existing user, returns JWT
  → JWT points to same org_id → subscription found
  → BUT: user doesn't know they should just enter their email again
  → Alternative: /dashboard/recover?email=X (no code verification!)
      → Looks up Stripe customer by email
      → Returns API key directly (security concern)
```

### 1.2 Identified Failure Points

| # | Failure Point | Severity | Description |
|---|--------------|----------|-------------|
| FP1 | **No persistent "Sign In" button** | HIGH | The "Sign In" / "Sign Up" button only appears inside the auth modal, not in the navbar. A user who has an account has no obvious way to sign back in. |
| FP2 | **JWT expiry too short (60 min)** | HIGH | `jwt_expire_minutes = 60` means a user who subscribes and returns the next day is logged out. No refresh-token mechanism exists. |
| FP3 | **No distinction between "new" vs "returning" user** | MEDIUM | The backend uses `get_user_by_email` → find or create. This works, but the frontend gives zero feedback about whether this is a first-time signup or a returning sign-in. |
| FP4 | **Recovery flow bypasses email verification** | HIGH | `POST /dashboard/recover?email=X` queries Stripe directly with just an email — no OTP verification. Anyone who knows your email could theoretically recover your API key (though Stripe limits this). |
| FP5 | **User locked out if both JWT and API key are lost** | CRITICAL | If browser data is cleared, both `mb_jwt` and `mb_api_key` are gone. There's no "sign in" reachable from the navbar. The user is effectively locked out of their paid account. |
| FP6 | **Double-click needed to subscribe after auth** | MEDIUM | User clicks "Subscribe" → auth modal → signs up → modal closes → user must click "Subscribe" *again*. The modal doesn't chain into checkout. |
| FP7 | **No session persistence across devices** | MEDIUM | JWT is stored in localStorage only. No cross-device session. User on a new device must re-authenticate (which works but is confusing without a visible sign-in option). |
| FP8 | **Avatar dropdown not on all pages** | LOW | Confirm: avatar dropdown exists in nav on index.html, dashboard.html, pricing.html. Should verify all pages (playground, graph, api-docs, faq). |

### 1.3 Root Cause Analysis

The fundamental problem is **not technical** — the backend already does the right thing (passwordless OTP, find-or-create user). The problems are:

1. **UX/HCI**: No visible "sign in" affordance in the persistent navigation
2. **Session Management**: JWT expiry is too aggressive for a subscription product
3. **Mental Model**: Users think they "created a password" or "registered" — they don't understand passwordless semantics where the email IS the credential
4. **Recovery Over-engineering**: The Stripe-based recovery flow adds complexity without solving the real problem

---

## 2. Proposed Flow Design

### 2.1 Core Insight: Sign-up ≡ Sign-in

> **The fundamental insight**: Since we use passwordless/magic-link OTP, there's no password to "forget." A user just enters their email → gets a code → is verified. So "signing in" IS the same as "signing up" — the system creates a User on first verification and returns JWT on subsequent verifications.

This means:
- **No separate registration vs login screens** — one unified flow
- **No "Forgot password"** — there is no password
- The only UI question is "first time?" vs "welcome back!" — both handled by the same code path

### 2.2 Unified Auth Flow (Redesigned)

```
┌─────────────────────────────────────────────────────────┐
│                    Landing / Any Page                    │
│  Navbar: [Sign In] button (always visible, right side)  │
└────────────────────┬────────────────────────────────────┘
                     │ click
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Auth Modal (Passwordless)                   │
│  "Sign in to Memory Bridge"                              │
│  [Continue with Google]  [Continue with Apple]           │
│  ─────────── or ───────────                              │
│  [Enter your email  ]                                    │
│  [Continue →]                                            │
└────────────────────┬────────────────────────────────────┘
                     │ email submitted
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Code Verification                           │
│  "We sent a 6-digit code to j***@example.com"            │
│  [ Enter code ]   [Verify →]                             │
│  Didn't get it? [Send new code]                          │
└────────────────────┬────────────────────────────────────┘
                     │ code verified
                     ▼
┌─────────────────────────────────────────────────────────┐
│           Backend: /auth/auth0/passwordless/verify       │
│                                                          │
│  1. Exchange code w/ Auth0 → get id_token               │
│  2. get_user_by_email(email)                             │
│     ├─ EXISTS: update last_login_at, return JWT          │
│     │  Response: { token, user, is_new: false }          │
│     └─ NEW: create User + org_id + free Subscription     │
│        + API key, return JWT                             │
│        Response: { token, user, is_new: true }           │
│                                                          │
│  3. JWT payload: { sub, email, name,                     │
│                    project_id (org_id),                   │
│                    iat, exp: 30 days }                    │
└────────────────────┬────────────────────────────────────┘
                     │ JWT returned
                     ▼
┌─────────────────────────────────────────────────────────┐
│              Frontend: Post-Verification                  │
│  • Store JWT in localStorage (mb_jwt)                    │
│  • Store API key in localStorage (mb_api_key)            │
│  • updateAuthUI() → show avatar dropdown                 │
│  • If user came from "Subscribe" CTA:                    │
│    → Auto-chain: immediately redirect to Stripe checkout │
│  • If user came from "Sign In" button:                   │
│    → Redirect to dashboard                               │
│  • Show toast: "Welcome back!" or "Account created!"     │
└─────────────────────────────────────────────────────────┘
```

### 2.3 Subscription Gating

The **current** `POST /billing/checkout` endpoint correctly requires authentication:

```python
# billing_controller.py line 91-96
auth = getattr(request.state, "auth", None)
if not auth:
    raise HTTPException(status_code=401, ...)
```

**No changes needed** for the gating itself. However:

1. **Auto-chain after auth**: When a user clicks "Subscribe" → auth modal opens → they verify → the system should NOT just close the modal. It should immediately proceed with the checkout.
2. **org_id consistency**: The JWT's `project_id` is the org_id. The subscription is created under this org_id via Stripe's `client_reference_id` and `metadata.organization_id`. This is correct.

### 2.4 Session Persistence

**Current**: JWT expires in 60 minutes (`jwt_expire_minutes = 60`)

**Proposed**: JWT expires in **30 days** for subscription users

```python
# In user_service.py, generate_token()
# Check if user has an active subscription → use longer expiry
if user.get("has_subscription"):
    exp = now + timedelta(days=30)
else:
    exp = now + timedelta(minutes=settings.jwt_expire_minutes)
```

Or simpler: just set `jwt_expire_minutes = 43200` (30 days) globally. Since passwordless auth is already stateless and secure (the OTP is the second factor), a 30-day JWT is reasonable. The user can always re-authenticate by entering their email again.

---

## 3. Recovery Flow (Redesigned)

### 3.1 The Simplified Recovery

Since email IS the identity and passwordless OTP IS the authentication, recovery is trivial:

```
User: "I lost my account"
  → Clicks "Sign In" in navbar (now always visible)
  → Enters email → gets code → verifies
  → Backend: get_user_by_email → FOUND → returns JWT
  → Frontend loads dashboard → subscription found via org_id
  → Done.
```

**No separate "recovery" flow needed.** Just sign in.

### 3.2 Why This Works

- The backend already stores `stripe_customer_id` on the Subscription record (set by Stripe webhook)
- When the user re-authenticates, the JWT contains the same `project_id` (org_id)
- `GET /dashboard/data` resolves the org_id from the JWT and returns the subscription tier
- The subscription is linked to the org_id, not to the browser session

### 3.3 What to Do With the Old Recovery Endpoint

**Deprecate** `POST /dashboard/recover` and `POST /dashboard/restore-subscription`. These were workarounds for the missing sign-in flow. With a persistent sign-in button and proper JWT handling, they're unnecessary.

**Keep as fallback only**: If someone truly has a Stripe subscription but no User record (edge case from before the fix), the Stripe webhook should have created the subscription. The webhook handler `_handle_checkout_completed` already stores the subscription linked to the org_id from `client_reference_id`.

### 3.4 Edge Case: Stripe Subscriber Without User Record

If somehow a subscription exists (from Stripe webhook) but no User record exists:
1. User signs in with email → get_user_by_email returns None → new User is created
2. BUT: The new User gets a NEW org_id, not the one from the Stripe subscription
3. **Fix**: On sign-in, if user is new, check if a Subscription exists with this email's Stripe customer:
   - Look up by email in Stripe → get stripe_customer_id
   - Find subscription by stripe_customer_id → get existing org_id
   - Assign the existing org_id to the new User row

This is an edge case that should rarely happen with the redesigned flow.

---

## 4. Visual Indicator of Logged-in State

### 4.1 Current State

The avatar dropdown already exists in the nav bar on:
- `index.html`
- `dashboard.html`
- `pricing.html`
- `playground.html`
- `api-docs.html`
- `graph.html`
- `faq.html`

The `updateAuthUI()` function:
- Hides the "Sign In" button
- Shows the avatar circle with user's initial
- Shows email in avatar name
- Dropdown has: email display, "Sign Out" option

### 4.2 What Needs to Change

| Aspect | Current | Proposed |
|--------|---------|----------|
| Sign In button visibility | Only in auth modal | Always visible in navbar on ALL pages |
| Avatar dropdown | Shows initial + email | Same + show plan tier (from decoded JWT or /dashboard/data) |
| Sign Out | Works via `logout()` | Same — keep as is |
| Session status text | Shows after actions | Show "Signed in as user@email.com" in avatar dropdown subtitle |
| Post-auth redirect | Reloads current page | If subscribed → dashboard. If signed in → dashboard. If came from CTA → chain action. |

### 4.3 Mobile Nav

The mobile nav already has:
- "Sign in" / "Sign out" label in mobile hamburger menu (index.html line 656)
- This correctly toggles between `openAuth()` and `showLogoutConfirm()`

No changes needed for mobile — this is already well-implemented.

### 4.4 Design Spec for Navbar

```
┌─────────────────────────────────────────────────────────────┐
│  [Logo]  Product  Docs  Pricing               [Sign In] 🔑  │  ← When logged out
│  [Logo]  Product  Docs  Pricing    [👤 j***@... ▼]         │  ← When logged in
│                                        ┌──────────┐         │
│                                        │ j***@e... │         │
│                                        │ Free Plan │         │
│                                        │───────────│         │
│                                        │ Dashboard │         │
│                                        │ Sign Out  │         │
│                                        └──────────┘         │
└─────────────────────────────────────────────────────────────┘
```

---

## 5. Recommended Implementation Changes

### 5.1 Backend Changes

| # | Change | File | Priority | Effort |
|---|--------|------|----------|--------|
| B1 | **Increase JWT expiry to 30 days** | `config.py:35` → `jwt_expire_minutes=43200` | P0 | 5 min |
| B2 | **Add `is_new` flag to verify response** | `auth0_controller.py:369-378` — add `"is_new": existing_user is None` to the response | P0 | 15 min |
| B3 | **Add `has_subscription`/tier to verify response** | `auth0_controller.py` — after finding/creating user, check if subscription exists, include in response | P0 | 20 min |
| B4 | **Deprecate `POST /dashboard/recover`** | `dashboard_controller.py:483-563` — return 410 Gone with message "Use Sign In instead" | P1 | 10 min |
| B5 | **Add `last_login_at` to User model** | `models.py:75` — add field `last_login_at: Optional[datetime] = None` | P2 | 15 min |
| B6 | **Track `last_login_at` on verify** | `auth0_controller.py:312` — update `last_login_at` when existing user logs in | P2 | 10 min |
| B7 | **Edge case: assign existing org_id on new User creation** | `auth0_controller.py:314-329` — before creating new User, check Stripe for existing subscription by email → reuse org_id | P2 | 30 min |
| B8 | **Add `POST /auth/me` endpoint** | New — returns user info, subscription, org_id from JWT. Avoids needing to parse JWT client-side. | P2 | 30 min |

#### B1 Detail: JWT Expiry

```python
# config.py line 35
jwt_expire_minutes: int = 43200  # 30 days (60 * 24 * 30)
```

#### B2 Detail: `is_new` flag

In `auth0_controller.py:369-378`, change the return to include:

```python
return {
    "token": jwt_token,
    "api_key": api_key,
    "is_new": existing_user is None,  # ← add this
    "user": { ... },
}
```

#### B3 Detail: Include subscription info

```python
# After getting user_data, before generating token
sub = await storage.get_subscription_by_org(org_id)
tier = sub.tier if sub else "free"
# Then in response:
return {
    "token": jwt_token,
    "api_key": api_key,
    "is_new": existing_user is None,
    "tier": tier,
    "user": { ... },
}
```

### 5.2 Frontend Changes

| # | Change | File(s) | Priority | Effort |
|---|--------|---------|----------|--------|
| F1 | **Make "Sign In" button always visible in navbar** | All 7 HTML pages: find `.auth-btn` or `#auth-nav-btn` and ensure it's never hidden by default. Currently it's hidden when JWT exists — keep that. But it should be visible when NO JWT exists. | P0 | 30 min |
| F2 | **Chain auth → checkout** | `pricing.html` `pricingCta()` + `verifyCode()`: after successful verification, if user was trying to subscribe, auto-call `checkout(tier)` | P0 | 30 min |
| F3 | **Show plan tier in avatar dropdown** | All HTML pages: add tier display in user dropdown. Use `data.tier` from verify response or fetch from `/dashboard/data` | P0 | 20 min |
| F4 | **Use `is_new` flag for onboarding** | All HTML pages: if `data.is_new === true`, show "Welcome!" toast + redirect to dashboard/getting-started. If false, show "Welcome back!" toast. | P1 | 15 min |
| F5 | **Handle JWT expiry gracefully** | All HTML pages: when API returns 401, DON'T immediately nuke auth. Show "Session expired. Please sign in again." with a button/link that opens auth modal. | P1 | 20 min |
| F6 | **Remove `handleStripeWelcome` polling** | `dashboard.html:2224-2347` — simplify. After Stripe checkout, the webhook handles it. Just redirect to dashboard and show "Plan activated" once /dashboard/data shows the new tier. | P2 | 30 min |
| F7 | **Remove recovery form from UI** | All HTML pages: remove references to `/dashboard/recover` | P2 | 15 min |
| F8 | **Add `POST /auth/me` call on page load** | All HTML pages: on DOMContentLoaded, if JWT exists, call `/auth/me` to validate + get user info + subscription status | P2 | 30 min |

#### F1 Detail: Sign In Button Always Visible

Currently in `updateAuthUI()`:
```javascript
// Line 1597-1618 in dashboard.html
if (jwt && userMenu && signInBtn) {
    signInBtn.style.display = 'none';       // Hide sign-in
    userMenu.style.display = 'inline-flex';  // Show avatar
} else {
    signInBtn.style.display = 'inline-flex'; // Show sign-in
    userMenu.style.display = 'none';         // Hide avatar
}
```

This is already correct — but the **issue is that on pages without `updateAuthUI()` being called on load, the sign-in button might not render**. Audit all 7 HTML files to ensure:
1. The Sign In button HTML exists in the nav
2. `updateAuthUI()` is called on DOMContentLoaded
3. No CSS hides it by default

#### F2 Detail: Auto-Chain Auth → Checkout

In `pricing.html`, track pending intent:

```javascript
let pendingCheckoutTier = null;

function pricingCta(tier) {
    const jwt = localStorage.getItem('mb_jwt');
    if (!jwt) {
        pendingCheckoutTier = tier;  // ← Store pending intent
        openAuth();
        return;
    }
    checkout(tier);
}

// In verifyCode(), after successful verification:
if (pendingCheckoutTier) {
    const tier = pendingCheckoutTier;
    pendingCheckoutTier = null;
    checkout(tier);  // ← Auto-chain
} else {
    window.location.href = '/dashboard/';
}
```

### 5.3 Priority Order

```
SPRINT 1 (P0 — Must Have for Ship):
┌─────────────────────────────────────────────────────┐
│ B1: Increase JWT expiry to 30 days                  │
│ F1: Sign In button always visible in navbar         │
│ F2: Chain auth → checkout (remove double-click)     │
│ F3: Show plan tier in avatar dropdown               │
│ B2: Add is_new flag to verify response              │
└─────────────────────────────────────────────────────┘

SPRINT 2 (P1 — Should Have):
┌─────────────────────────────────────────────────────┐
│ B4: Deprecate /dashboard/recover endpoint           │
│ F4: Use is_new flag for onboarding/welcome messages │
│ F5: Handle JWT expiry gracefully                    │
│ B3: Include tier/subscription info in verify resp.  │
└─────────────────────────────────────────────────────┘

SPRINT 3 (P2 — Nice to Have):
┌─────────────────────────────────────────────────────┐
│ F6: Simplify handleStripeWelcome polling            │
│ F7: Remove recovery form from UI                    │
│ B5-B8: last_login_at, edge-case org_id, /auth/me    │
│ F8: Add /auth/me call on page load                  │
└─────────────────────────────────────────────────────┘
```

---

## 6. Revised Frontend Architecture

### 6.1 Shared Auth Module

Currently, the auth logic (auth modal, `updateAuthUI`, `logout`, `decodeJWT`) is duplicated across all 7 HTML files. **Recommendation**: Extract into a shared `auth.js` that is loaded on every page.

```javascript
// static/js/auth.js

// ── Auth State ──
let pendingCheckoutTier = null;
let pendingEmail = '';
let pendingPhone = '';
let pendingMethod = 'email';

// ── Init ──
function initAuth() {
    const jwt = localStorage.getItem('mb_jwt');
    updateAuthUI();
    if (jwt) {
        validateSession(jwt);
    }
}

// ── UI ──
function updateAuthUI() { /* ... */ }
function openAuth(intentTier) { /* store intent, show modal */ }
function closeAuth() { /* hide modal */ }
function logout() { /* clear storage, update UI */ }

// ── Auth0 Passwordless ──
async function continueWithEmail() { /* ... */ }
async function sendPhoneCode() { /* ... */ }
async function verifyCode() { /* ... */ }
async function resendCode() { /* ... */ }

// ── Subscriptions ──
async function checkout(tier) { /* POST /billing/checkout */ }
async function pricingCta(tier) { /* check JWT → auth or checkout */ }

// ── Session ──
async function validateSession(jwt) { /* call /auth/me */ }
```

### 6.2 Page-Specific Intents

Each page sets a `pendingCheckoutTier` before calling `openAuth()`:

```javascript
// On pricing page:
pricingCta('pro');  // Sets pendingCheckoutTier = 'pro' if not authenticated

// On dashboard page, after verify:
if (pendingCheckoutTier) {
    checkout(pendingCheckoutTier);
    pendingCheckoutTier = null;
}
```

---

## 7. Security Considerations

| Concern | Mitigation |
|---------|------------|
| 30-day JWT is too long | Passwordless OTP already provides strong auth on each new device. JWT revocation isn't needed since there's no privileged scope beyond the user's own data. If security is a concern, implement a JWT refresh mechanism with 7-day refresh tokens. |
| Stripe recovery bypass | **Fixed** by deprecating `/dashboard/recover`. All account access goes through email OTP verification. |
| API key in localStorage | Keep current behavior — API key is the actual credential for Memory Bridge API calls. It's already in localStorage, which is the same risk as JWT. |
| CSRF on auth endpoints | All auth endpoints use POST with JSON body and `Content-Type: application/json`. The `Same-Origin` implicit protection applies since custom headers trigger CORS preflight. |
| Rate limiting on OTP | Already handled by Auth0's built-in rate limiting. Add application-level rate limiting as a second layer. |

---

## 8. Success Metrics

| Metric | Current | Target |
|--------|---------|--------|
| Users who can re-access paid account | ~40% (those who didn't clear browser data) | 100% |
| Time to re-authenticate | 5-15 min (confusing, Stripe recovery) | < 30 seconds (just sign in) |
| Support tickets about "lost account" | Unknown (likely high) | Near zero |
| Checkout completion after auth modal | Low (double-click friction) | High (auto-chain) |
| Pages with visible sign-in CTA | 0 (no persistent nav button) | 7/7 (all pages) |

---

## 9. Appendix: File Change Summary

| File | Changes |
|------|---------|
| `config.py` | `jwt_expire_minutes` → 43200 |
| `auth0_controller.py` | Add `is_new`, `tier` to response; add `last_login_at` update |
| `dashboard_controller.py` | Deprecate `/recover` endpoint (410); deprecate `/restore-subscription` |
| `models.py` | Add `last_login_at` to User model |
| `user_service.py` | Optional: conditional JWT expiry based on subscription |
| All `static/*.html` (7 files) | Ensure Sign In button visible; chain auth→checkout; show tier in dropdown |
| `static/js/auth.js` | NEW: shared auth module extracted from HTML files |

---

## 10. Discussion Notes (from analysis session)

- **Henry (AI Expert)**: "The backend is surprisingly solid. The find-or-create pattern for passwordless auth is exactly right. The problem is purely frontend UX and session configuration. I'd recommend we keep the backend changes minimal and focus on the navigation redesign."

- **Fred (Co-founder)**: "Users don't understand passwordless. They think they 'registered' with a password. We need to communicate clearly: 'Enter your email to sign in — no password needed.' The mental model shift is critical."

- **Nova (Visionary)**: "This flow is actually simpler than password-based auth. Stripe subscriptions can't be 'lost' if the identity is email-based. We should lean into this — market it as 'No passwords, no hassle. Your email is your key.'"

- **Rex (Critic)**: "The JWT expiry extension concerns me. 30 days is long. I'd compromise at 7 days with a refresh mechanism. Also, we need to verify that the Stripe webhook correctly links subscription to org_id in ALL scenarios. The edge case of subscriber-without-user-record needs addressing."
