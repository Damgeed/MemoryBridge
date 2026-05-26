"""Surgical removal of shared auth functions from dashboard and playground.
Only removes functions that are identical to auth.js.
Keeps all page-specific variants."""

import os

STATIC = "/Users/openclaw_007/MemoryBridge/src/memory_bridge/static"

pages = [
    {
        "file": "dashboard.html",
        "remove_ranges": [
            (1590, 1590),    # // ── Shared Auth ── comment
            (1595, 1600),    # decodeJWT
            (1602, 1639),    # JWT Validity + ensureValidJWT
            (1641, 1646),    # Cross-Tab Sync
            (1648, 1666),    # Periodic JWT Expiry Check
            (1668, 1747),    # updateAuthUI (standard version)
            (1749, 1759),    # toggleUserDropdown + outside click handler
            (1761, 1769),    # showLogoutConfirm + closeLogoutConfirm
        ],
        "insert_after": 758,
        # KEPT: L1591-1593 (pending vars), L1771-1780 (logout, page-specific)
    },
    {
        "file": "playground.html",
        "remove_ranges": [
            # KEEP L1532 shared auth comment (page-specific code follows immediately)
            (1764, 1769),    # decodeJWT
            (1771, 1808),    # JWT Validity + ensureValidJWT
            (1810, 1827),    # Periodic JWT Expiry Check
            (2012, 2022),    # toggleUserDropdown + outside click handler
            (2024, 2032),    # showLogoutConfirm + closeLogoutConfirm
        ],
        "insert_after": 179,
        # KEPT: L1829-1920 updateAuthUI (page-specific), L1994-2010 cross-tab (page-specific), 
        #       L2034-2042 logout (page-specific), L2044-2049 getAuthHeaders (page-specific)
    },
]

for page in pages:
    path = os.path.join(STATIC, page["file"])
    with open(path, "r") as f:
        lines = f.readlines()

    original_len = len(lines)

    # Build set of lines to remove
    remove_set = set()
    for start, end in page["remove_ranges"]:
        for ln in range(start, end + 1):
            remove_set.add(ln)

    SCRIPT_TAG = '<script src="/playground/auth.js?v=1"></script>\n'
    inserted = False

    new_lines = []
    removed = 0

    for i, line in enumerate(lines):
        line_num = i + 1

        if line_num in remove_set:
            removed += 1
            continue

        new_lines.append(line)

        if line_num == page["insert_after"] and not inserted:
            new_lines.append(SCRIPT_TAG)
            inserted = True

    with open(path, "w") as f:
        f.writelines(new_lines)

    print(f"{page['file']}: {original_len} -> {len(new_lines)} lines (removed {removed})")
