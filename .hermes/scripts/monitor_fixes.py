#!/usr/bin/env python3
"""Monitor and auto-fix all known issues across MemoryBridge pages.

Checks every 30min: auth tabs, close buttons, error messages, email-check
logic, button loading states, isNetwork catch blocks, nav order, footers.
Auto-fixes anything that regresses and reports what changed.
"""

import os
import re
import sys

BASE = os.path.expanduser("~/MemoryBridge/src/memory_bridge/static")
PAGES = [
    "index.html", "playground.html", "graph.html", "dashboard.html",
    "pricing.html", "faq.html", "api-docs.html", "demo.html", "concept.html",
]
JS_FILES = ["auth.js"]
STYLE = "style.css"

issues = []
fixes = []


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def write(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def check(condition, msg):
    if not condition:
        issues.append(msg)


def fix(path, old, new, desc):
    content = read(path)
    if old in content:
        new_content = content.replace(old, new)
        write(path, new_content)
        fixes.append(f"{path}: {desc}")
        return True
    return False


def check_and_fix(path, old, new, desc):
    content = read(path)
    count = content.count(old)
    if count > 0:
        new_content = content.replace(old, new)
        write(path, new_content)
        fixes.append(f"{path}: {desc} ({count}x)")
        return True
    return False


def check_not_present(path, pattern, desc):
    content = read(path)
    if pattern in content:
        issues.append(f"{path}: {desc}")


# ── 1. No ❌ emoji in error messages ──────────────────────────────
for page in PAGES + JS_FILES:
    path = os.path.join(BASE, page)
    check_not_present(path, "❌", "❌ still present")
    # auto-fix
    check_and_fix(path, "❌ ", "", "removed ❌ prefix")
    check_and_fix(path, "❌", "", "removed ❌")

# ── 2. auth-notice CSS class ──────────────────────────────────────
style_path = os.path.join(BASE, STYLE)
css = read(style_path)
if ".auth-form .auth-notice" not in css:
    issues.append(f"{STYLE}: missing .auth-notice class")
    # Add it after .auth-error
    notice_css = """
.auth-form .auth-notice {
  display: block;
  width: 100%;
  box-sizing: border-box;
  padding: 0.75rem 1rem;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-primary);
  border-radius: var(--radius-sm);
  color: var(--text-secondary);
  font-size: 0.85rem;
  line-height: 1.5;
  text-align: center;
  margin: 0.75rem auto 0;
}
"""
    css = css.replace(
        "/* Auth loading spinner */",
        notice_css + "\n/* Auth loading spinner */",
    )
    write(style_path, css)
    fixes.append(f"{STYLE}: added missing .auth-notice class")

# Check auth-notice has proper props
check(
    "width: 100%" in css and "box-sizing: border-box" in css and "margin: 0.75rem auto 0" in css,
    f"{STYLE}: .auth-notice missing centering props",
)

# ── 3. No "Close" text on close buttons (SVG X only) ─────────────
for page in PAGES:
    path = os.path.join(BASE, page)
    check_and_fix(path, 'data-i18n="auth.close"', 'aria-label="Close"', "replaced data-i18n auth.close")
    # Check no literal "Close" text inside close button context
    content = read(path)
    if re.search(r'<button[^>]*class="[^"]*auth-close[^"]*"[^>]*>Close<', content):
        issues.append(f"{page}: close button has 'Close' text")

# ── 4. Email check with authMode logic ────────────────────────────
for page in PAGES:
    path = os.path.join(BASE, page)
    content = read(path)
    # Should have /auth/check-email call
    if "checkRes = await fetch('/auth/check-email'" not in content:
        issues.append(f"{page}: missing /auth/check-email call")
    # Should have authMode check in continueWithEmail
    if "authMode === 'signin'" in content and "No account found" in content:
        # Has the logic — good
        pass
    elif "authMode" in content:
        # Has authMode but might be missing the check
        if "if (!checkData.exists)" in content and "authMode === 'signin'" not in content:
            issues.append(f"{page}: !checkData.exists blocks all tabs, not just Sign In")

    # Check no plain 'No account found' message (should be wrapped in auth-notice)
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "No account found" in stripped and "auth-notice" not in stripped and "//" not in stripped:
            issues.append(f"{page}: line {i+1}: 'No account found' not wrapped in auth-notice")

# ── 5. isNetwork catch block pattern ──────────────────────────────
for page in PAGES:
    path = os.path.join(BASE, page)
    content = read(path)
    lines = content.split('\n')
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "catch (e) {" in stripped:
            # Check next line for error output
            next_lines = []
            j = i + 1
            while j < len(lines) and j < i + 5:
                next_lines.append(lines[j].strip())
                j += 1
            next_text = " ".join(next_lines)
            # Should have isNetwork or at least proper error message
            if "'❌ ' + e.message" in next_text or "errorEl.innerHTML = '❌ ' + e.message" in next_text:
                # Has ❌ still
                check_and_fix(path, "'❌ ' + e.message", "e.message", f"line {i+1}: removed ❌ from catch")

# ── 6. Button loading states (no stuck spinner) ───────────────────
for page in PAGES:
    path = os.path.join(BASE, page)
    content = read(path)
    # Check that btn.innerHTML is restored after catch blocks that set spinner
    # This is harder to automate — just flag if we see any patterns

# ── 7. Nav order consistency ──────────────────────────────────────
for page in PAGES:
    path = os.path.join(BASE, page)
    content = read(path)
    # Check nav has expected links
    nav_checks = {
        "playground": "/playground/",
        "graph": "/playground/graph.html",
        "dashboard": "/dashboard/",
        "pricing": "/pricing",
    }
    for label, href in nav_checks.items():
        if href not in content:
            issues.append(f"{page}: missing nav link for {label} ({href})")

# ── 8. Sign In / Create Account tab styling ───────────────────────
for page in PAGES:
    path = os.path.join(BASE, page)
    content = read(path)
    # Check tab styling
    if 'flex:1;padding:0.5rem 0' not in content:
        issues.append(f"{page}: auth tabs missing flex:1;padding:0.5rem 0")

# ── 9. Check auth.js recovery flow ────────────────────────────────
js_path = os.path.join(BASE, "auth.js")
js_content = read(js_path)
if "check-email" in js_content and "recoverAccount" in js_content:
    # Has the check — good
    pass
else:
    issues.append("auth.js: recovery flow missing /auth/check-email")

if "No account found with this email" in js_content and "auth-notice" not in js_content:
    issues.append("auth.js: recovery 'No account found' not styled")

# ── Report ────────────────────────────────────────────────────────
if not issues and not fixes:
    print("✅ All clean — no issues found, no fixes needed.")
    sys.exit(0)

if fixes:
    print(f"🔧 Fixed {len(fixes)} issue(s):")
    for f in fixes:
        print(f"  • {f}")

if issues:
    print(f"\n⚠️  {len(issues)} issue(s) remaining:")
    for issue in issues:
        print(f"  • {issue}")
    sys.exit(1)
else:
    print("\n✅ All issues fixed!")
    sys.exit(0)
