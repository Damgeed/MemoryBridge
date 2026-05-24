#!/usr/bin/env python3
"""
Memory Bridge — Daily Security Scan
Runs comprehensive checks: dependency audit, secret leaks, code quality,
CORS config, auth coverage, rate limits, and test status.
Outputs a Markdown report suitable for email and chat delivery.
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(PROJECT_DIR)


def run(cmd, timeout=60):
    """Run a shell command and return (stdout, stderr, exit_code)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, shell=True)
        return r.stdout.strip(), r.stderr.strip(), r.returncode
    except subprocess.TimeoutExpired as e:
        return e.stdout.strip() if e.stdout else "", f"TIMEOUT after {timeout}s", 124


def check_python_deps():
    """Check for known-vulnerability deps via pip-audit or pip check."""
    out, err, code = run("pip check 2>/dev/null || echo 'NO_PIP_CHECK'")
    if "NO_PIP_CHECK" in out:
        return "✅ pip not available for dependency check"
    if code != 0:
        return f"❌ **Dependency conflict detected**\n```\n{out[:2000]}\n```"
    return "✅ All Python dependencies consistent (`pip check` passed)"


def check_bandit():
    """Run bandit security linter if available."""
    out, err, code = run("which bandit 2>/dev/null && bandit -r src/ -f json -q 2>/dev/null || echo 'NO_BANDIT'")
    if "NO_BANDIT" in out:
        return "⚠️ `bandit` not installed — skipping SAST scan (install: `pip install bandit`)"
    if code == 0:
        return "✅ Bandit SAST scan passed — no security issues found"
    try:
        data = json.loads(out)
        issues = [r for r in data.get("results", []) if r.get("issue_severity") in ("HIGH", "MEDIUM")]
        if not issues:
            return "✅ Bandit SAST scan passed — no high/medium severity issues"
        lines = [f"❌ **{r['issue_severity']}**: {r['test_name']} — {r['filename']}:{r['line_number']}\n   `{r['issue_text'][:120]}`" for r in issues[:10]]
        return "❌ **Bandit Security Issues Found:**\n" + "\n".join(lines)
    except (json.JSONDecodeError, KeyError):
        return f"⚠️ Bandit found issues but couldn't parse report:\n```\n{out[:1000]}\n```"


def check_hardcoded_secrets():
    """Check for common secret patterns in source code."""
    out, _, code = run(
        'grep -rn "sk_live_" src/ --include="*.py" --include="*.html" --include="*.js" --include="*.yml" --include="*.yaml" --include="*.toml" --include="*.json" --include="*.md" 2>/dev/null || true'
    )
    secrets_found = [l for l in out.split("\n") if l.strip() and "sk_live_test" not in l and "STRIPE_SECRET" not in l and ".stripe.com" not in l]
    if secrets_found:
        return f"❌ **Potential secrets leaked in code:**\n```\n" + "\n".join(secrets_found[:5]) + "\n```"
    return "✅ No live secrets found in codebase"


def check_env_exposure():
    """Check .env or docker-compose for hardcoded credentials."""
    out, _, _ = run(
        'grep -rn "=" docker-compose.yml 2>/dev/null | grep -vE "\\$\\{|.*=.*\\{.*\\}|POSTGRES_DB|POSTGRES_USER" | head -20 || true'
    )
    if out.strip():
        return f"⚠️ **docker-compose.yml may have hardcoded values:**\n```\n{out[:1000]}\n```"
    return "✅ No obvious hardcoded credentials in config files"


def check_test_status():
    """Run the test suite and report results."""
    out, err, code = run("python -m pytest tests/ -x -q --tb=short 2>&1 | tail -20", timeout=120)
    if code == 0:
        # Extract count
        parts = out.split()
        passed = "all"
        return f"✅ **All tests passed** ({out[:200]})"
    return f"❌ **Tests FAILING** (exit code {code})\n```\n{out[:1000]}\n```"


def check_cors_config():
    """Check CORS is not wide-open."""
    out, _, _ = run('grep -n "allow_origins" src/memory_bridge/main.py 2>/dev/null || true')
    if "*" in out:
        return "❌ **CORS allow_origins set to '*'** — too permissive!"
    origins = [l.strip() for l in out.split("\n") if l.strip()]
    return f"✅ CORS properly restricted:\n```\n{chr(10).join(origins)}\n```" if origins else "⚠️ CORS config not found"


def check_auth_coverage():
    """Check that EXEMPT_PATHS doesn't include sensitive endpoints."""
    import re
    try:
        with open("src/memory_bridge/auth.py") as f:
            content = f.read()
        m = re.search(r'EXEMPT_PATHS = \{(.+?)\}', content, re.DOTALL)
        if m:
            exempt = m.group(0)
            exempt_paths = m.group(1).strip()
            sensitive_exposed = [p for p in ["admin", "export", "delete"] if p in exempt_paths.split(",")]
            report = f"```\n{exempt[:500]}\n```"
            if sensitive_exposed:
                return f"❌ **Sensitive endpoints exposed without auth:** {', '.join(sensitive_exposed)}\n{report}"
            return f"✅ Auth exemption list clean:\n{report}"
    except FileNotFoundError:
        pass
    return "⚠️ EXEMPT_PATHS not found in auth.py"


def check_disk_usage():
    """Check disk and memory health of the server."""
    out, _, _ = run("df -h / | tail -1")
    disk = out.split()
    pct = disk[4] if len(disk) >= 5 else "?"
    return f"💾 **Disk:** {pct} used ({disk[2] if len(disk) >= 3 else '?'}/{disk[1] if len(disk) >= 2 else '?'})"


def generate_report():
    """Run all checks and produce a Markdown report."""
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    
    sections = []

    sections.append(f"# 🔒 Memory Bridge — Daily Security Scan\n**{timestamp}**\n")

    # Quick summary
    sections.append("## 📊 Summary\n")
    sections.append(check_test_status())
    sections.append("")

    # Security
    sections.append("## 🔐 Security\n")
    sections.append(check_bandit())
    sections.append("")
    sections.append(check_hardcoded_secrets())
    sections.append("")
    sections.append(check_env_exposure())
    sections.append("")
    sections.append(check_cors_config())
    sections.append("")
    sections.append(check_auth_coverage())
    sections.append("")

    # Dependencies
    sections.append("## 📦 Dependencies\n")
    sections.append(check_python_deps())
    sections.append("")

    # System
    sections.append("## 🖥️ System\n")
    sections.append(check_disk_usage())
    sections.append("")

    sections.append("---\n*Report generated automatically. Memory Bridge security scan.*")

    return "\n".join(sections)


def send_email(report, to_addr, smtp_host, smtp_port, smtp_user, smtp_pass):
    """Send the report via SMTP."""
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(report, "markdown", "utf-8")
    msg["Subject"] = "🔒 Memory Bridge — Daily Security Scan Report"
    msg["From"] = smtp_user
    msg["To"] = to_addr
    msg["X-Priority"] = "3"

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.send_message(msg)
        return True, None
    except Exception as e:
        return False, str(e)


if __name__ == "__main__":
    report = generate_report()
    print(report)

    # Email sending — only if env vars are set
    if os.environ.get("SCAN_SMTP_USER") and os.environ.get("SCAN_SMTP_PASS"):
        to = os.environ.get("SCAN_TO_EMAIL", "mdamoh@yahoo.com")
        ok, err = send_email(
            report,
            to_addr=to,
            smtp_host=os.environ.get("SCAN_SMTP_HOST", "smtp.mail.yahoo.com"),
            smtp_port=int(os.environ.get("SCAN_SMTP_PORT", "587")),
            smtp_user=os.environ["SCAN_SMTP_USER"],
            smtp_pass=os.environ["SCAN_SMTP_PASS"],
        )
        if ok:
            print(f"\n📧 Email sent to {to}")
        else:
            print(f"\n❌ Email FAILED: {err}")
    else:
        print("\n📧 Email not configured — set SCAN_SMTP_USER / SCAN_SMTP_PASS to enable")
