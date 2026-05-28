# Nova: The Auth Flow That Disappears

> **Author**: Nova 🌟 — Visionary / Product Thinker
> **Date**: 2026-05-26
> **Status**: Product Design Vision
> **Motto**: *"The best auth is the one users never think about."*

---

## Guiding Principles

Before any wireframe, any journey map, any code — these are the rules that govern every design decision:

### ✦ Golden Rule: Never leave the user hanging

A spinner that resolves to nothing is a UX sin. Every loading state has:
1. A **maximum duration** — the spinner stops after N seconds
2. A **clear outcome** — success, failure with an action, or a graceful redirect
3. A **fallback display** — show *something* useful even when the ideal data isn't available

### ✦ Email is identity, OTP is auth — no passwords, no confusion

There's no "register" vs "login" — just "sign in." The system finds-or-creates. The user never wonders "did I make a password?" or "which social login did I use?"

### ✦ The navbar is the permanent session anchor

The user must ALWAYS know, at a glance:
- Am I signed in?
- Who am I signed in as?
- How do I sign out?
- What plan am I on?

### ✦ Subscribe after auth, not before

Anonymous checkout creates orphans. The system requires authentication *before* Stripe. The flow is:
Sign in → Subscribe → Pay → Done

Never: Subscribe → Pay → ??? → Lost access

### ✦ Every error has a path forward

If something breaks, the user sees:
1. What went wrong (plain language)
2. What they should do next (one click)
3. How to get help if it persists

---

## 1. User Journey Maps

### 1.1 Sign Up (First-Time Email OTP)

```
SCENE: User visits memorybridge.ai for the first time. They want to try it.

NAVBAR STATE:            [Logo]  Product  Docs  Pricing  [Sign In]
USER ACTION:             Clicks "Sign In" (or "Subscribe" on pricing page)

AUTH MODAL OPENS:
┌─────────────────────────────────────────┐
│  🔑  Sign in to Memory Bridge           │
│                                         │
│  [Continue with Google]                 │
│  [Continue with Apple]                  │
│  [Continue with Phone]                  │
│        ───── or ─────                   │
│  [✉ you@example.com    ] [Continue →]   │
│                                         │
│  (Any path works — pick your favorite)  │
└─────────────────────────────────────────┘

USER: Enters email → Clicks Continue

TRANSITION (200ms):

┌─────────────────────────────────────────┐
│  ✉  Check your email                    │
│                                         │
│  We sent a 6-digit code to              │
│         j***@example.com                │
│                                         │
│  [  _  _  _  _  _  _  ]  [Verify →]    │
│                                         │
│  Didn't get it?  [Send new code]        │
│  (30s countdown before resend)          │
└─────────────────────────────────────────┘

USER: Opens email → Copies 6-digit code → Pastes → Clicks Verify

BEHIND THE SCENES:
  → POST /auth/auth0/passwordless/verify
  → Backend: find user by email
  → NOT FOUND → Create User + org_id
  → Create FREE Subscription
  → Create API Key
  → Return JWT (30-day expiry) + API key + is_new: true

USER JOURNEY CONTINUES:

┌─────────────────────────────────────────┐
│  🎉  Welcome to Memory Bridge!          │
│                                         │
│  Your account is ready.                  │
│                                         │
│  [🚀 Go to Dashboard]                   │
│                                         │
│  (Toast: "Account created! 🎉")         │
└─────────────────────────────────────────┘

NAVBAR NOW SHOWS:  [Logo]  [Docs]  [Pricing]  [👤 J ▼]
                                               ┌────────────┐
                                               │ j***@e...  │
                                               │ 🔷 Free     │
                                               │────────────│
                                               │ Dashboard   │
                                               │ Sign Out    │
                                               └────────────┘

IF USER CAME FROM "SUBSCRIBE" BUTTON:
  → Auto-chain: immediately redirect to Stripe checkout
  → Never make the user click Subscribe twice

WHAT THE USER FEELS:
  "That was easy. I just entered my email and a code. Now I'm in."
```

### 1.2 Returning User (Already Signed In, JWT Valid)

```
SCENE: User comes back the next day. JWT is still valid (30-day expiry).

NAVBAR STATE:  [Logo]  [Docs]  [Pricing]  [👤 J ▼]  (avatar already shown)

USER ACTION:   Types memorybridge.ai/dashboard in browser

PAGE LOADS:

INSTANTANEOUS (cached):
  → Read JWT from localStorage
  → Read API key from localStorage
  → Show avatar dropdown immediately (no flash of "Sign In")
  → Show dashboard skeleton with cached plan tier

BACKGROUND (async, <500ms):
  → GET /dashboard/data → returns tier, keys, usage
  → Hydrate full dashboard with live data
  → If API returns 401 → JWT expired → show re-auth prompt

WHAT USER SEES ON LOAD:
┌─────────────────────────────────────────┐
│  Dashboard          ⚡ Pro Plan         │
│                                         │
│  ┌─ Profile ──────────────────────┐     │
│  │  J  John                     │     │
│  │  john@example.com            │     │
│  │  🆔 Memory Bridge  📅 Joined May 2026│
│  └───────────────────────────────┘     │
│                                         │
│  ┌─ Subscription ─────────────────┐     │
│  │  ⚡ Pro Plan                    │     │
│  │  Memories: 842/1M  ████░░░░░  │     │
│  │  Keys: 3/100                    │     │
│  │  Rate Limit: 300/min           │     │
│  │  Period ends Jul 26, 2026      │     │
│  └───────────────────────────────┘     │
└─────────────────────────────────────────┘

WHAT THE USER FEELS:
  "I'm still logged in. Everything's where I left it."
```

### 1.3 Subscribe to a Paid Plan

```
SCENE: Free-tier user clicks "Upgrade" on dashboard or pricing page.

USER STATE: Already signed in (JWT exists in localStorage)

FLOW:

1. User clicks "Upgrade to Pro"

2. Frontend: POST /billing/checkout?tier=pro
   → Auth header: Bearer <JWT>
   → Backend extracts org_id from JWT
   → Backend creates Stripe Checkout Session
   → Session metadata includes:
       - organization_id
       - user_id
       - email
       - tier: "pro"

3. Frontend: Opens Stripe Checkout page in new tab (not redirect!)
   ↓
   [Better UX: modal overlay with Stripe iframe]
   → User completes payment
   → Stripe shows "Payment successful" screen
   → Stripe fires checkout.session.completed webhook
   → Webhook stores subscription in DB linked to org_id
   → AFTER webhook fires: Stripe redirects to /dashboard?session_id=xxx

4. Dashboard loads:
   → Detects session_id param
   → Starts polling /dashboard/data (every 2s, max 30 attempts = 60s)
   → Shows clear status: "✅ Payment confirmed! Activating your Pro plan..."

   ┌─────────────────────────────────────────┐
   │  ⚡ Pro Plan                             │
   │                                         │
   │  ✅ Payment confirmed!                   │
   │  Activating your plan...                │
   │  (usually takes a few seconds)          │
   │                                         │
   │  ┌─────────────────────────────────┐     │
   │  │ ████████████░░░░░░░░░░░░░░░░░  │     │
   │  └─────────────────────────────────┘     │
   └─────────────────────────────────────────┘

5. When webhook fires (< 5s typical):
   → Poll detects tier !== 'free'
   → Dashboard updates: "🎉 Welcome to Pro!"
   → All features unlocked
   → Avatar dropdown shows "⚡ Pro"

6. FALLBACK (if webhook delayed > 30s):
   → Call POST /dashboard/restore-subscription
   → Searches Stripe for session matching this org_id
   → If found, restores subscription locally
   → Dashboard shows upgraded plan

7. SUPER-FALLBACK (if restore fails > 60s):
   → Show dashboard anyway with Free tier view
   → Show message: "⏳ Your Pro plan is being activated. If this takes more than a few minutes, contact support@memorybridge.ai"
   → Show "Retry" button
   → User can still use the product on Free tier while waiting

WHAT THE USER FEELS:
  "I paid, and it just worked. I didn't lose access or get stuck on a spinner."
```

### 1.4 Return After Subscribing (The Current Bug Scenario — Fixed)

```
SCENE: User paid for Pro plan yesterday. Returns to dashboard today.

CURRENT (BROKEN):
  → Dashboard loads
  → Calls /auth/my-key-value → may 404 or error
  → Error handler nukes the JWT from localStorage
  → User sees "Loading..." forever
  → User is stuck. No way forward. No way to sign in again.

REDESIGNED (FIXED):

STEP 1: Dashboard loads
  → JWT found in localStorage
  → Avatar shows immediately (from JWT decode + cached tier)
  → Dashboard skeleton renders with "⚡ Pro Plan" from cache

STEP 2: Background hydration
  → GET /dashboard/data with JWT Bearer token
  → Backend: finds subscription by org_id → returns tier: "pro"
  → Render full dashboard with Pro features

STEP 3: Resolve API key
  → GET /auth/my-key-value (JWT only — API key for playground use)
  → ONLY clear JWT on explicit 401 (expired/invalid)
  → 404 (no key) → show "Generate Key" prompt, don't destroy session
  → 5xx (transient) → retry once, then show friendly warning, keep JWT

NAVBAR AVATAR DROPDOWN:
  → Shows "john@example.com"
  → Shows "⚡ Pro Plan"
  → Shows "Signed in since May 2026"
  → "Sign Out" at bottom

USER FEELING:
  "I just opened the dashboard and everything worked. I didn't even think about auth."
```

### 1.5 Sign Out and Sign Back In

```
SCENE: User clicks "Sign Out" from avatar dropdown.

1. Confirmation modal:
   ┌─────────────────────────────────────┐
   │  🚪  Sign out of Memory Bridge?     │
   │                                     │
   │  You'll need to sign in again to    │
   │  manage your account. Your API keys │
   │  will continue to work.             │
   │                                     │
   │       [Cancel]  [Sign Out →]        │
   └─────────────────────────────────────┘

2. User confirms → System:
   → Clears localStorage: mb_jwt, mb_api_key, mb_user_*, mb_tier
   → Sets currentApiKey = ''
   → Updates navbar: [Sign In] appears
   → Clears dashboard content
   → Shows: "👋 Signed out. Sign in again to continue."
   → Shows auth gate card with "Sign In →" button

3. Sign Back In:
   → User clicks "Sign In" (always visible in navbar)
   → Enters email → gets OTP → verifies
   → Backend: get_user_by_email → FOUND → returns JWT
   → Frontend: stores JWT + fetches API key
   → Dashboard reloads with full data
   → TODO: Full re-authentication, not just a token swap

USER FEELING:
  "Signing out was clear. Signing back in was fast. My account is still there."
```

### 1.6 Expired JWT / Lost Session

```
SCENE: User opens dashboard after 31 days without visiting.

PAGE LOADS:
  → JWT found in localStorage
  → Avatar shows (from cached JWT decode)
  → GET /dashboard/data returns 401

BEHAVIOR:
  → Dashboard shows auth gate card (not infinite spinner)
  → Navbar changes: shows [Sign In] button
  → Toast: "🔑 Your session has expired. Please sign in again."

  ┌───────────────────────────────────────────────┐
  │  🔑  Session Expired                          │
  │                                               │
  │  Your session has expired. This is normal —   │
  │  we keep you signed in for 30 days for         │
  │  security reasons.                            │
  │                                               │
  │  Just enter your email to get a new code:     │
  │  [✉ you@example.com] [Continue →]             │
  │                                               │
  │  No password needed — we'll email you a code. │
  └───────────────────────────────────────────────┘

  → User enters email → gets OTP → verifies
  → System returns JWT (same org_id, same subscription)
  → Dashboard loads with full data
  → Toast: "Welcome back! 👋"

USER FEELING:
  "Oh, my session expired. No big deal — I just entered my email again."
```

### 1.7 New Device / Cross-Device

```
SCENE: User subscribed on their laptop. Now they open Memory Bridge on their phone.

1. Phone loads memorybridge.ai
   → No localStorage → No JWT
   → Navbar: [Sign In]
   → If landing on dashboard → auth gate card appears

2. User clicks "Sign In" → enters email → gets OTP → verifies
   → Backend: get_user_by_email → FOUND → returns JWT
   → JWT contains same org_id as laptop
   → Subscription found → Pro tier displayed
   → API key fetched (same key as laptop, or new one if needed)
   → Full dashboard rendered

3. KEY INSIGHT: Everything is linked to the org_id in the JWT, not the device.
   The user's subscription, API keys, and memories all follow the org_id.
   Cross-device works because the email IS the identity.

USER FEELING:
  "I just signed in on my phone with my email, and everything was there."
```

---

## 2. Wireframe Descriptions for Auth State Indicators

### 2.1 Signed-Out State

```
NAVBAR (right side):

[🌐 Language ▼]  [☀ Theme]  [👤 Sign In]  [▶ Launch Playground]

The "Sign In" button is ALWAYS visible when the user is signed out.
It's styled as a subtle outline button — inviting, not intimidating.
The person icon communicates "this is about you."
```

### 2.2 Signed-In State (Collapsed)

```
NAVBAR (right side):

[🌐 Language ▼]  [☀ Theme]  [👤 J ▼]  [▶ Launch Playground]

The avatar button replaces the Sign In button:
- 28px circle with user's first initial (gradient background)
- Truncated name/email next to it (max 120px)
- Tiny chevron indicating "click for menu"
- Green dot (6px) in bottom-right corner of avatar → session is valid
```

### 2.3 Session States (Avatar Dot Colors)

| State | Dot | Meaning |
|-------|-----|---------|
| Valid session | 🟢 Green | JWT valid, all good |
| Expiring soon | 🟡 Amber | JWT expires in < 5 min |
| Expired | 🔴 Red | JWT expired, API 401'd |
| No session | None | Not signed in |

### 2.4 Signed-In Dropdown (Expanded)

```
┌──────────────────────────────┐
│  john@example.com          │
│  ⚡ Pro Plan                │
│  ─────────────────────── │
│  📊  Dashboard            │
│  🔑  API Keys             │
│  💳  Billing & Plan       │
│  🔗  Integration Guide     │
│  ─────────────────────── │
│  ⏰  Signed in since May 26│
│  🚪  Sign Out              │
└──────────────────────────────┘

Key details:
- Email at top (truncated if long)
- Plan badge with colored dot (🔷 free, 💎 starter, ⚡ pro, 🏢 enterprise)
- Common actions as quick links
- "Signed in since" timestamp for session awareness
- "Sign Out" at bottom with red hover state
```

### 2.5 Auth Gate Card (When Not Signed In but on Dashboard)

```
┌───────────────────────────────────────────┐
│  🔑                                      │
│  Sign in to manage your account           │
│                                           │
│  Sign in with your email, Google, or      │
│  Apple to manage API keys, view your      │
│  subscription, and configure Memory       │
│  Bridge.                                  │
│                                           │
│           [Sign In →]                     │
│                                           │
│  New here? Create an account — no         │
│  password needed.                         │
└───────────────────────────────────────────┘
```

---

## 3. Error Recovery Flows

### 3.1 The "No API Key for JWT User" Case

**Principle**: A JWT is proof of identity. An API key is a credential for the Memory Bridge API. They are separate concepts that happen to both be in localStorage.

**Design decision**: Every user gets an API key automatically on signup. The backend already does this (lines 361-363 of `auth0_controller.py`). The gap is on the frontend:

**Recovery flow**:
```
User has JWT but /auth/my-key-value returns 404:
  → Dashboard renders fully (subscription data works via JWT)
  → API Keys section shows: "No API keys yet. Generate one."
  → "Generate New Key" button is enabled (uses JWT auth)
  → User can create a key at any time
  → Key is shown once, then hidden
  → NEVER block the dashboard because a key is missing
```

**The API key is not auth. The JWT is auth. The API key is a credential.**

### 3.2 Subscription Webhook Delay

**Design**: Stripe webhooks usually fire in <5s. But sometimes they take 30-60s.

**Polling strategy**:
```
session_id detected → start 2s polling (max 30 attempts = 60s)

0-30s (attempts 1-15): Poll /dashboard/data
  → Shows "✅ Payment confirmed! Activating your plan..."
  → Progress bar animating
  → If tier changes → success! Stop polling.

30-60s (attempts 16-30): Call /dashboard/restore-subscription
  → If restored → success! Show "🎉 Subscription restored!"
  → If not → continue polling

60s+ (after 30 attempts): Stop polling. Show fallback.
  → Show dashboard with current tier (may be 'free')
  → Show banner: "⏳ Your plan is being activated. This usually takes a moment."
  → Provide a "Refresh Status" button
  → Do NOT show an infinite spinner
```

**The key insight**: After 60s, stop waiting and show *something*. The user is still logged in, they can still use the product. The upgrade will arrive eventually.

### 3.3 Dashboard Can't Load Data

**Graceful degradation tiers**:

| Scenario | What User Sees | Recovery Action |
|----------|---------------|-----------------|
| Network down | "📡 No internet connection. Your dashboard will load once you're back online." | Auto-retry when network returns |
| API 500 | " We're having trouble loading your dashboard. It's not you — it's us." | "Retry" button + "Contact support" link |
| API 401 (expired JWT) | Auth gate card: "🔑 Your session has expired. Sign in again." | Auto-shows auth modal |
| Webhook not fired yet | "✅ Payment confirmed! Activating your plan... (usually takes a few seconds)" | Progress bar + auto-poll |
| Rate limited | "⏳ You're refreshing too fast. Give it a moment." | Cooldown countdown |

**Golden rule applied to each**: Show the action. Never leave the user looking at a spinner with no way forward.

### 3.4 Anonymous User Clicks "Subscribe" (The Orphan Prevention)

**Current bug**: User can click "Subscribe" without signing in → Stripe checkout opens → user pays → no User record created → subscription stored in Stripe but not linked to any org_id → user loses access.

**Redesigned flow**:
```
User clicks "Subscribe" (not signed in):
  ↓
1. Open auth modal (don't open Stripe)
2. User signs in (OTP or social)
3. On success → auto-chain to Stripe checkout
4. Stripe metadata includes org_id + user_id + email
5. User pays → webhook fires → subscription linked to org_id
6. Dashboard loads with upgraded plan
```

**The user only clicks "Subscribe" once. The system handles the rest.**

### 3.5 Stripe Customer Without User Record (Orphan Detection)

**Edge case**: Legacy users who somehow have a Stripe subscription but no User record.

**Flow on sign-in**:
```
User enters email → gets OTP → verifies
  → Backend: get_user_by_email → NOT FOUND
  → Before creating new User with new org_id:
    1. Look up Stripe Customer by email
    2. If customer found with subscriptions:
       a. Extract stripe_customer_id
       b. Find existing subscription's org_id
       c. Create User with that org_id instead of a new one
    3. Create free subscription + API key (if none exist)
  → Return JWT with correct org_id
  → Dashboard loads with existing subscription
```

---

## 4. Specific Design Decisions

### 4.1 JWT Expiry: 30 Days

**Why 30?** 
- Passwordless OTP is the 2FA — the code in the email proves identity
- A 30-day JWT means users rarely need to re-authenticate
- For a developer tool (Memory Bridge), long sessions are expected
- If security concerns arise, implement refresh tokens (7-day refresh + 1-hour access tokens)

### 4.2 No Separate Registration vs Login

The auth modal has ONE entry point. The user enters their email. The backend finds-or-creates. The response includes `is_new: true/false` so the frontend can show appropriate messaging.

**This eliminates the "did I sign up with Google or email?" confusion entirely.**

### 4.3 API Key is a Credential, Not Auth

The JWT is for the dashboard UI. The API key is for the Memory Bridge API (programmatic access).

- Dashboard pages use JWT for API calls
- The API key is fetched once and cached for the playground
- If no API key exists, the dashboard still works — just shows "Generate Key" prompt

### 4.4 The "Refresh" Button vs Automatic Fallback

**Decision**: Both exist, but the automatic fallback tries first.

- Automatic: Poll for 60s, then call restore-subscription
- Manual: "Refresh Status" button always available
- The manual button is for edge cases where the user waited >60s and wants to retry

**The refresh button calls `/dashboard/restore-subscription` and shows a clear result:**
- "✅ Subscription found! Refreshing data..."
- "❌ No subscription found. If you just paid, it may take another moment."

### 4.5 Sign-Out Confirmation

Always show confirmation before signing out. This prevents accidental sign-outs and gives the user a moment to remember they might need their API key.

The confirmation modal shows:
- What will happen (session cleared, API keys continue working)
- Option to cancel
- Clear "Sign Out" action

### 4.6 Session Persistence Across Tabs

Use `window.addEventListener('storage')` to sync auth state across browser tabs:
- When user signs in on Tab A, Tab B detects the localStorage change
- Tab B updates UI automatically
- When user signs out on Tab A, Tab B detects and signs out too

This prevents the confusing state of being "signed in" on one tab but "signed out" on another.

---

## 5. Implementation Roadmap (Nova's Priority)

The engineering team has a detailed execution plan. This is the **product priority** — what matters most to users:

```
P0 — MUST SHIP: The Loading Bug Fix
  └─ Users who paid can see their dashboard again
  └─ Fix my-key-value error handling (don't nuke JWT on transient errors)
  └─ Fix handleStripeWelcome auth initialization
  └─ No users get stuck on infinite spinner

P1 — MUST SHIP: Auth Identity in Navbar
  └─ "Sign In" always visible when logged out
  └─ Avatar with name, email, and plan when logged in
  └─ "Sign Out" always accessible from dropdown
  └─ 30-day JWT expiry

P2 — SHOULD SHIP: Subscribe Flow Polish
  └─ Auto-chain auth → checkout (remove double-click)
  └─ Graceful webhook delay handling (60s poll + fallback)
  └─ Orphan prevention (no anonymous checkout)

P3 — NICE TO HAVE: Error Recovery
  └─ Session timeout warning
  └─ Graceful degradation tiers for dashboard
  └─ Cross-device session sync
  └─ Stripe orphan detection on sign-in
```

---

## 6. Success Criteria

The auth flow is successful when:

1. **Zero users get stuck on "Loading..."** — every loading state resolves or provides a clear action
2. **Users always know they're signed in** — the navbar is the permanent signal
3. **Users can always sign out** — "Sign Out" is never hidden
4. **Paying users never lose access** — subscription is linked to identity, not session
5. **Cross-device works effortlessly** — sign in with email on any device
6. **Anonymous checkout is impossible** — must sign in before Stripe
7. **No spinner lives forever** — every async operation has a timeout and a fallback

---

## Appendix: The Mental Model

```
Think of it this way:

┌─────────────────────────────────────────────────┐
│                                                 │
│   Email = Your Identity (who you are)           │
│   JWT   = Your Session (proof you just authed)  │
│   API Key = Your Credential (for programmatic   │
│             access to the API)                  │
│   Subscription = Your Entitlements (what tier   │
│                   you've paid for)              │
│                                                 │
│   They are four separate things that work       │
│   together. Losing one should never lose the    │
│   others.                                       │
│                                                 │
└─────────────────────────────────────────────────┘

The JWT lives in localStorage. If it expires, you re-auth with email.
The API key lives in localStorage. If you lose it, you generate a new one.
The Subscription lives in the database, linked to your org_id.
Your identity is your email — you can always prove it with an OTP.
```

---

*"The best interface is no interface. The best auth is the one users never think about."* — Nova
