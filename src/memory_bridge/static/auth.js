     1|/**
     2| * Memory Bridge — Shared Auth Module
     3| *
     4| * Loaded by all pages. Provides JWT management, session dot,
     5| * user menu, logout flow, and cross‑tab sync.
     6| *
     7| * Pages define these functions (optional/shared):
     8| *   openAuth() — opens sign‑in modal (each page may have its own)
     9| *   showAuthGate() — shows auth‑required overlay (info pages)
    10| *   showAuthenticatedContent(visible) — toggles page content (dashboard)
    11| */
    12|
    13|(function () {
    14|  'use strict';
    15|
    16|  const JWT_KEY = 'mb_jwt';
    17|  const API_KEY = 'mb_api_key';
    18|
    19|  /* ── Helpers ─────────────────────────────────────────── */
    20|
    21|  function getJWT() { return localStorage.getItem(JWT_KEY); }
    22|  function setJWT(t) { localStorage.setItem(JWT_KEY, t); }
    23|  function clearJWT() { localStorage.removeItem(JWT_KEY); }
    24|
    25|  function getApiKey() { return localStorage.getItem(API_KEY); }
    26|  function setApiKey(k) { localStorage.setItem(API_KEY, k); }
    27|  function clearApiKey() { localStorage.removeItem(API_KEY); }
    28|
    29|  /* ── Decode ──────────────────────────────────────────── */
    30|
    31|  function decodeJWT(token) {
    32|    try {
    33|      const payload = token.split('.')[1];
    34|      return JSON.parse(atob(payload.replace(/-/g, '+').replace(/_/g, '/')));
    35|    } catch { return null; }
    36|  }
    37|
    38|  /* ── Validate & Refresh ──────────────────────────────── */
    39|
    40|  async function ensureValidJWT() {
    41|    const jwt = getJWT();
    42|    if (!jwt) return false;
    43|    try {
    44|      const parts = jwt.split('.');
    45|      if (parts.length !== 3) { clearJWT(); return false; }
    46|      const payload = JSON.parse(atob(parts[1]));
    47|      const now = Date.now();
    48|      const expMs = payload.exp * 1000;
    49|      if (expMs < now) { clearJWT(); return false; }
    50|      if (expMs - now < 300000) {
    51|        try {
    52|          const res = await fetch('/auth/refresh', {
    53|            method: 'POST',
    54|            headers: { 'Content-Type': 'application/json' },
    55|            body: JSON.stringify({ token: jwt }),
    56|          });
    57|          if (res.ok) {
    58|            const data = await res.json();
    59|            if (data.token) setJWT(data.token);
    60|          }
    61|        } catch (e) {
    62|          console.warn('JWT refresh failed (transient), keeping current JWT:', e);
    63|        }
    64|      }
    65|      return true;
    66|    } catch { return false; }
    67|  }
    68|
    69|  /* ── Cross‑Tab Sync ──────────────────────────────────── */
    70|
    71|  window.addEventListener('storage', (e) => {
    72|    if ((e.key === JWT_KEY || e.key === API_KEY) && !e.newValue) {
    73|      if (typeof updateAuthUI === 'function') updateAuthUI();
    74|    }
    75|  });
    76|
    77|  /* ── Auth UI ─────────────────────────────────────────── */
    78|
    79|  const DEMO_API_KEY = 'mb_demo_public_test';
    80|
    81|  function isDemoKey(key) { return key === DEMO_API_KEY; }
    82|
    83|  /* ── Sign-In Toast ─────────────────────────────────────── */
    84|
    85|  function showSignInToast() {
    86|    // Only show once per session (survives page reloads — sessionStorage)
    87|    if (sessionStorage.getItem('_mb_signin_toast') === '1') return;
    88|    sessionStorage.setItem('_mb_signin_toast', '1');
    89|
    90|    const email = (decodeJWT(getJWT())?.email) || '';
    91|    const displayName = email ? email.split('@')[0] : '';
    92|
    93|    const toast = document.createElement('div');
    94|    toast.id = 'signin-toast';
    95|    toast.style.cssText = 'position:fixed;top:64px;left:50%;transform:translateX(-50%);z-index:10000;background:#10b981;color:#fff;padding:12px 24px;border-radius:8px;font-size:14px;font-weight:500;box-shadow:0 4px 12px rgba(0,0,0,0.15);display:flex;align-items:center;gap:10px;transition:opacity 0.3s,transform 0.3s;';
    96|    toast.innerHTML = '<span style="font-size:18px;">✓</span> Signed in' + (displayName ? ' as <strong>' + displayName + '</strong>' : '');
    97|    document.body.appendChild(toast);
    98|
    99|    setTimeout(() => {
   100|      toast.style.opacity = '0';
   101|      toast.style.transform = 'translateX(-50%) translateY(-10px)';
   102|      setTimeout(() => toast.remove(), 300);
   103|    }, 3000);
   104|  }
   105|
   106|  function updateAuthUI() {
   107|    const jwt = getJWT();
   108|    const key = getApiKey();
   109|    const signInBtn = document.getElementById('auth-nav-btn');
   110|    const userMenu = document.getElementById('user-menu');
   111|    const avatarName = document.getElementById('user-avatar-name');
   112|    const avatarCircle = document.getElementById('user-avatar-circle');
   113|    const dropdownEmail = document.getElementById('user-dropdown-email');
   114|
   115|    // Never show the demo key as a user identity
   116|    const hasRealSession = (jwt || (key && !isDemoKey(key)));
   117|    if (hasRealSession && userMenu && signInBtn) {
   118|      signInBtn.style.display = 'none';
   119|      userMenu.style.display = 'inline-flex';
   120|
   121|      // Show sign-in toast on transition (sessionStorage dedup prevents reload spam)
   122|      showSignInToast();
   123|
   124|      const claims = jwt ? decodeJWT(jwt) : null;
   125|      const displayName = claims?.email || claims?.name || claims?.sub || (key ? key.slice(0, 8) + '…' : '');
   126|      const initial = (displayName && displayName.length > 0) ? displayName[0].toUpperCase() : '?';
   127|
   128|      if (avatarName) avatarName.textContent = displayName.length > 16 ? displayName.slice(0, 14) + '…' : displayName;
   129|      if (avatarCircle) avatarCircle.textContent = initial;
   130|      if (dropdownEmail) dropdownEmail.textContent = displayName;
   131|
   132|      // Plan badge next to avatar (present on playground, optionally on other pages)
   133|      const planBadgeEl = document.getElementById('user-plan-badge');
   134|      const planDropdownEl = document.getElementById('user-dropdown-plan');
   135|      if (planBadgeEl && planDropdownEl) {
   136|        const planText = planDropdownEl.textContent || 'Free';
   137|        planBadgeEl.textContent = planText.length > 10 ? planText.slice(0, 8) + '…' : planText;
   138|        planBadgeEl.style.display = 'inline-block';
   139|      }
   140|
   141|      // Session dot + signed-in-since
   142|      const sessionDot = document.getElementById('session-dot');
   143|      const sinceEl = document.getElementById('user-dropdown-since');
   144|      if (sessionDot) {
   145|        if (claims && claims.exp) {
   146|          const now = Math.floor(Date.now() / 1000);
   147|          const remaining = claims.exp - now;
   148|          if (remaining <= 0) {
   149|            sessionDot.className = 'session-dot red';
   150|          } else if (remaining < 300) {
   151|            sessionDot.className = 'session-dot amber';
   152|          } else {
   153|            sessionDot.className = 'session-dot green';
   154|          }
   155|          if (sinceEl && claims.iat) {
   156|            const d = new Date(claims.iat * 1000);
   157|            const dateStr = d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
   158|            const timeStr = d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' });
   159|            sinceEl.textContent = 'Signed in since ' + dateStr + ' ' + timeStr;
   160|          }
   161|        } else if (key) {
   162|          sessionDot.className = 'session-dot green';
   163|          if (sinceEl) sinceEl.textContent = 'Using API key';
   164|        }
   165|      }
   166|
   167|      // Mobile user card
   168|      const mobileCard = document.getElementById('mobile-user-card');
   169|      const mobileBtn = document.getElementById('auth-mobile-btn');
   170|      if (mobileCard) mobileCard.style.display = 'block';
   171|      if (mobileBtn) mobileBtn.style.display = 'none';
   172|      const mobileCircle = document.getElementById('mobile-user-circle');
   173|      const mobileEmail = document.getElementById('mobile-user-email');
   174|      const mobilePlan = document.getElementById('mobile-user-plan');
   175|      const mobileSessionDot = document.getElementById('mobile-session-dot');
   176|      const mobileSince = document.getElementById('mobile-user-since');
   177|      if (mobileCircle) mobileCircle.textContent = initial || '👤';
   178|      if (mobileEmail) mobileEmail.textContent = displayName;
   179|      const planLabel = document.getElementById('user-dropdown-plan')?.textContent || 'Free Plan';
   180|      if (mobilePlan) mobilePlan.textContent = planLabel;
   181|      if (mobileSessionDot && sessionDot) mobileSessionDot.className = sessionDot.className;
   182|      if (mobileSince && sinceEl) mobileSince.textContent = sinceEl.textContent;
   183|
   184|    } else if (userMenu && signInBtn) {
   185|      signInBtn.style.display = 'inline-flex';
   186|      userMenu.style.display = 'none';
   187|      const mobileCard = document.getElementById('mobile-user-card');
   188|      const mobileBtn = document.getElementById('auth-mobile-btn');
   189|      if (mobileCard) mobileCard.style.display = 'none';
   190|      if (mobileBtn) {
   191|        mobileBtn.style.display = 'flex';
   192|        mobileBtn.onclick = function () { closeMobileNav?.(); openAuth?.(); };
   193|      }
   194|      const mobileLabel = document.getElementById('auth-mobile-label');
   195|      if (mobileLabel) mobileLabel.textContent = 'Sign in';
   196|
   197|    }
   198|  }
   199|
   200|  /* ── Dropdown ────────────────────────────────────────── */
   201|
   202|  function toggleUserDropdown() {
   203|    const menu = document.getElementById('user-menu');
   204|    if (menu) menu.classList.toggle('open');
   205|  }
   206|
   207|  document.addEventListener('click', (e) => {
   208|    const menu = document.getElementById('user-menu');
   209|    if (menu && menu.classList.contains('open') && !e.target.closest('#user-menu')) {
   210|      menu.classList.remove('open');
   211|    }
   212|  });
   213|
   214|  /* ── Logout ──────────────────────────────────────────── */
   215|
   216|  function showLogoutConfirm() {
   217|    document.getElementById('user-menu')?.classList.remove('open');
   218|    document.getElementById('logout-overlay')?.classList.add('open');
   219|    document.getElementById('logout-modal')?.classList.add('open');
   220|  }
   221|
   222|  function closeLogoutConfirm() {
   223|    document.getElementById('logout-overlay')?.classList.remove('open');
   224|    document.getElementById('logout-modal')?.classList.remove('open');
   225|  }
   226|
   227|  function logout() {
   228|    closeLogoutConfirm();
   229|    // Best-effort server-side logout
   230|    try {
   231|      fetch('/auth/logout', { method: 'POST', headers: { 'Authorization': 'Bearer ' + getJWT() } });
   232|    } catch (e) { /* ignore */ }
   233|    clearJWT();
   234|    clearApiKey();
   235|    localStorage.removeItem('mb_key_exists');
   236|    sessionStorage.removeItem('_mb_signin_toast');
   237|    if (typeof showAuthenticatedContent === 'function') showAuthenticatedContent(false);
   238|    updateAuthUI();
   239|  }
   240|
   241|  /* ── Auth Headers ────────────────────────────────────── */
   242|
   243|  function getAuthHeaders() {
   244|    const headers = { 'Content-Type': 'application/json' };
   245|    const key = window.currentApiKey || getApiKey() || '';
   246|    if (key) {
   247|      headers['Authorization'] = 'Bearer ' + key;
   248|    } else {
   249|      const jwt = getJWT();
   250|      if (jwt) headers['Authorization'] = 'Bearer ' + jwt;
   251|    }
   252|    return headers;
   253|  }
   254|
   255|  /* ── Account Recovery ────────────────────────────────── */
   256|
   257|  let _pendingRecoveryEmail = '';
   258|
   259|  async function recoverAccount(email) {
   260|    if (!email) {
   261|      const errEl = document.getElementById('auth-error-recovery');
   262|      if (errEl) errEl.textContent = 'Please enter your email address.';
   263|      return;
   264|    }
   265|    const errEl = document.getElementById('auth-error-recovery');
   266|    if (errEl) errEl.textContent = '';
   267|    const btn = document.getElementById('recovery-btn');
   268|    if (btn) {
   269|      btn.innerHTML = '<span class="spinner-sm"></span> Sending code...';
   270|      btn.disabled = true;
   271|    }
   272|    try {
   273|      // Step 0: Check if email exists in our database first
   274|      const checkRes = await fetch('/auth/auth0/check-email', {
   275|        method: 'POST',
   276|        headers: { 'Content-Type': 'application/json' },
   277|        body: JSON.stringify({ email })
   278|      });
   279|      if (!checkRes.ok) {
   280|        const err = await checkRes.json().catch(() => ({}));
   281|        throw new Error(err.detail || 'Could not verify email');
   282|      }
   283|      const checkData = await checkRes.json();
   284|      if (!checkData.exists) {
   285|        if (errEl) errEl.innerHTML = '<span class="auth-notice">No account found with this email.</span>';
   286|        if (btn) { btn.innerHTML = 'Recover Account →'; btn.disabled = false; }
   287|        return;
   288|      }
   289|
   290|      // Step 1: Send verification code to the email
   291|      const startRes = await fetch('/auth/auth0/passwordless/start', {
   292|        method: 'POST',
   293|        headers: { 'Content-Type': 'application/json' },
   294|        body: JSON.stringify({ email })
   295|      });
   296|      if (!startRes.ok) {
   297|        const err = await startRes.json().catch(() => ({}));
   298|        throw new Error(err.detail || 'Failed to send verification code');
   299|      }
   300|      _pendingRecoveryEmail = email;
   301|      // Check if this is a resend (code section already visible)
   302|      const codeSection = document.getElementById('recovery-code-section');
   303|      const isResend = codeSection && codeSection.style.display !== 'none';
   304|      // Hide email input, show code input
   305|      const emailRow = document.getElementById('recovery-email-row');
   306|      if (emailRow) emailRow.style.display = 'none';
   307|      if (codeSection) codeSection.style.display = 'block';
   308|      if (btn) {
   309|        btn.style.display = 'none';
   310|      }
   311|      const codeInput = document.getElementById('recovery-code');
   312|      if (codeInput) setTimeout(function() { codeInput.focus(); }, 100);
   313|      // Update info text
   314|      const infoText = document.querySelector('#auth-recovery-step .auth-code-msg');
   315|      if (infoText) infoText.textContent = 'Check your email';
   316|      const infoSub = document.querySelector('#auth-recovery-step .recovery-subtitle');
   317|      if (infoSub) infoSub.textContent = 'We sent a 6-digit code to ' + email;
   318|      if (isResend && errEl) errEl.textContent = '✅ New code sent!';
   319|      else if (errEl) errEl.textContent = '';
   320|    } catch (e) {
   321|      if (errEl) errEl.textContent = '' + e.message;
   322|      if (btn) { btn.innerHTML = 'Recover Account →'; btn.disabled = false; }
   323|    }
   324|  }
   325|
   326|  async function recoverVerifyCode() {
   327|    const code = document.getElementById('recovery-code')?.value;
   328|    if (!code || code.length < 6) {
   329|      const errEl = document.getElementById('auth-error-recovery');
   330|      if (errEl) errEl.textContent = 'Please enter the 6-digit code.';
   331|      return;
   332|    }
   333|    const errEl = document.getElementById('auth-error-recovery');
   334|    if (errEl) errEl.textContent = '';
   335|    const btn = document.getElementById('recovery-verify-btn');
   336|    if (btn) {
   337|      btn.innerHTML = '<span class="spinner-sm"></span> Verifying...';
   338|      btn.disabled = true;
   339|    }
   340|    try {
   341|      // Step 2: Verify the code
   342|      const verifyRes = await fetch('/auth/auth0/passwordless/verify', {
   343|        method: 'POST',
   344|        headers: { 'Content-Type': 'application/json' },
   345|        body: JSON.stringify({ email: _pendingRecoveryEmail, code })
   346|      });
   347|      if (!verifyRes.ok) {
   348|        const err = await verifyRes.json().catch(() => ({}));
   349|        throw new Error(err.detail || 'Wrong code. Please try again.');
   350|      }
   351|      // Step 3: Code verified — now recover the account
   352|      const recoverRes = await fetch('/dashboard/recover?email=' + encodeURIComponent(_pendingRecoveryEmail), { method: 'POST' });
   353|      const data = await recoverRes.json();
   354|      if (recoverRes.ok) {
   355|        if (data.key) localStorage.setItem(API_KEY, data.key);
   356|        if (data.token) localStorage.setItem(JWT_KEY, data.token);
   357|        if (typeof updateAuthUI === 'function') updateAuthUI();
   358|        if (typeof closeAuth === 'function') closeAuth();
   359|        if (typeof showToast === 'function') showToast('🔑 Account recovered!');
   360|        if (window.location.pathname.startsWith('/dashboard')) {
   361|          if (typeof showAuthenticatedContent === 'function') showAuthenticatedContent(true);
   362|          if (typeof reloadDashboard === 'function') setTimeout(reloadDashboard, 300);
   363|        } else {
   364|          setTimeout(function() { window.location.href = '/dashboard'; }, 1000);
   365|        }
   366|      } else {
   367|        if (errEl) errEl.textContent = data.detail || data.error || 'No user found. Create an account to get started.';
   368|        if (btn) { btn.innerHTML = 'Verify Code →'; btn.disabled = false; }
   369|      }
   370|    } catch (e) {
   371|      if (errEl) errEl.textContent = '' + e.message;
   372|      if (btn) { btn.innerHTML = 'Verify Code →'; btn.disabled = false; }
   373|    }
   374|  }
   375|
   376|  function showRecoveryForm() {
   377|    // Hide all auth steps
   378|    const options = document.getElementById('auth-options');
   379|    if (options) options.style.display = 'none';
   380|    const phoneStep = document.getElementById('auth-phone-step');
   381|    if (phoneStep) phoneStep.style.display = 'none';
   382|    const codeStep = document.getElementById('auth-code-step');
   383|    if (codeStep) codeStep.style.display = 'none';
   384|
   385|    // Show recovery step
   386|    const recoveryStep = document.getElementById('auth-recovery-step');
   387|    if (recoveryStep) {
   388|      _pendingRecoveryEmail = '';
   389|      // Reset to email input view
   390|      const emailRow = document.getElementById('recovery-email-row');
   391|      if (emailRow) emailRow.style.display = '';
   392|      const codeSection = document.getElementById('recovery-code-section');
   393|      if (codeSection) codeSection.style.display = 'none';
   394|      const recoveryBtn = document.getElementById('recovery-btn');
   395|      if (recoveryBtn) recoveryBtn.style.display = '';
   396|      const infoMsg = document.querySelector('#auth-recovery-step .auth-code-msg');
   397|      if (infoMsg) infoMsg.textContent = 'Recover Your Account';
   398|      const infoSub = document.querySelector('#auth-recovery-step .recovery-subtitle');
   399|      if (infoSub) infoSub.textContent = 'Enter your email to recover your API keys and subscription.';
   400|      const errEl = document.getElementById('auth-error-recovery');
   401|      if (errEl) errEl.textContent = '';
   402|      recoveryStep.style.display = 'block';
   403|      const emailInput = document.getElementById('recovery-email');
   404|      if (emailInput) setTimeout(function() { emailInput.focus(); }, 100);
   405|    }
   406|  }
   407|
   408|
   409|  /* ── Simple Init (for most pages) ────────────────────── */
   410|
   411|  async function initAuth() {
   412|    if (!await ensureValidJWT()) {
   413|      updateAuthUI();
   414|      return null;
   415|    }
   416|    updateAuthUI();
   417|    return { jwt: getJWT(), apiKey: getApiKey() };
   418|  }
   419|
   420|  /* ── Expose ──────────────────────────────────────────── */
   421|
   422|  window.decodeJWT         = decodeJWT;
   423|  window.ensureValidJWT    = ensureValidJWT;
   424|  window.updateAuthUI      = updateAuthUI;
   425|  window.toggleUserDropdown = toggleUserDropdown;
   426|  window.showLogoutConfirm  = showLogoutConfirm;
   427|  window.closeLogoutConfirm = closeLogoutConfirm;
   428|  window.logout             = logout;
   429|  window.getAuthHeaders     = getAuthHeaders;
   430|  window.initAuth           = initAuth;
   431|  window.getJWT             = getJWT;
   432|  window.setJWT             = setJWT;
   433|  window.getApiKey          = getApiKey;
   434|  window.setApiKey          = setApiKey;
   435|  window.clearJWT           = clearJWT;
   436|  window.clearApiKey        = clearApiKey;
   437|  window.showSignInToast    = showSignInToast;
   438|  window.recoverAccount     = recoverAccount;
   439|  window.recoverVerifyCode  = recoverVerifyCode;
   440|  window.showRecoveryForm   = showRecoveryForm;
   441|})();
   442|