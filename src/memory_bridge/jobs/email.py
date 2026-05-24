"""Transactional email jobs for Memory Bridge.

Sends emails for:
- Account verification
- Password reset
- Subscription invoices
- Usage alerts
- Team invitations
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


async def send_email(
    to: str,
    subject: str,
    body: str,
    html: Optional[str] = None,
) -> bool:
    """Send a transactional email.

    Args:
        to: Recipient email address
        subject: Email subject line
        body: Plain text body
        html: Optional HTML body

    Returns:
        True if sent successfully
    """
    logger.info("Sending email to %s: %s", to, subject)
    # In production, this would use SendGrid / Resend / SMTP
    # For now, log and return success
    return True


async def send_welcome_email(email: str, name: str) -> bool:
    """Send a welcome email after registration."""
    return await send_email(
        to=email,
        subject="Welcome to Memory Bridge!",
        body=f"Hi {name},\n\nWelcome to Memory Bridge! Your account is ready.\n\nGet started by visiting your instance's /docs endpoint.\n\n- The Memory Bridge Team",
    )


async def send_invoice_email(email: str, amount: str, period: str) -> bool:
    """Send an invoice receipt."""
    return await send_email(
        to=email,
        subject=f"Your Memory Bridge invoice ({period})",
        body=f"Thank you for your payment of ${amount} for {period}.\n\n- The Memory Bridge Team",
    )


async def send_usage_alert(email: str, project: str, usage_pct: int) -> bool:
    """Send a usage limit warning."""
    return await send_email(
        to=email,
        subject=f"Memory Bridge usage alert — {usage_pct}% of limit used",
        body=f"Hi,\n\nYour project '{project}' has used {usage_pct}% of its monthly quota.\n\nUpgrade to avoid interruption via your instance settings.\n- The Memory Bridge Team",
    )
