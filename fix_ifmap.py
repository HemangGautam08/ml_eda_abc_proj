#!/usr/bin/env python3
"""
fix_ifmap.py
------------
1. Removes stale per-round If_ObjOverrideCutWithML injections (lines ~540, ~653).
2. Injects If_ManMLPostProcess ONCE, just before the final `return 1;` in
   If_ManPerformMapping (after all rounds and timing updates are done).

Run from anywhere:
    python3 fix_ifmap.py

Then rebuild:
    cd /Users/hemanggautam/Desktop/eda_proj/abc
    make clean
    make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
    cp abc abc_ml
"""

import re, sys, os

ABC_DIR  = os.environ.get("ABC_DIR", "/Users/hemanggautam/Desktop/eda_proj/abc")
IFMAP    = f"{ABC_DIR}/src/map/if/ifMap.c"

with open(IFMAP) as f:
    src = f.read()

original = src  # keep for diff summary

# ── Step 1: Remove ALL existing USE_ML blocks in ifMap.c ─────────────────────
# Matches the exact pattern the old script injected (with or without blank lines
# around #ifdef / #endif).
pattern = (
    r'[ \t]*#ifdef USE_ML[ \t]*\n'
    r'(?:[ \t]*\n)*'                     # optional blank lines
    r'(?:[ \t]*[^\n]+\n)*?'             # body lines (non-greedy)
    r'[ \t]*#endif\s*/\*\s*USE_ML\s*\*/[ \t]*\n'
)
cleaned, n_removed = re.subn(pattern, '', src)
print(f"Removed {n_removed} USE_ML block(s) from ifMap.c")
src = cleaned

# Sanity: none should remain
if 'USE_ML' in src:
    print("WARNING: some USE_ML text still present — check manually:")
    for i, line in enumerate(src.splitlines(), 1):
        if 'USE_ML' in line:
            print(f"  line {i}: {line}")
    sys.exit(1)

# ── Step 2: Inject If_ManMLPostProcess before the final `return 1;` ───────────
# We anchor on the closing of the DUMP_CUTS block which is a stable landmark
# just before the return. Falls back to the bare `return 1;\n}` pattern.

ML_INJECT = (
    '#ifdef USE_ML\n'
    '    If_ManMLPostProcess( p );\n'
    '#endif /* USE_ML */\n'
)

# Anchor 1: after #endif /* DUMP_CUTS */ line
anchor1 = '#endif /* DUMP_CUTS */\n    return 1;\n}'
if anchor1 in src:
    src = src.replace(
        anchor1,
        '#endif /* DUMP_CUTS */\n' + ML_INJECT + '    return 1;\n}',
        1
    )
    print("Injected If_ManMLPostProcess after DUMP_CUTS block  ✓")
else:
    # Anchor 2: bare `    return 1;\n}` — replace the LAST occurrence
    pattern2 = r'(    return 1;\n\})'
    matches = list(re.finditer(pattern2, src))
    if not matches:
        print("ERROR: could not find injection point. Add manually before the final `return 1;`")
        sys.exit(1)
    m = matches[-1]
    src = src[:m.start()] + ML_INJECT + src[m.start():]
    print("Injected If_ManMLPostProcess before final return 1;  ✓  (fallback anchor)")

# ── Step 3: Verify ────────────────────────────────────────────────────────────
if 'If_ManMLPostProcess' not in src:
    print("ERROR: injection not found after patching — aborting.")
    sys.exit(1)

round_positions = [m.start() for m in re.finditer(r'If_ManPerformMappingRound', src)]
post_positions  = [m.start() for m in re.finditer(r'If_ManMLPostProcess', src)]

if round_positions and post_positions:
    if post_positions[-1] > round_positions[-1]:
        print("Order verified: If_ManMLPostProcess is AFTER last mapping round  ✓")
    else:
        print("WARNING: If_ManMLPostProcess appears before some mapping round calls — check ifMap.c")

# ── Step 4: Write ─────────────────────────────────────────────────────────────
with open(IFMAP, 'w') as f:
    f.write(src)
print(f"\nSaved: {IFMAP}")

# Print context around injection for quick eyeball check
lines = src.splitlines()
for i, line in enumerate(lines):
    if 'If_ManMLPostProcess' in line:
        start = max(0, i - 4)
        end   = min(len(lines), i + 5)
        print("\n── Context around injection ──────────────────────────────")
        for j in range(start, end):
            marker = " >>>" if j == i else "    "
            print(f"{marker} {j+1:4d}  {lines[j]}")
        break

print("""
── Next steps ─────────────────────────────────────────────────────────────────
cd /Users/hemanggautam/Desktop/eda_proj/abc
make clean
make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
cp abc abc_ml
echo "Build done"
──────────────────────────────────────────────────────────────────────────────
""")
