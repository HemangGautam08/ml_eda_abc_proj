#!/bin/zsh
# =============================================================================
# 07_install_ml_into_abc.sh
#
# KEY FIX: ML override is now injected at the END of If_ManPerformMapping()
# (just before its final return), NOT inside the per-round node loop.
#
# WHY: ABC runs 2-3 rounds of technology mapping. Injecting inside the loop
# meant round 1 re-ran If_ObjPerformMapping for every node and silently
# overwrote all of round 0's ML overrides. By the time print_stats ran,
# ABC had fully reverted to its own choices — hence exactly 0.00% on all
# benchmarks.
#
# The fix: If_ManMLPostProcess(p) runs ONCE after all rounds and timing
# updates complete, with fully converged required_time/slack values.
# =============================================================================

cd "$(dirname "$0")"

export ABC_DIR="${ABC_DIR:-/Users/hemanggautam/Desktop/eda_proj/abc}"
IF_DIR="$ABC_DIR/src/map/if"
PATCH_DIR="abc_patch"

echo "Installing ML files into ABC: $ABC_DIR"
echo "Source patch dir: $(pwd)/$PATCH_DIR"

# ── Check required files ──────────────────────────────────────────────────────
for f in "$PATCH_DIR/ifML.c" "$PATCH_DIR/model_weights.h"; do
  if [[ ! -f "$f" ]]; then
    echo "ERROR: $f not found."
    [[ "$f" == *"model_weights.h"* ]] && echo "  Run: python3 06_export_weights_to_c.py"
    exit 1
  fi
done

# Read ML_INPUT_DIM from the generated header
ACTUAL_DIM=$(grep "define ML_INPUT_DIM" "$PATCH_DIR/model_weights.h" | awk '{print $3}')
if [[ -z "$ACTUAL_DIM" || ! "$ACTUAL_DIM" =~ ^[0-9]+$ ]]; then
  echo "ERROR: Could not read ML_INPUT_DIM from $PATCH_DIR/model_weights.h."
  echo "  Re-run: python3 06_export_weights_to_c.py"
  exit 1
fi

# Verify ifML.c and model_weights.h agree on the input dimension
IFML_DIM=$(grep -E 'Architecture:.*->.*->.*->.*1' "$PATCH_DIR/ifML.c" | grep -oE '^[0-9]+' | head -1)
if [[ -n "$IFML_DIM" && "$IFML_DIM" != "$ACTUAL_DIM" ]]; then
  echo "WARNING: ifML.c architecture comment shows $IFML_DIM inputs but model_weights.h has $ACTUAL_DIM."
  echo "  ifML.c comment may be stale — the header value ($ACTUAL_DIM) is authoritative."
fi
echo "model_weights.h: ML_INPUT_DIM=$ACTUAL_DIM  ✓"

# ── Copy ML source files ──────────────────────────────────────────────────────
cp "$PATCH_DIR/ifML.c"          "$IF_DIR/ifML.c"
cp "$PATCH_DIR/model_weights.h" "$IF_DIR/model_weights.h"
echo "Copied ifML.c and model_weights.h to $IF_DIR  ✓"

# ── Add ifML.c to module.make ─────────────────────────────────────────────────
MODULE_MAKE="$IF_DIR/module.make"
if ! grep -q "ifML.c" "$MODULE_MAKE"; then
  echo "SRC += src/map/if/ifML.c" >> "$MODULE_MAKE"
  echo "Added ifML.c to module.make"
else
  echo "module.make already references ifML.c"
fi

# ── Patch if.h: forward declarations ─────────────────────────────────────────
python3 << 'PYEOF'
import os, sys, re

abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
path    = f"{abc_dir}/src/map/if/if.h"

with open(path) as f:
    src = f.read()

# Check if already correctly patched (int return type + all 3 functions)
already_correct = (
    'If_ManMLPostProcess' in src and
    'extern int  If_ObjOverrideCutWithML' in src
)
if already_correct:
    print("if.h: ML declarations already correct — skipping")
    sys.exit(0)

# Remove ALL existing ML blocks (handles duplicates and wrong void types)
src = re.sub(
    r'/\* ML cut override.*?#endif /\* USE_ML \*/\n',
    '', src, flags=re.DOTALL
)
src = re.sub(
    r'/\* Forward declarations for USE_ML.*?#endif /\* USE_ML \*/\n',
    '', src, flags=re.DOTALL
)
# Remove any stray partial blocks
src = re.sub(
    r'#ifdef USE_ML\s*\nextern.*?If_ObjOverrideCutWithML.*?\n#endif /\* USE_ML \*/\n',
    '', src, flags=re.DOTALL
)

decl = (
    '\n/* ML cut override — If_ManMLPostProcess called after all mapping rounds */\n'
    '#ifdef USE_ML\n'
    'extern int  If_ObjOverrideCutWithML( If_Man_t * p, If_Obj_t * pObj );\n'
    'extern void If_ManMLPostProcess( If_Man_t * p );\n'
    'extern void If_ManMLScoreAllCuts( If_Man_t * p );\n'
    '#endif /* USE_ML */\n'
)

# Insert just before ABC_NAMESPACE_HEADER_END
if 'ABC_NAMESPACE_HEADER_END' in src:
    src = src.replace('ABC_NAMESPACE_HEADER_END', decl + 'ABC_NAMESPACE_HEADER_END', 1)
else:
    # Fallback: insert before last #endif
    lines = src.rstrip().split('\n')
    last_endif = next((i for i in range(len(lines)-1, -1, -1)
                       if lines[i].strip() == '#endif'), -1)
    if last_endif >= 0:
        lines.insert(last_endif, decl)
        src = '\n'.join(lines) + '\n'
    else:
        src += decl

with open(path, 'w') as f:
    f.write(src)
print("if.h: ML declarations added/corrected (int return type)  ✓")
PYEOF
[[ $? -ne 0 ]] && exit 1

# ── Patch ifMap.c: inject If_ManMLPostProcess at end of If_ManPerformMapping ──
#
# Strategy: find the final `return 1;` (or `return 0;`) in If_ManPerformMapping
# and insert the ML post-process call just before it.
#
# If_ManPerformMapping is the outer function that calls per-round mapping.
# We inject AFTER all rounds complete so timing is fully converged.
# ──────────────────────────────────────────────────────────────────────────────
python3 << 'PYEOF'
import re, os, sys

abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
path    = f"{abc_dir}/src/map/if/ifMap.c"

with open(path) as f:
    src = f.read()

# Remove any old per-round injection if present
if 'If_ObjOverrideCutWithML' in src or 'If_ManMLPostProcess' in src:
    # Remove old mid-loop injections
    src = re.sub(
        r'\s*#ifdef USE_ML\s*\n\s*If_ObjOverrideCutWithML\s*\(\s*p\s*,\s*pObj\s*\)\s*;\s*\n\s*#endif\s*/\*\s*USE_ML\s*\*/\s*\n',
        '\n', src
    )
    # Remove old If_ManMLScoreAllCuts pre-pass
    src = re.sub(
        r'#ifdef USE_ML\s*\n\s*/\*.*?\*/\s*\n\s*If_ManMLScoreAllCuts\s*\(\s*p\s*\)\s*;\s*\n#endif\s*/\*\s*USE_ML\s*\*/\s*\n',
        '', src, flags=re.DOTALL
    )
    if 'If_ManMLPostProcess' in src:
        print("ifMap.c: If_ManMLPostProcess already injected — skipping")
        sys.exit(0)
    print("ifMap.c: removed old per-round injection(s)")

ml_call = (
    '#ifdef USE_ML\n'
    '    If_ManMLPostProcess( p );\n'
    '#endif /* USE_ML */\n'
)

# ── Injection strategy ────────────────────────────────────────────────────────
# Find If_ManPerformMapping function body and inject before its final return.
# We try multiple patterns in order of specificity.
injected = False

# Strategy 1: inject before `return 1;` that follows the last mapping round call
# Look for the last If_ManPerformMappingRound call, then find the next return
strategies = [
    # After the last call to If_ManPerformMappingRound, before return
    (r'(If_ManPerformMappingRound\s*\([^;]+;\s*\n)((?:(?!If_ManPerformMappingRound).)*?)([ \t]*return\s+[01]\s*;)',
     True),   # uses DOTALL
    # Before the last `return 1;` in If_ManPerformMapping (simpler)
    (r'([ \t]*)(return 1;\s*\n\})', False),
]

for pattern, dotall in strategies:
    flags = re.DOTALL if dotall else 0
    matches = list(re.finditer(pattern, src, flags=flags))
    if not matches:
        continue

    m = matches[-1]   # last match = end of If_ManPerformMapping

    if dotall:
        # Insert ml_call between the round call and the return
        insert_at = m.start(3)
        src = src[:insert_at] + '    ' + ml_call + src[insert_at:]
    else:
        # Insert ml_call before `return 1;`
        insert_at = m.start(2)
        src = src[:insert_at] + ml_call + src[insert_at:]

    injected = True
    print(f"ifMap.c: If_ManMLPostProcess injected at end of If_ManPerformMapping  ✓")
    break

if not injected:
    # Fallback: inject before the very last `return` in the file that looks like
    # the end of a mapping function. Warn the user.
    last_ret = [m for m in re.finditer(r'^    return 1;\n\}', src, re.MULTILINE)]
    if last_ret:
        m = last_ret[-1]
        src = src[:m.start()] + '    ' + ml_call + src[m.start():]
        injected = True
        print("ifMap.c: If_ManMLPostProcess injected (fallback — verify manually)  ⚠")
    else:
        print("ERROR: Could not find injection point in If_ManPerformMapping.")
        print("Add manually just before the final `return 1;` in If_ManPerformMapping:")
        print("  #ifdef USE_ML")
        print("  If_ManMLPostProcess( p );")
        print("  #endif")
        sys.exit(1)

with open(path, 'w') as f:
    f.write(src)
print("ifMap.c saved.")
PYEOF
[[ $? -ne 0 ]] && exit 1

# ── Verify injection landed inside If_ManPerformMapping ───────────────────────
python3 << 'PYEOF'
import os, sys, re
abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
path    = f"{abc_dir}/src/map/if/ifMap.c"
with open(path) as f:
    src = f.read()

if 'If_ManMLPostProcess' not in src:
    print("ERROR: If_ManMLPostProcess not found in ifMap.c after patching!")
    sys.exit(1)

# Check it appears after (not before) If_ManPerformMappingRound
round_positions = [m.start() for m in re.finditer(r'If_ManPerformMappingRound', src)]
post_pos        = [m.start() for m in re.finditer(r'If_ManMLPostProcess', src)]

if round_positions and post_pos:
    if post_pos[-1] > round_positions[-1]:
        print("Injection order verified: If_ManMLPostProcess is AFTER last mapping round  ✓")
    else:
        print("WARNING: If_ManMLPostProcess appears BEFORE some mapping round calls.")
        print("  Check ifMap.c manually — injection may fire too early.")
else:
    print("If_ManMLPostProcess found in ifMap.c  ✓")
PYEOF
[[ $? -ne 0 ]] && exit 1

# ── Build abc_ml (USE_ML) ─────────────────────────────────────────────────────
cd "$ABC_DIR" || exit 1
NCPU=$(sysctl -n hw.ncpu)

echo "\n── Building abc_ml (USE_ML enabled) ────────────────────────────────────"
make clean > /dev/null 2>&1
make -j"$NCPU" OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1 2>&1 | tail -10
MAKE_STATUS=${pipestatus[1]}
if [[ $MAKE_STATUS -ne 0 ]]; then
  echo "ERROR: abc_ml build failed (make exit code $MAKE_STATUS)."
  echo "Tip:  cd $ABC_DIR && make -j1 OPTFLAGS='-O2 -DUSE_ML' ABC_USE_NO_READLINE=1"
  exit 1
fi
cp abc abc_ml
codesign --sign - --force abc_ml
codesign --sign - --force abc
codesign --sign - --force "$ABC_DIR/abc_ml"
echo "Signed abc_ml  ✓"
echo "Created: $ABC_DIR/abc_ml  ✓"

# ── Rebuild clean baseline ────────────────────────────────────────────────────
echo "\n── Building abc (clean baseline) ───────────────────────────────────────"
make clean > /dev/null 2>&1
make -j"$NCPU" OPTFLAGS="-O2" ABC_USE_NO_READLINE=1 2>&1 | tail -4
if [[ ${pipestatus[1]} -ne 0 ]]; then
  echo "ERROR: baseline abc build failed."
  exit 1
fi
echo "Created: $ABC_DIR/abc  ✓"

echo ""
for bin in abc abc_ml; do
  if [[ -f "$ABC_DIR/$bin" ]]; then
    sz=$(du -sh "$ABC_DIR/$bin" | cut -f1)
    echo "  ✓ $bin  ($sz)"
  else
    echo "  ✗ $bin NOT FOUND"
  fi
done

echo "\nDone. Next: python3 08_compare_qor.py"