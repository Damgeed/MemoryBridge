# Auth UX Vision — Memory Bridge Dashboard

> Design proposal by Nova (Visionary)
> Date: 2026-05-26

## Executive Summary

Memory Bridge's current auth UX is functional but **identity-invisible**. A user can sign in and never see their own name, email, or a clear indicator that they're authenticated. The navbar, pricing page, and auth modal all behave identically regardless of auth state. This proposal designs a **session lifecycle** that feels present, intentional, and human — from first visit through sign-up, subscription, and ongoing management.

---

## 1. The Session Lifecycle — A Feeling Map

```
[BEFORE]                    [DURING]                    [AFTER]
  │                           │                           │
  ├─ Anonymous visitor        ├─ Just signed in           ├─ Signed out
  │   "Who are you?"          │   "Welcome back"          │   "See you later"
  │                           │                           │
  ├─ Cold landing page        ├─ Warm dashboard            ├─ Clean break
  │   "Try this"              │   "Here's your stuff"     │   "No data lost"
  │                           │                           │
  └─ Auth as a doorway        └─ Auth as a presence        └─ Auth as a memory
     (one-time friction)         (zero friction day-to-day)   (reversible)
```

**Design principle:** Authentication should feel like walking into your own space — not like showing a ticket every time. The system should acknowledge you, remember you, and get out of your way.

---

## 2. Nav Avatar Dropdown — The Identity Hub

### Current state
A single `.auth-btn` toggle between "Sign in" and "Sign out" — no identity shown.

### Proposed design

```
┌──────────────────────────────────────────────┐
│  [Logo]  Home  Playground  Graph  Dashboard  │  🌐  🌙  [👤 Alex]  [Launch ▸]
│                            Pricing           │           ▼
└──────────────────────────────────────────────┘        │
                                                         ├─ Signed in as
                                                         │   alex@example.com
                                                         ├─ Pro plan  ⭐
                                                         ├───
                                                         ├─ 🔑 API Keys
                                                         ├─ 💳 Billing & Plan
                                                         ├─ ⚙️ Account Settings
                                                         ├───
                                                         └─ 🚪 Sign out
```

### Implementation recommendations

**When signed out:**
```html
<button class="auth-btn" onclick="openAuth()">
  <svg>...</svg>
  <span>Sign in</span>
</button>
```

**When signed in (replace the button with avatar dropdown):**
```html
<div class="avatar-dropdown">
  <button class="avatar-btn" onclick="toggleUserMenu()">
    <div class="avatar-circle">
      <!-- First letter of name/email, or Gravatar -->
      <span>A</span>
    </div>
    <span class="avatar-name">Alex</span>
    <svg class="chevron-down">...</svg>
  </button>
  <div class="user-dropdown" id="user-dropdown">
    <div class="dropdown-header">
      <div class="dropdown-email">alex@example.com</div>
      <div class="dropdown-plan">
        <span class="tier-dot pro"></span> Pro
      </div>
    </div>
    <div class="dropdown-divider"></div>
    <a href="/dashboard/" class="dropdown-item">
      <svg>...</svg> Dashboard
    </a>
    <a href="/dashboard/#api-keys" class="dropdown-item">
      <svg>...</svg> API Keys
    </a>
    <a href="/dashboard/#billing" class="dropdown-item">
      <svg>...</svg> Billing & Plan
    </a>
    <a href="/dashboard/#settings" class="dropdown-item">
      <svg>...</svg> Account Settings
    </a>
    <div class="dropdown-divider"></div>
    <button class="dropdown-item text-red" onclick="confirmLogout()">
      <svg>...</svg> Sign Out
    </button>
  </div>
</div>
```

**New CSS needed:**
```css
.avatar-btn {
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.25rem 0.5rem 0.25rem 0.25rem;
  border-radius: 999px;  /* pill shape */
  border: 1px solid var(--border-primary);
  background: var(--bg-glass);
  cursor: pointer;
  transition: all var(--transition-fast);
}
.avatar-btn:hover {
  border-color: var(--accent);
  background: var(--accent-glow);
}
.avatar-circle {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--accent-gradient);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 0.8rem;
  font-weight: 700;
  flex-shrink: 0;
}
.avatar-name {
  font-size: 0.82rem;
  font-weight: 600;
  color: var(--text-primary);
}
.avatar-btn .chevron-down {
  width: 14px;
  height: 14px;
  color: var(--text-muted);
  transition: transform 0.2s;
}
.avatar-btn.open .chevron-down {
  transform: rotate(180deg);
}
.user-dropdown {
  position: absolute;
  top: 100%;
  right: 0;
  margin-top: 6px;
  min-width: 220px;
  background: var(--bg-glass-strong);
  backdrop-filter: blur(20px) saturate(1.5);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  display: none;
  z-index: 200;
  overflow: hidden;
}
.user-dropdown.open { display: block; }
.dropdown-header {
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--border-subtle);
}
.dropdown-email {
  font-size: 0.82rem;
  color: var(--text-muted);
  margin-bottom: 0.2rem;
}
.dropdown-plan {
  font-size: 0.78rem;
  font-weight: 600;
  display: flex;
  align-items: center;
  gap: 0.35rem;
}
.tier-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
}
.tier-dot.free { background: var(--text-faint); }
.tier-dot.starter { background: var(--accent); }
.tier-dot.pro { background: var(--green); }
.tier-dot.enterprise { background: var(--blue); }
.dropdown-item {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  padding: 0.6rem 1rem;
  font-size: 0.85rem;
  color: var(--text-secondary);
  cursor: pointer;
  background: none;
  border: none;
  width: 100%;
  text-align: left;
  font-family: var(--font-sans);
  transition: all var(--transition-fast);
}
.dropdown-item:hover {
  background: var(--btn-secondary-hover);
  color: var(--text-primary);
  text-decoration: none;
}
.dropdown-item svg { width: 16px; height: 16px; flex-shrink: 0; }
.text-red { color: var(--red) !important; }
.text-red:hover { background: rgba(239, 68, 68, 0.1) !important; }
.dropdown-divider { height: 1px; background: var(--border-subtle); }
```

---

## 3. Session Indicator — "You Are Here"

### Current state
A single line of text in `#dash-status`: `"Org: ... · Free plan"`. No name, no email.

### Proposed design

**Dashboard header area after sign-in:**
```
┌─────────────────────────────────────────────────────┐
│  [*] Dashboard                          Alex Chen    │
│  ────────────────                       ─────────    │
│  Welcome back, Alex ✨                  Pro plan      │
│                                         Active since  │
│                                         March 2026    │
│                                                      │
│  Current period: Apr 26 – May 26, 2026               │
│  1,234 / 1,000,000 memories used                     │
└─────────────────────────────────────────────────────┘
```

**Navbar session indicator (subtle, persistent):**
A small green dot next to the avatar when authenticated + a subtle session chip that appears on hover:

```html
<!-- Session chip in nav, shown when logged in -->
<div class="session-chip" title="Session active">
  <span class="session-dot"></span>
  <span class="session-expiry">Expires in 2h 14m</span>
</div>
```

**New JS state model:**
```javascript
const SESSION = {
  jwt: localStorage.getItem('mb_jwt'),
  apiKey: localStorage.getItem('mb_api_key'),
  user: null,  // { name, email, avatar_url }
  org: null,   // { id, tier, status, period_end }
};

async function initSession() {
  if (!SESSION.jwt && !SESSION.apiKey) return SESSION_STATE.ANONYMOUS;
  
  // Hydrate user info from the dashboard/data endpoint
  try {
    const data = await api('GET', '/dashboard/data');
    SESSION.user = { name: data.user_name || data.email?.split('@')[0] || 'User', email: data.email };
    SESSION.org = data;
    return SESSION_STATE.AUTHENTICATED;
  } catch {
    // JWT expired
    clearSession();
    return SESSION_STATE.EXPIRED;
  }
}
```

---

## 4. "Before" — The Anonymous Visitor Experience

### What the user sees

**Landing page (index.html):**
```
 ┌──────────────────────────────────────────────┐
 │  [Logo]  Home  Playground  Graph  Pricing    │  🌐  🌙  [Sign in]  [Launch ▸]
 └──────────────────────────────────────────────┘

 Memory Bridge
 Cross-Session Memory for AI Agent Teams
 ┌─────────────────┐  ┌─────────────────┐
 │ Get Started Free│  │ See Pricing  →  │
 └─────────────────┘  └─────────────────┘
```

**The "Sign in" button shows a person icon + text. Clear, unambiguous.**
**The "Launch" CTA always goes to the playground (with a trial/demo key).**

### What should NOT happen

- ❌ Auto-opening the auth modal on page load (current dashboard does this — jarring)
- ❌ Hiding the pricing CTA from anonymous visitors (they need to see what they'll get)
- ❌ Showing a generic "Get Started Free" that doesn't explain what happens next

### What should happen

- **Clear value proposition** before asking for auth
- **Functional demo** without sign-in (the playground already supports this — keep it)
- **Soft auth prompts** — a small "Sign in to save your data" banner, not a modal
- **Pricing page shows prices** but the CTA on Free tier says "Try Free →" (redirects to sign-up)

---

## 5. "During" — The Sign-In Experience

### Step-by-step ideal flow

#### 5a. First visit (anonymous → signed in)

```
1. Visitor clicks "Get Started Free" or "Sign in"
   → Auth modal opens (duplicated across pages — extract to shared HTML)

2. User enters email
   → "Continue with Email" button
   → 6-digit code sent to inbox
   
3. User enters code
   → JWT + API key returned
   → localStorage updated
   
4. TRANSITION: Smooth close of modal
   → Navbar morphs: "Sign in" fades out → avatar fades in
   → Dashboard header reads: "Welcome, Alex! 🎉"
   → Quick tour tooltip: "🔑 Your first API key has been created"
   
5. NEW USER FLOW:
   → Auto-open the "Key Reveal" card (they need to copy it)
   → Show "Your first API key" with prominent copy/warning
   → Subtle onboarding: "Step 1: Copy your key. Step 2: Run the command below."
```

#### 5b. Returning user (signed in → sees content)

```
1. Page loads → check localStorage for jwt + apiKey
   
2. If both present:
   → Immediately show dashboard content (no modal, no flicker)
   → Silently validate JWT in background
   → If JWT valid → "Welcome back, Alex"
   → If JWT expired but apiKey exists → silently refresh
   → If all expired → show "Session expired" toast → soft re-auth prompt
```

#### 5c. Sign-in via social

```
Same as email flow, but:
- Social login redirects to Auth0 → callback URL includes ?jwt=...
- On return: nav morphs immediately, no code step
- User sees: "Signed in with Google" toast
```

**Critical UX detail:** After social login callback, the page should NOT show a loading spinner then modal. It should go straight to content with a transition.

---

## 6. "After" — Signed Out Experience

### Current state
```javascript
function logout() {
  localStorage.removeItem('mb_jwt');
  localStorage.removeItem('mb_api_key');
  // ... silently clears
}
```

### Proposed design — A proper goodbye

#### 6a. Logout confirmation modal

```
┌──────────────────────────────────┐
│          🚪 Sign Out             │
│                                  │
│  Are you sure you want to        │
│  sign out of Memory Bridge?      │
│                                  │
│  Your API keys will continue     │
│  to work until revoked.          │
│                                  │
│          [Cancel]  [Sign Out →]  │
└──────────────────────────────────┘
```

#### 6b. Post-logout state

```
- Navbar morphs back: avatar → "Sign in" button
- Dashboard shows: "👋 You've been signed out. Your data is safe."
- Clean UI — no dangling state, no partial data
- Confirmation toast: "Signed out. See you later, Alex."
- localStorage cleared completely
```

#### 6c. Session timeout warning

```
JWT expires → background check every 5 minutes
When 5 minutes from expiry:
  → Subtle in-nav warning: yellow dot on avatar
  → Tooltip: "Session expires in 4 min"
  → Click avatar → dropdown shows "Session expiring soon" warning
  
When expired:
  → Toast: "Your session has expired. Please sign in again."
  → Auth modal opens on next action (not immediately)
  → Dashboard data stays cached — no jarring data loss
```

**Implementation:**
```javascript
// Background session monitor
let sessionTimer = null;

function startSessionMonitor() {
  const jwt = localStorage.getItem('mb_jwt');
  if (!jwt) return;
  
  try {
    const payload = JSON.parse(atob(jwt.split('.')[1]));
    const exp = payload.exp * 1000; // milliseconds
    const now = Date.now();
    const remaining = exp - now;
    
    if (remaining <= 0) {
      // Already expired
      handleSessionExpired();
      return;
    }
    
    // Warn at 5 minutes
    if (remaining < 5 * 60 * 1000) {
      showSessionWarning(Math.round(remaining / 60000));
    }
    
    // Set timer for warning
    const warnAt = Math.max(0, remaining - 5 * 60 * 1000);
    setTimeout(() => showSessionWarning(5), warnAt);
    
    // Set timer for expiry
    setTimeout(() => handleSessionExpired(), remaining);
  } catch {
    // Invalid JWT — silently ignore
  }
}
```

---

## 7. The Sign-Up → Subscribe → Manage Flow

### The complete user journey

```
┌─────────┐    ┌──────────┐    ┌───────────┐    ┌──────────┐
│ LANDING │───>│ SIGN UP  │───>│ DASHBOARD │───>│ SUBSCRIBE│
│         │    │ (free)   │    │ (free)    │    │          │
└─────────┘    └──────────┘    └──────┬────┘    └────┬─────┘
                                      │              │
                                      │              ▼
                                      │       ┌──────────┐
                                      │       │ STRIPE   │
                                      │       │ CHECKOUT │
                                      │       └────┬─────┘
                                      │              │
                                      │              ▼
                                      │       ┌──────────┐
                                      ├──────>│ WELCOME  │
                                      │       │ (Pro)    │
                                      │       └──────────┘
                                      │
                                      ▼
                               ┌──────────┐
                               │ MANAGE   │
                               │(upgrade/ │
                               │downgrade)│
                               └──────────┘
```

### 7a. Landing → Sign Up

When user clicks **"Get Started Free"** on pricing or **"Sign In"** in navbar:

```
1. Auth modal opens (shared component)
2. User signs in via email/social/phone
3. On success:
   → Smooth transition (modal closes, nav updates)
   → If first time → auto-redirect to dashboard with welcome state
   → If returning → stay on current page, nav updates
```

**Key UX rule:** Never force a page reload after sign-in. Use SPA-style state updates.

### 7b. Free Dashboard → Subscribe

```
1. User sees subscription card showing "Free plan"
2. Click "Upgrade" on any paid tier:
   → If NOT signed in → auth modal opens first
   → If signed in → direct to Stripe checkout
   
3. During Stripe redirect:
   → "Redirecting to secure checkout..." overlay (not alert())
   → Save pending tier + org_id in localStorage
   
4. Stripe callback → /dashboard/?session_id=cs_xxx
   → Welcome screen: "🎉 Payment confirmed! Setting up your account..."
   → Poll for webhook (already implemented — good!)
   → On success: "🎉 You're now on the Pro plan!"
   → Subscription card updates immediately
```

### 7c. Subscription Management

**From dashboard subscription card:**
```
┌─────────────────────────────────────────────────┐
│  💳 Subscription                                 │
│                                                  │
│  [Pro ⭐]  Active  ·  5 of 100 keys used         │
│                                                  │
│  Current period: Apr 26 – May 26, 2026           │
│  Next billing: May 26, 2026                      │
│                                                  │
│  [Manage on Stripe ▸]  [View Usage]  [Cancel]    │
│                                                  │
│  ─── Usage This Month ───                        │
│  Memories:  1,234 / 1,000,000  ████░░░░  0.12%  │
│  Sessions:    156 / 10,000      ░░░░░░░░  1.56%  │
│  Rate:       ~42 req/min                        │
└─────────────────────────────────────────────────┘
```

**From avatar dropdown:**
```
👤 Alex Chen
alex@example.com
Pro plan  ⭐

  Dashboard
  API Keys
  Billing & Plan   ← links to subscription card
  Account Settings ← NEW section
  ───────────
  Sign Out
```

**Cancel flow:**
```
1. Click "Cancel" → confirm modal opens:
   ┌─────────────────────────────────┐
   │   😢 Cancel Subscription        │
   │                                  │
   │  Your Pro plan will remain       │
   │  active until May 26, 2026.      │
   │  After that, you'll downgrade    │
   │  to Free (1,000 memories max).   │
   │                                  │
   │  You can re-subscribe anytime.   │
   │                                  │
   │  [Keep Pro]  [Continue Cancel]   │
   └─────────────────────────────────┘
   
2. After cancellation:
   → Subscription card shows "Cancels on May 26"
   → "Resubscribe" button appears
   → No data loss — graceful transition
```

---

## 8. Pricing Page Auth Awareness

### Current state
Pricing page doesn't know if you're logged in — same CTAs for everyone.

### Proposed design

**When anonymous:**
```
Free card:    [Get Started Free →]  → opens auth modal + redirects to dashboard
Starter card: [Subscribe]           → opens auth modal first
Pro card:     [Subscribe]           → opens auth modal first
```

**When signed in (on Free plan):**
```
Free card:    [Your Current Plan ✓]  → disabled, green checkmark
Starter card: [Upgrade →]            → direct to Stripe checkout
Pro card:     [Upgrade →]            → direct to Stripe checkout
```

**When signed in (on Pro plan):**
```
Free card:    [Downgrade]            → confirm modal
Starter card: [Downgrade]            → confirm modal
Pro card:     [Your Current Plan ✓]  → disabled, green checkmark
```

**Implementation:**
```javascript
// In pricing.html, check auth state on load
document.addEventListener('DOMContentLoaded', () => {
  const jwt = localStorage.getItem('mb_jwt');
  const key = localStorage.getItem('mb_api_key');
  
  if (jwt || key) {
    // Fetch current tier
    fetch('/dashboard/data', {
      headers: { 'Authorization': 'Bearer ' + (jwt || key) }
    })
    .then(r => r.json())
    .then(data => {
      renderPricingWithPlan(data.tier);
    })
    .catch(() => {
      // Not authenticated on backend side
      renderPricingAnonymous();
    });
  } else {
    renderPricingAnonymous();
  }
});
```

---

## 9. Mobile Parity

### Current state
Mobile nav hides `.auth-btn` (display: none at 820px breakpoint). Mobile panel has a "Sign in" link at the bottom.

### Proposed design

**Mobile panel when signed in (820px and below):**
```
┌──────────────────────┐
│ [Logo]           [✕] │
│                      │
│ 🏠 Home              │
│ ▶ Playground         │
│ 🛜 Graph             │
│ 💰 Pricing           │
│ ◫ Dashboard          │
│                      │
│ ─── Account ───      │
│ 👤 Alex Chen         │  ← shows avatar + name
│    alex@example.com  │
│    Pro plan          │
│                      │
│ 🔑 API Keys          │
│ 💳 Billing & Plan    │
│ ⚙️ Account Settings  │
│ 🚪 Sign Out          │  ← opens confirm modal
│                      │
│ ───────────────────  │
│ [▶ Launch Playground]│
└──────────────────────┘
```

**Mobile panel when signed out:**
```
┌──────────────────────┐
│ [Logo]           [✕] │
│                      │
│ 🏠 Home              │
│ ▶ Playground         │
│ 🛜 Graph             │
│ 💰 Pricing           │
│ ◫ Dashboard          │
│                      │
│ ───────────────────  │
│ 🔑 Sign In / Sign Up │  ← accent-colored, opens auth modal
│                      │
│ ───────────────────  │
│ [▶ Launch Playground]│
└──────────────────────┘
```

---

## 10. Account Settings Page (NEW Section)

### What it should include

```
┌─────────────────────────────────────────────┐
│  ⚙️ Account Settings                        │
│                                             │
│  ── Profile ──                              │
│  Name:          [Alex Chen        ]         │
│  Email:         alex@example.com (verified) │
│  Avatar:        [Choose Image] [Remove]     │
│                                             │
│  ── Preferences ──                          │
│  Theme:         ○ System  ● Dark  ○ Light   │
│  Language:      [English          ▼]        │
│  Timezone:      [UTC+8 Asia/Shanghai  ▼]    │
│                                             │
│  ── Session ──                              │
│  Signed in with: Google (alex@gmail.com)    │
│  Member since:  March 15, 2026              │
│  [Sign Out All Devices]                     │
│                                             │
│  ── Danger Zone ──                          │
│  [Delete Account] → confirm → "We'll miss   │
│   you" screen with data export option       │
└─────────────────────────────────────────────┘
```

---

## 11. Shared Auth Component (Technical Recommendation)

### Current issue
The auth modal HTML and JS are duplicated verbatim across `index.html`, `pricing.html`, `dashboard.html`, and `playground.html`. This means 4 copies of the same hundreds-of-lines phone country selector.

### Recommendation
Extract auth modal into a shared static file loaded by all pages:

```html
<!-- In each page, replace the duplicated auth modal with: -->
<div id="auth-mount"></div>
<script>
  fetch('/playground/auth-modal.html')
    .then(r => r.text())
    .then(html => {
      document.getElementById('auth-mount').innerHTML = html;
      // Re-bind event handlers
      initAuthModal();
    });
</script>
```

Or even better — use a `<template>` approach with a shared JS module.

---

## 12. Constants & CSS Variables

Add to `style.css`:

```css
/* ── Auth State Colors ─────────────────────── */
--auth-dot-active: #34d399;
--auth-dot-warning: #f59e0b;
--auth-dot-expired: #ef4444;
--auth-dot-inactive: #505068;

/* ── Avatar ────────────────────────────────── */
--avatar-size: 28px;
--avatar-font: 0.8rem;
```

---

## 13. Summary of Changes Required

| Component | Change | Effort |
|-----------|--------|--------|
| `style.css` | Add avatar dropdown, session chip, user menu, mobile account section CSS | ~120 lines |
| `dashboard.html` | Add `updateSessionUI()`, `initUserSession()`, session monitor, logout confirm, account settings section | ~250 lines JS + HTML |
| `pricing.html` | Add auth-aware CTA rendering, `renderPricingWithPlan()` | ~80 lines JS |
| `index.html` | Auth-aware nav with avatar dropdown | ~50 lines JS |
| `i18n.js` / translations | Add strings for dropdown items, session warnings, settings | ~30 keys |
| All pages | Extract auth modal to shared partial, replace 4x duplicate | ~20 lines each page |
| New: `account-settings` | New dashboard section or page | Medium |
| Backend: `/dashboard/data` | Add `user_name`, `email`, `avatar_url`, `member_since` to response | ~10 lines |
| Backend: `/auth/me` | New endpoint to return current user profile from JWT | ~15 lines |

---

## 14. Design Principles Recap

1. **Identity-first** — The user should see their own name/email in the UI immediately after sign-in
2. **Zero friction for returning users** — No modals, no loading spinners if valid credentials exist
3. **Graceful transitions** — Nav morph, toast, welcome text — never a hard reload
4. **Error resilience** — Expired JWT → soft prompt, not hard lockout
5. **Mobile parity** — Full account management from mobile nav panel
6. **Shareable auth** — One auth component, zero duplication
7. **Subscription awareness everywhere** — Pricing page, nav bar, dashboard all know your plan
8. **A good goodbye** — Logout is deliberate, confirmed, and emotionally appropriate
