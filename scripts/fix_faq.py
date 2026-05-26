"""Fix faq.html - remove only shared auth functions, keep page-specific code."""
import os

STATIC = "/Users/openclaw_007/MemoryBridge/src/memory_bridge/static"
filename = os.path.join(STATIC, "faq.html")
SCRIPT_TAG = '<script src="/playground/auth.js?v=1"></script>\n'

with open(filename, "r") as f:
    lines = f.readlines()

original_len = len(lines)

remove_ranges = [
    (741, 807),    # JWT Validity (ensureValidJWT) + Cross-Tab + Periodic check
    (808, 813),    # decodeJWT
    (815, 895),    # updateAuthUI
    (896, 906),    # toggleUserDropdown + outside click handler
    (907, 911),    # showLogoutConfirm
    (912, 916),    # closeLogoutConfirm
    (917, 923),    # logout
]

remove_set = set()
for start, end in remove_ranges:
    for ln in range(start, end + 1):
        remove_set.add(ln)

inserted = False
new_lines = []
removed = 0

for i, line in enumerate(lines):
    line_num = i + 1
    if line_num in remove_set:
        removed += 1
        continue
    new_lines.append(line)
    if line_num == 11 and not inserted:
        new_lines.append(SCRIPT_TAG)
        inserted = True

with open(filename, "w") as f:
    f.writelines(new_lines)

print(f"faq.html: {original_len} -> {len(new_lines)} lines (removed {removed})")

content = "".join(new_lines)
checks = ["function openAuth", "// ── Auth modal", "// ── Init",
          "DOMContentLoaded", "ensureValidJWT", "initLanguage",
          "updateAuthUI", "</script>", "</body>", "</html>"]
for c in checks:
    print(f"  {c}: {content.count(c)}")
