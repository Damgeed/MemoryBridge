# Security Policy

## Reporting a Vulnerability

The Memory Bridge team takes security seriously. If you discover a security vulnerability, please follow responsible disclosure:

**Do NOT open a public GitHub issue.**

Instead, send a private report to the maintainers via one of these channels:

1. **GitHub Security Advisory** — Use the [Report a Vulnerability](https://github.com/Damgeed/MemoryBridge/security/advisories/new) form (preferred)
2. **Email** — Reach out to the maintainers through the repository's security contact

Please include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Any suggested fix (if applicable)

## What to Expect

- **Acknowledgment** within 48 hours
- **Assessment** within 5 business days
- **Fix timeline** communicated within the assessment
- **Credit** in release notes (if desired)

## Scope

The security review covers:
- The Memory Bridge API server (`src/memory_bridge/`)
- The handoff protocol and guardrails
- Storage layer and data handling
- Authentication and authorization mechanisms

## Out of Scope

- Infrastructure not under the project's control (e.g., GitHub Actions runners)
- Third-party dependencies (report those to the respective maintainers)

## Hall of Fame

We thank the following researchers for their responsible disclosures:

*(None yet — be the first!)*
