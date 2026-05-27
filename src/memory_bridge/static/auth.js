/**
 * Memory Bridge — Shared Auth Module
 *
 * Loaded by all pages. Provides JWT management, session dot,
 * user menu, logout flow, cross‑tab sync, and periodic expiry checks.
 *
 * Pages define these callbacks (optional):
 *   window._mbOnAuthExpired() — called when JWT expires during periodic check
 *
 * Pages define these functions (optional/shared):
 *   openAuth() — opens sign‑in modal (each page may have its own)
 *   showAuthGate() — shows auth‑required overlay (info pages)
 *   showAuthenticatedContent(visible) — toggles page content (dashboard)
 */

(function () {
  'use strict';

  const JWT_KEY = 'mb_jwt';
  const API_KEY = 'mb_api_key';

  /* ── Helpers ─────────────────────────────────────────── */

  function getJWT() { return localStorage.getItem(JWT_KEY); }
  function setJWT(t) { localStorage.setItem(JWT_KEY, t); }
  function clearJWT() { localStorage.removeItem(JWT_KEY); }

  function getApiKey() { return localStorage.getItem(API_KEY); }
  function setApiKey(k) { localStorage.setItem(API_KEY, k); }
  function clearApiKey() { localStorage.removeItem(API_KEY); }

  window.__mb = window.__mb || {};

  /* ── Decode ──────────────────────────────────────────── */

  function decodeJWT(token) {
    try {
      const payload = token.split('.')[1];
      return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    } catch { return null; }
  }

  /* ── Validate & Refresh ──────────────────────────────── */

  async function ensureValidJWT() {
    const jwt = getJWT();
    if (!jwt) return false;
    try {
      const parts = jwt.split('.');
      if (parts.length !== 3) { clearJWT(); return false; }
      const payload = JSON.parse(atob(parts[1]));
      const now = Date.now();
      const expMs = payload.exp * 1000;
      if (expMs < now) { clearJWT(); return false; }
      if (expMs - now < 300000) {
        try {
          const res = await fetch('/auth/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ token: jwt }),
          });
          if (res.ok) {
            const data = await res.json();
            if (data.token) setJWT(data.token);
          }
        } catch (e) {
          console.warn('JWT refresh failed (transient), keeping current JWT:', e);
        }
      }
      return true;
    } catch { return false; }
  }

  /* ── Cross‑Tab Sync ──────────────────────────────────── */

  window.addEventListener('storage', (e) => {
    if ((e.key === JWT_KEY || e.key === API_KEY) && !e.newValue) {
      if (typeof updateAuthUI === 'function') updateAuthUI();
    }
  });

  /* ── Periodic JWT Expiry Check ───────────────────────── */

  // Default session expiry handler: orange banner 30s, then auto-open auth modal
  if (typeof window._mbOnAuthExpired !== 'function') {
    window._mbOnAuthExpired = function () {
      // Stage 1: show non-destructive orange banner
      const existing = document.getElementById('session-expiry-banner');
      if (existing) return;
      const banner = document.createElement('div');
      banner.id = 'session-expiry-banner';
      banner.style.cssText = 'position:fixed;top:56px;left:0;right:0;z-index:9999;background:#f59e0b;color:#fff;padding:10px 20px;text-align:center;font-size:14px;display:flex;align-items:center;justify-content:center;gap:8px;cursor:pointer;';
      banner.innerHTML = '<span>⚠️</span> Your session has expired. <a href="#" style="color:#fff;font-weight:700;text-decoration:underline;" id="expiry-signin-link">Sign in again</a>';
      document.body.prepend(banner);

      // Click the link to open auth modal immediately
      document.getElementById('expiry-signin-link')?.addEventListener('click', function (e) {
        e.preventDefault();
        banner.remove();
        if (typeof openAuth === 'function') openAuth();
      });

      // Stage 2: auto-open auth modal after 30 seconds
      setTimeout(function () {
        const b = document.getElementById('session-expiry-banner');
        if (b) b.remove();
        if (typeof openAuth === 'function') openAuth();
      }, 30000);
    };
  }

  setInterval(() => {
    const jwt = getJWT();
    if (!jwt) return;
    try {
      const parts = jwt.split('.');
      if (parts.length === 3) {
        const payload = JSON.parse(atob(parts[1]));
        if (payload.exp && payload.exp * 1000 < Date.now()) {
          clearJWT();
          if (typeof updateAuthUI === 'function') updateAuthUI();
          if (typeof window._mbOnAuthExpired === 'function') window._mbOnAuthExpired();
        }
      }
    } catch (e) { /* ignore */ }
  }, 60000);

  /* ── Auth UI ─────────────────────────────────────────── */

  const DEMO_API_KEY = 'mb_demo_public_test';

  function isDemoKey(key) { return key === DEMO_API_KEY; }

  /* ── Sign-In Toast ─────────────────────────────────────── */

  function showSignInToast() {
    // Only show once per session (survives page reloads — sessionStorage)
    if (sessionStorage.getItem('_mb_signin_toast') === '1') return;
    sessionStorage.setItem('_mb_signin_toast', '1');

    const email = (decodeJWT(getJWT())?.email) || '';
    const displayName = email ? email.split('@')[0] : '';

    const toast = document.createElement('div');
    toast.id = 'signin-toast';
    toast.style.cssText = 'position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:10000;background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;font-size:14px;font-weight:500;box-shadow:0 4px 12px rgba(0,0,0,0.15);display:flex;align-items:center;gap:10px;transition:opacity 0.3s,transform 0.3s;';
    toast.innerHTML = '<span style="font-size:18px;">✓</span> Signed in' + (displayName ? ' as <strong>' + displayName + '</strong>' : '');
    document.body.appendChild(toast);

    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateX(-50%) translateY(-10px)';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  }

  function updateAuthUI() {
    const jwt = getJWT();
    const key = getApiKey();
    const signInBtn = document.getElementById('auth-nav-btn');
    const userMenu = document.getElementById('user-menu');
    const avatarName = document.getElementById('user-avatar-name');
    const avatarCircle = document.getElementById('user-avatar-circle');
    const dropdownEmail = document.getElementById('user-dropdown-email');

    // Never show the demo key as a user identity
    const hasRealSession = (jwt || (key && !isDemoKey(key)));
    if (hasRealSession && userMenu && signInBtn) {
      signInBtn.style.display = 'none';
      userMenu.style.display = 'inline-flex';

      // Show sign-in toast on transition (sessionStorage dedup prevents reload spam)
      showSignInToast();

      const claims = jwt ? decodeJWT(jwt) : null;
      const displayName = claims?.email || claims?.name || claims?.sub || (key ? key.slice(0, 8) + '…' : '');
      const initial = (displayName && displayName.length > 0) ? displayName[0].toUpperCase() : '?';

      if (avatarName) avatarName.textContent = displayName.length > 16 ? displayName.slice(0, 14) + '…' : displayName;
      if (avatarCircle) avatarCircle.textContent = initial;
      if (dropdownEmail) dropdownEmail.textContent = displayName;

      // Plan badge next to avatar (present on playground, optionally on other pages)
      const planBadgeEl = document.getElementById('user-plan-badge');
      const planDropdownEl = document.getElementById('user-dropdown-plan');
      if (planBadgeEl && planDropdownEl) {
        const planText = planDropdownEl.textContent || 'Free';
        planBadgeEl.textContent = planText.length > 10 ? planText.slice(0, 8) + '…' : planText;
        planBadgeEl.style.display = 'inline-block';
      }

      // Session dot + signed-in-since
      const sessionDot = document.getElementById('session-dot');
      const sinceEl = document.getElementById('user-dropdown-since');
      if (sessionDot) {
        if (claims && claims.exp) {
          const now = Math.floor(Date.now() / 1000);
          const remaining = claims.exp - now;
          if (remaining <= 0) {
            sessionDot.className = 'session-dot red';
          } else if (remaining < 300) {
            sessionDot.className = 'session-dot amber';
          } else {
            sessionDot.className = 'session-dot green';
          }
          if (sinceEl && claims.iat) {
            const d = new Date(claims.iat * 1000);
            const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
            const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
            sinceEl.textContent = 'Signed in since ' + dateStr + ' ' + timeStr;
          }
        } else if (key) {
          sessionDot.className = 'session-dot green';
          if (sinceEl) sinceEl.textContent = 'Using API key';
        }
      }

      // Mobile user card
      const mobileCard = document.getElementById('mobile-user-card');
      const mobileBtn = document.getElementById('auth-mobile-btn');
      if (mobileCard) mobileCard.style.display = 'block';
      if (mobileBtn) mobileBtn.style.display = 'none';
      const mobileCircle = document.getElementById('mobile-user-circle');
      const mobileEmail = document.getElementById('mobile-user-email');
      const mobilePlan = document.getElementById('mobile-user-plan');
      const mobileSessionDot = document.getElementById('mobile-session-dot');
      const mobileSince = document.getElementById('mobile-user-since');
      if (mobileCircle) mobileCircle.textContent = initial || '👤';
      if (mobileEmail) mobileEmail.textContent = displayName;
      const planLabel = document.getElementById('user-dropdown-plan')?.textContent || 'Free Plan';
      if (mobilePlan) mobilePlan.textContent = planLabel;
      if (mobileSessionDot && sessionDot) mobileSessionDot.className = sessionDot.className;
      if (mobileSince && sinceEl) mobileSince.textContent = sinceEl.textContent;

    } else if (userMenu && signInBtn) {
      signInBtn.style.display = 'inline-flex';
      userMenu.style.display = 'none';
      const mobileCard = document.getElementById('mobile-user-card');
      const mobileBtn = document.getElementById('auth-mobile-btn');
      if (mobileCard) mobileCard.style.display = 'none';
      if (mobileBtn) {
        mobileBtn.style.display = 'flex';
        mobileBtn.onclick = function () { closeMobileNav?.(); openAuth?.(); };
      }
      const mobileLabel = document.getElementById('auth-mobile-label');
      if (mobileLabel) mobileLabel.textContent = 'Sign in';

    }
  }

  /* ── Dropdown ────────────────────────────────────────── */

  function toggleUserDropdown() {
    const menu = document.getElementById('user-menu');
    if (menu) menu.classList.toggle('open');
  }

  document.addEventListener('click', (e) => {
    const menu = document.getElementById('user-menu');
    if (menu && menu.classList.contains('open') && !e.target.closest('#user-menu')) {
      menu.classList.remove('open');
    }
  });

  /* ── Logout ──────────────────────────────────────────── */

  function showLogoutConfirm() {
    document.getElementById('user-menu')?.classList.remove('open');
    document.getElementById('logout-overlay')?.classList.add('open');
    document.getElementById('logout-modal')?.classList.add('open');
  }

  function closeLogoutConfirm() {
    document.getElementById('logout-overlay')?.classList.remove('open');
    document.getElementById('logout-modal')?.classList.remove('open');
  }

  function logout() {
    closeLogoutConfirm();
    // Best-effort server-side logout
    try {
      fetch('/auth/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + getJWT() } });
    } catch (e) { /* ignore */ }
    clearJWT();
    clearApiKey();
    localStorage.removeItem('mb_key_exists');
    sessionStorage.removeItem('_mb_signin_toast');
    if (typeof showAuthenticatedContent === 'function') showAuthenticatedContent(false);
    updateAuthUI();
  }

  /* ── Auth Headers ────────────────────────────────────── */

  function getAuthHeaders() {
    const headers = { 'Content-Type': 'application/json' };
    const key = window.currentApiKey || getApiKey() || '';
    if (key) {
      headers['Authorization'] = 'Bearer ' + key;
    } else {
      const jwt = getJWT();
      if (jwt) headers['Authorization'] = 'Bearer ' + jwt;
    }
    return headers;
  }

  /* ── Account Recovery ────────────────────────────────── */

  async function recoverAccount(email) {
    if (!email) {
      const errEl = document.getElementById('auth-error-recovery');
      if (errEl) errEl.textContent = 'Please enter your email address.';
      return;
    }
    const errEl = document.getElementById('auth-error-recovery');
    if (errEl) errEl.textContent = '';
    try {
      const res = await fetch('/dashboard/recover?email=' + encodeURIComponent(email), { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        if (data.key) localStorage.setItem(API_KEY, data.key);
        if (data.token) localStorage.setItem(JWT_KEY, data.token);
        if (typeof closeAuth === 'function') closeAuth();
        if (typeof showToast === 'function') showToast('🔑 Account recovered!');
        setTimeout(function() { window.location.href = '/dashboard'; }, 1000);
      } else {
        if (errEl) errEl.textContent = data.detail || data.error || 'Could not find an account with that email.';
      }
    } catch (e) {
      if (errEl) errEl.textContent = 'Network error. Please try again.';
    }
  }

  function showRecoveryForm() {
    // Hide all auth steps
    const options = document.getElementById('auth-options');
    if (options) options.style.display = 'none';
    const phoneStep = document.getElementById('auth-phone-step');
    if (phoneStep) phoneStep.style.display = 'none';
    const codeStep = document.getElementById('auth-code-step');
    if (codeStep) codeStep.style.display = 'none';

    // Hide sign-in/create-account tabs — not needed during recovery
    const authTabs = document.querySelector('.auth-modal .auth-tabs, .auth-modal .auth_tabs');
    if (authTabs) authTabs.style.display = 'none';
    // Also hide the subtitle
    const subtitle = document.getElementById('auth-subtitle');
    if (subtitle) subtitle.style.display = 'none';

    // Show recovery step
    const recoveryStep = document.getElementById('auth-recovery-step');
    if (recoveryStep) {
      recoveryStep.style.display = 'block';
      const emailInput = document.getElementById('recovery-email');
      if (emailInput) setTimeout(function() { emailInput.focus(); }, 100);
    }
  }


  /* ── Password Setup ──────────────────────────────────── */

  function showPasswordSetup() {
    let container = document.getElementById('password-setup-container');
    if (container) {
      container.style.display = 'block';
      return;
    }

    container = document.createElement('div');
    container.id = 'password-setup-container';
    container.style.cssText = 'margin-top:16px;max-width:360px;';

    const title = document.createElement('h3');
    title.textContent = 'Set Your Password';
    title.style.cssText = 'margin:0 0 12px;font-size:16px;';

    const pwInput = document.createElement('input');
    pwInput.type = 'password';
    pwInput.id = 'setup-password';
    pwInput.placeholder = 'New password';
    pwInput.style.cssText = 'width:100%;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box;margin-bottom:8px;';

    const confirmInput = document.createElement('input');
    confirmInput.type = 'password';
    confirmInput.id = 'setup-password-confirm';
    confirmInput.placeholder = 'Confirm password';
    confirmInput.style.cssText = 'width:100%;padding:10px;border:1px solid #ccc;border-radius:6px;font-size:14px;box-sizing:border-box;margin-bottom:8px;';

    const setBtn = document.createElement('button');
    setBtn.textContent = 'Set Password';
    setBtn.style.cssText = 'width:100%;padding:10px;background:#10b981;color:#fff;border:none;border-radius:6px;font-size:14px;cursor:pointer;';
    setBtn.onclick = setPassword;

    container.appendChild(title);
    container.appendChild(pwInput);
    container.appendChild(confirmInput);
    container.appendChild(setBtn);
    document.body.appendChild(container);
  }

  async function setPassword() {
    const password = document.getElementById('setup-password')?.value;
    const confirm = document.getElementById('setup-password-confirm')?.value;

    if (!password || !confirm) {
      if (typeof showError === 'function') showError('Both fields are required.');
      return;
    }
    if (password !== confirm) {
      if (typeof showError === 'function') showError('Passwords do not match.');
      return;
    }

    try {
      const res = await fetch('/auth/set-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': 'Bearer ' + getJWT() },
        body: JSON.stringify({ password }),
      });
      const data = await res.json();
      if (!res.ok) {
        if (typeof showError === 'function') showError(data.error || 'Failed to set password.');
        return;
      }
      if (typeof showSuccess === 'function') showSuccess('Password set successfully.');
    } catch (e) {
      if (typeof showError === 'function') showError('Network error.');
    }
  }

  /* ── Simple Init (for most pages) ────────────────────── */

  async function initAuth() {
    if (!await ensureValidJWT()) {
      updateAuthUI();
      return null;
    }
    updateAuthUI();
    return { jwt: getJWT(), apiKey: getApiKey() };
  }

  /* ── Expose ──────────────────────────────────────────── */

  window.decodeJWT         = decodeJWT;
  window.ensureValidJWT    = ensureValidJWT;
  window.updateAuthUI      = updateAuthUI;
  window.toggleUserDropdown = toggleUserDropdown;
  window.showLogoutConfirm  = showLogoutConfirm;
  window.closeLogoutConfirm = closeLogoutConfirm;
  window.logout             = logout;
  window.getAuthHeaders     = getAuthHeaders;
  window.initAuth           = initAuth;
  window.getJWT             = getJWT;
  window.setJWT             = setJWT;
  window.getApiKey          = getApiKey;
  window.setApiKey          = setApiKey;
  window.clearJWT           = clearJWT;
  window.clearApiKey        = clearApiKey;
  window.showSignInToast    = showSignInToast;
  window.recoverAccount     = recoverAccount;
  window.showRecoveryForm   = showRecoveryForm;
  window.showPasswordSetup  = showPasswordSetup;
  window.setPassword        = setPassword;
})();
