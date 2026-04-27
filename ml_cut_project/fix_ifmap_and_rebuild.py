#!/usr/bin/env python3
"""
fix_ifmap_and_rebuild.py
========================
1. Shows the injection region in ifMap.c so you can see what went wrong.
2. Surgically reverts the (possibly-broken) injection.
3. Re-injects cleanly and verifies C syntax before rebuilding.

Run from:  ~/Desktop/eda_proj/ml_cut_project/
Then:      ./07_install_ml_into_abc.sh   (or let this script rebuild)
"""

import os, re, subprocess, sys, shutil

ABC_DIR = os.path.expanduser(
    os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc'))
IFMAP_C = os.path.join(ABC_DIR, 'src/map/if/ifMap.c')
BACKUP  = IFMAP_C + '.bak_prefix'

SEP = '─' * 70

# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)

def show_context(src, pattern, label, context=8):
    """Print lines around the first match of pattern."""
    m = re.search(pattern, src)
    if not m:
        print(f"  [{label}] *** not found ***")
        return
    lines  = src[:m.start()].count('\n')
    all_ln = src.split('\n')
    lo, hi = max(0, lines - context), min(len(all_ln), lines + context + 1)
    print(f"  [{label}] — around line {lines + 1}:")
    for i, ln in enumerate(all_ln[lo:hi], start=lo + 1):
        marker = '>>>' if i == lines + 1 else '   '
        print(f"    {marker} {i:4d}  {ln}")

# ── 1. Sanity check ───────────────────────────────────────────────────────────
print(SEP)
print("STEP 1 — Verify paths")
print(SEP)

if not os.path.isfile(IFMAP_C):
    sys.exit(f"ERROR: {IFMAP_C} not found. Check ABC_DIR.")

print(f"  ifMap.c : {IFMAP_C}  ({os.path.getsize(IFMAP_C)//1024} KB)  ✓")

# Quick crash-isolation test
print("\n  Testing abc with trivial command (no circuit)...")
abc_bin = os.path.join(ABC_DIR, 'abc')
r = run(f'"{abc_bin}" -c "quit;" 2>&1')
if r.returncode != 0:
    print(f"  abc crashes even on 'quit;'  (rc={r.returncode})")
    print("  → The binary itself is broken. Must revert ifMap.c and rebuild.")
else:
    print(f"  abc exits cleanly on 'quit;'  rc=0  ✓")
    print("  → Crash only happens with read_aiger / if command → likely ML injection.")

# ── 2. Show current injection region ─────────────────────────────────────────
print()
print(SEP)
print("STEP 2 — Show injection region in ifMap.c")
print(SEP)

with open(IFMAP_C) as f:
    src = f.read()

show_context(src, r'If_ManMLPostProcess|If_ManMLScoreAllCuts|If_ObjOverrideCutWithML',
             'ML injection', context=10)

# Show the end of If_ManPerformMapping (last `return 1;` or `return 0;`)
returns = list(re.finditer(r'^[ \t]*return\s+[01]\s*;', src, re.MULTILINE))
if returns:
    last = returns[-1]
    lines = src[:last.start()].count('\n')
    all_ln = src.split('\n')
    lo, hi = max(0, lines - 15), min(len(all_ln), lines + 5)
    print(f"\n  [Last return in file — around line {lines+1}]:")
    for i, ln in enumerate(all_ln[lo:hi], start=lo+1):
        marker = '>>>' if i == lines+1 else '   '
        print(f"    {marker} {i:4d}  {ln}")

# ── 3. Back up and revert ─────────────────────────────────────────────────────
print()
print(SEP)
print("STEP 3 — Revert ifMap.c (remove ML injections)")
print(SEP)

shutil.copy2(IFMAP_C, BACKUP)
print(f"  Backed up to: {BACKUP}")

original = src

# Remove every form of ML injection we might have inserted
# Pattern A: #ifdef USE_ML / If_ManMLPostProcess(p) / #endif
src = re.sub(
    r'\s*#ifdef USE_ML\s*\n\s*If_ManMLPostProcess\s*\(\s*p\s*\)\s*;\s*\n\s*#endif\s*(?:/\*\s*USE_ML\s*\*/)?\s*\n',
    '\n', src
)
# Pattern B: per-node override (old form)
src = re.sub(
    r'\s*#ifdef USE_ML\s*\n\s*If_ObjOverrideCutWithML\s*\([^)]+\)\s*;\s*\n\s*#endif\s*(?:/\*[^*]*\*/)?\s*\n',
    '\n', src
)
# Pattern C: If_ManMLScoreAllCuts pre-pass
src = re.sub(
    r'#ifdef USE_ML\s*\n[^\n]*If_ManMLScoreAllCuts[^\n]*\n#endif[^\n]*\n',
    '', src
)

removed = len(original) - len(src)
print(f"  Removed ~{removed} bytes of ML injection code.")

# ── 4. Find the correct injection point ───────────────────────────────────────
# We need to inject INSIDE If_ManPerformMapping, not at file end.
# Strategy: find the function, then its last `return 1;`
print()
print(SEP)
print("STEP 4 — Find correct injection point inside If_ManPerformMapping")
print(SEP)

# Locate function start
fn_match = re.search(r'int\s+If_ManPerformMapping\s*\(', src)
if not fn_match:
    # Try alternate signatures
    fn_match = re.search(r'If_ManPerformMapping\s*\(', src)

if not fn_match:
    print("  ERROR: Could not locate If_ManPerformMapping in ifMap.c")
    print("  The function may have a different name in your ABC version.")
    print("  Please open ifMap.c and manually find where mapping rounds complete.")
    sys.exit(1)

fn_start = fn_match.start()
fn_line  = src[:fn_start].count('\n') + 1
print(f"  Found If_ManPerformMapping at line {fn_line}")

# Find the opening brace of this function
brace_pos = src.find('{', fn_start)
# Walk forward matching braces to find function end
depth = 0
pos   = brace_pos
fn_end = -1
while pos < len(src):
    if src[pos] == '{':
        depth += 1
    elif src[pos] == '}':
        depth -= 1
        if depth == 0:
            fn_end = pos
            break
    pos += 1

if fn_end == -1:
    print("  ERROR: Could not find end of If_ManPerformMapping.")
    sys.exit(1)

fn_end_line = src[:fn_end].count('\n') + 1
print(f"  Function ends at line {fn_end_line}")

# Find the last `return` before fn_end
fn_body   = src[fn_start:fn_end]
ret_matches = list(re.finditer(r'^([ \t]*)return\s+[01]\s*;', fn_body, re.MULTILINE))
if not ret_matches:
    print("  WARNING: No 'return 0/1;' found inside function body.")
    print("  Injecting just before closing brace instead.")
    inject_at = fn_end
    indent    = '    '
else:
    last_ret  = ret_matches[-1]
    inject_at = fn_start + last_ret.start()
    indent    = last_ret.group(1)
    ret_line  = src[:inject_at].count('\n') + 1
    print(f"  Will inject before return at line {ret_line}  (indent='{indent}')")

# ── 5. Inject cleanly ─────────────────────────────────────────────────────────
ml_block = (
    f'#ifdef USE_ML\n'
    f'{indent}    If_ManMLPostProcess( p );\n'
    f'#endif /* USE_ML */\n'
    f'{indent}'
)

src_new = src[:inject_at] + ml_block + src[inject_at:]

# Verify injection landed after all If_ManPerformMappingRound calls
round_calls = [m.start() for m in re.finditer(r'If_ManPerformMappingRound', src_new)]
post_calls  = [m.start() for m in re.finditer(r'If_ManMLPostProcess',       src_new)]

print()
print(SEP)
print("STEP 5 — Verify injection order")
print(SEP)

if round_calls and post_calls:
    if post_calls[-1] > round_calls[-1]:
        print("  ✓ If_ManMLPostProcess is AFTER last If_ManPerformMappingRound")
    else:
        print("  ✗ WARNING: ML post-process injected BEFORE some mapping rounds!")
        print("    This will cause the ML overrides to be wiped by subsequent rounds.")
        print("    The injection point heuristic failed — check ifMap.c manually.")
else:
    print("  If_ManMLPostProcess found in file ✓")

# Show new injection context
print()
show_context(src_new, r'If_ManMLPostProcess', 'new injection', context=6)

# ── 6. Write and syntax-check ─────────────────────────────────────────────────
print()
print(SEP)
print("STEP 6 — Write ifMap.c and syntax-check")
print(SEP)

with open(IFMAP_C, 'w') as f:
    f.write(src_new)
print(f"  Written: {IFMAP_C}")

# Syntax check using clang (without linking)
# Use -DUSE_ML so the injected block is also checked
if_dir = os.path.join(ABC_DIR, 'src/map/if')
check_cmd = (
    f'clang -fsyntax-only -DUSE_ML -DABC_USE_STDINT_H '
    f'-I"{ABC_DIR}/src" -I"{if_dir}" '
    f'"{IFMAP_C}" 2>&1 | head -30'
)
print(f"  Syntax check: {check_cmd}")
r = run(check_cmd)
if r.stdout.strip():
    print("  ── clang output ──")
    print(r.stdout)
    print()
    if 'error:' in r.stdout:
        print("  ✗ Syntax errors found — please fix manually before rebuilding.")
        sys.exit(1)
    else:
        print("  (warnings are OK; errors are not)")
else:
    print("  ✓ No syntax errors (clang silent = clean)")

# ── 7. Rebuild instructions ───────────────────────────────────────────────────
print()
print(SEP)
print("STEP 7 — Rebuild")
print(SEP)
print("""
  Run the following commands:

    cd /Users/hemanggautam/Desktop/eda_proj/abc

    # Build abc_ml (with ML)
    make clean
    make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
    cp abc abc_ml
    codesign --sign - --force abc_ml

    # Build clean baseline abc
    make clean
    make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2" ABC_USE_NO_READLINE=1
    codesign --sign - --force abc

    # Quick sanity test (should NOT segfault)
    ./abc    -c "read_aiger src/map/if/ifMap.c; quit;" 2>&1 | head -5
    ./abc_ml -c "quit;" 2>&1 | head -5

  Then re-run:  python3 08_compare_qor.py
""")

print(SEP)
print("Done.  ifMap.c has been reverted and re-injected cleanly.")
print(f"Backup at: {BACKUP}")
print(SEP)
