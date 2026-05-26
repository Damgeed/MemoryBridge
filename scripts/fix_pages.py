"""Fix pages where shared auth removal was too aggressive.
Keeps page-specific auth modal functions (openAuth, socialLogin, etc.)
Removes only the actual shared auth functions (decodeJWT through getAuthHeaders).
"""

import os

STATIC = "/Users/openclaw_007/MemoryBridge/src/memory_bridge/static"
SCRIPT_TAG = '<script src="/playground/auth.js?v=1"></script>\n'

pages = [
    # (filename, remove_start, remove_end, insert_after_line)
    ("index.html",   1629, 1817, 533),
    ("demo.html",    1573, 1756, 9),
    ("graph.html",   1568, 1770, 176),
    ("api-docs.html", 1132, 1322, 324),
]

for filename, remove_start, remove_end, insert_after in pages:
    path = os.path.join(STATIC, filename)
    with open(path) as f:
        lines = f.readlines()
    
    original = len(lines)
    remove_set = set(range(remove_start, remove_end + 1))
    
    new_lines = []
    removed = 0
    inserted = False
    
    for i, line in enumerate(lines):
        ln = i + 1
        if ln in remove_set:
            removed += 1
            continue
        new_lines.append(line)
        if ln == insert_after and not inserted:
            new_lines.append(SCRIPT_TAG)
            inserted = True
    
    with open(path, "w") as f:
        f.writelines(new_lines)
    
    # Verify
    content = "".join(new_lines)
    checks = [
        ("function openAuth", 1),
        ("function socialLogin", 1),
        ("function decodeJWT", 0),
        ("async function ensureValidJWT", 0),
        ("function updateAuthUI", 0),
        ("function logout", 0),
        ("function getAuthHeaders", 0),
        ("auth.js", 1),
        ("</html>", 1),
    ]
    
    results = []
    for text, expected in checks:
        count = content.count(text)
        ok = "✓" if count == expected else "✗"
        results.append(f"{ok} {text}: {count}")
    
    print(f"{filename}: {original} -> {len(new_lines)} lines (removed {removed})")
    for r in results:
        print(f"  {r}")
    print()
