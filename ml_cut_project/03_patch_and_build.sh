#!/bin/zsh
# =============================================================================
# 03_patch_and_build.sh  — MERGED v3  (replaces 03_apply_abc_patch.sh + 03b)
#
# Why the old scripts kept failing
# ─────────────────────────────────
#   • The .orig backup files were saved AFTER a previous partial-patch run,
#     so "restoring" from them gave a broken state where the script would
#     report "MLScore already present" yet the struct field was absent or
#     the forward declarations were placed outside the abc:: namespace.
#   • 03b's FIX-B used fragile line-stripping logic that could silently
#     corrupt the header when trying to move the declarations.
#   • The two-file split meant partial state from 03b could survive into
#     the next run of 03.
#
# This script
# ───────────
#   1. Restores originals via `git checkout` (the only trustworthy source).
#      Falls back to .orig only when git is unavailable AND .orig is verified
#      to be truly unpatched (contains none of our sentinel strings).
#   2. Applies every patch in Python with explicit pre-condition checks so a
#      wrong-state file causes an early, descriptive error instead of silent
#      corruption.
#   3. Builds abc_dump and the clean baseline in a single pass.
#   4. Requires no separate fix script.
# =============================================================================

export ABC_DIR="${ABC_DIR:-/Users/hemanggautam/Desktop/eda_proj/abc}"
IF_DIR="$ABC_DIR/src/map/if"

echo "========================================================"
echo " ABC patch + build  (merged v3)"
echo " ABC_DIR = $ABC_DIR"
echo "========================================================"
echo ""

if [[ ! -d "$IF_DIR" ]]; then
  echo "ERROR: $IF_DIR not found."
  echo "Set:  export ABC_DIR=/path/to/abc   then re-run."
  exit 1
fi

# ── Step 0: Restore clean originals ──────────────────────────────────────────
# Sentinel strings that only appear in patched files
SENTINELS=(DUMP_CUTS If_DumpNodeCuts MLScore If_CutCompareByMLScore)

restore_file() {
  local rel="$1"          # e.g. src/map/if/if.h
  local full="$ABC_DIR/$rel"
  local orig="${full}.orig"

  # Try git first — always correct
  if git -C "$ABC_DIR" checkout HEAD -- "$rel" 2>/dev/null; then
    echo "git-restored  $full"
    # Verify sentinel-free after restore
    for s in $SENTINELS; do
      if grep -q "$s" "$full" 2>/dev/null; then
        echo "WARNING: sentinel '$s' still present after git restore."
        echo "  This means HEAD itself is patched. Run:"
        echo "    git -C $ABC_DIR stash   OR   git -C $ABC_DIR checkout <orig-commit> -- $rel"
        exit 1
      fi
    done
    # Delete stale .orig so it can't mislead future runs
    [[ -f "$orig" ]] && rm -f "$orig" && echo "  (deleted stale $orig)"
    return 0
  fi

  # Git unavailable — try .orig
  if [[ -f "$orig" ]]; then
    local contaminated=0
    for s in $SENTINELS; do
      if grep -q "$s" "$orig" 2>/dev/null; then
        contaminated=1
        echo "  .orig sentinel found: '$s'"
      fi
    done
    if (( contaminated )); then
      echo ""
      echo "ERROR: $orig is contaminated (contains patch sentinel strings)."
      echo "  It was saved AFTER a previous patch run and cannot be used as"
      echo "  a clean baseline.  You must restore from the ABC git history:"
      echo ""
      echo "    git -C $ABC_DIR checkout HEAD -- $rel"
      echo ""
      echo "  Or if HEAD is also patched, checkout the original commit."
      exit 1
    fi
    cp "$orig" "$full"
    echo "orig-restored $full"
    rm -f "$orig"
    echo "  (deleted $orig — git is preferred for future runs)"
    return 0
  fi

  # Nothing to restore from — file is what it is; check for contamination
  for s in $SENTINELS; do
    if grep -q "$s" "$full" 2>/dev/null; then
      echo ""
      echo "ERROR: $full appears already patched (contains '$s') and no"
      echo "clean backup exists. Restore the original with:"
      echo "  git -C $ABC_DIR checkout HEAD -- $rel"
      exit 1
    fi
  done
  echo "no-backup     $full  (assuming it is already clean)"
}

restore_file "src/map/if/if.h"
restore_file "src/map/if/ifCut.c"
restore_file "src/map/if/ifMap.c"
echo ""

# ── Step 1: Patch if.h ───────────────────────────────────────────────────────
echo "--- Patching if.h ---"
python3 << 'PYEOF'
import sys, os

abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
path    = f"{abc_dir}/src/map/if/if.h"

with open(path) as f:
    src = f.read()

changed = False

# ── 1a: MLScore field in If_Cut_t, immediately after Area ────────────────────
OLD_AREA = '    float              Area;          // area (or area-flow) of the cut'
NEW_AREA = (OLD_AREA +
    '\n    float              MLScore;       /* ML quality score (0.0=low 1.0=high) */')

if OLD_AREA not in src:
    print("ERROR: Could not find 'float Area;' line in If_Cut_t — has if.h been modified?")
    sys.exit(1)

if 'MLScore' in src:
    print("if.h: MLScore already present — skipping (unexpected; check struct manually)")
else:
    src = src.replace(OLD_AREA, NEW_AREA, 1)
    changed = True
    print("if.h: MLScore field added to If_Cut_t  ✓")

# ── 1b: Forward declarations BEFORE ABC_NAMESPACE_HEADER_END ─────────────────
#   They must be inside the abc:: namespace so the extern linkage resolves.
#   ABC_NAMESPACE_HEADER_END closes that namespace — any extern AFTER it is
#   in the global namespace, which mismatches the definitions in ifCut.c.
NS_END  = 'ABC_NAMESPACE_HEADER_END'
DECLS   = (
    '\n/* Forward declarations for DUMP_CUTS helpers (ifCut.c) */\n'
    '#ifdef DUMP_CUTS\n'
    'extern void If_DumpCutsOpen( const char * pCircuitName );\n'
    'extern void If_DumpCutsClose( void );\n'
    'extern void If_DumpNodeCuts( If_Man_t * p, If_Obj_t * pObj );\n'
    '#endif /* DUMP_CUTS */\n'
    '\n'
    '/* Forward declarations for USE_ML (ifML.c) */\n'
    '#ifdef USE_ML\n'
    'extern void If_ObjOverrideCutWithML( If_Man_t * p, If_Obj_t * pObj );\n'
    'extern void If_ManMLScoreAllCuts( If_Man_t * p );\n'
    '#endif /* USE_ML */\n'
    '\n'
)

if NS_END not in src:
    print("ERROR: ABC_NAMESPACE_HEADER_END not found in if.h")
    sys.exit(1)

# Check where the declarations already are (if at all)
ns_pos   = src.find(NS_END)
dump_pos = src.find('If_DumpNodeCuts')

if dump_pos >= 0 and dump_pos > ns_pos:
    # Declarations exist but are AFTER the namespace end — wrong place.
    # Remove them from after the namespace end and re-insert before it.
    print("if.h: declarations found AFTER ABC_NAMESPACE_HEADER_END — relocating...")
    # Split at NS_END; strip the misplaced declarations from the tail
    before_ns, after_ns = src.split(NS_END, 1)
    # Remove all our injected lines from the tail
    import re
    after_ns = re.sub(
        r'\n?/\* Forward declarations for (?:DUMP_CUTS|USE_ML).*?#endif /\* (?:DUMP_CUTS|USE_ML) \*/\n?',
        '', after_ns, flags=re.DOTALL)
    src = before_ns + DECLS + NS_END + after_ns
    changed = True
    print("if.h: declarations relocated BEFORE ABC_NAMESPACE_HEADER_END  ✓")
elif dump_pos < 0:
    src = src.replace(NS_END, DECLS + NS_END, 1)
    changed = True
    print("if.h: forward declarations added BEFORE ABC_NAMESPACE_HEADER_END  ✓")
else:
    print("if.h: forward declarations already correctly placed  ✓")

if changed:
    with open(path, 'w') as f:
        f.write(src)
print("if.h done.")
PYEOF
[[ $? -ne 0 ]] && { echo "ERROR: if.h patch failed."; exit 1; }
echo ""

# ── Step 2: Patch ifCut.c ─────────────────────────────────────────────────────
echo "--- Patching ifCut.c ---"
python3 << 'PYEOF'
import sys, os

abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
path    = f"{abc_dir}/src/map/if/ifCut.c"

with open(path) as f:
    src = f.read()

# ── 2a: DUMP_CUTS helper block ────────────────────────────────────────────────
DUMP_BLOCK = r'''
/* =========================================================================
   DUMP_CUTS helpers
   Compiled only when -DDUMP_CUTS is passed to the compiler.
   ========================================================================= */
#ifdef DUMP_CUTS
#include <stdio.h>
#include <stdlib.h>
static FILE * s_pDumpFile = NULL;

void If_DumpCutsOpen( const char * pCircuitName )
{
    char fname[512];
    snprintf(fname, sizeof(fname), "data/%s_cuts.csv", pCircuitName);
    system("mkdir -p data");
    s_pDumpFile = fopen(fname, "w");
    if (!s_pDumpFile) { fprintf(stderr, "[DUMP] Cannot open %s\n", fname); return; }
    fprintf(s_pDumpFile,
        "node_id,cut_idx,n_leaves,cut_delay,area_flow,"
        "node_level,required_time,slack,node_fanout,mffc_size,is_best\n");
}

void If_DumpCutsClose( void )
{
    if (s_pDumpFile) { fclose(s_pDumpFile); s_pDumpFile = NULL; }
}

/*
 * If_DumpNodeCuts — emit one CSV row per cut of pObj.
 *
 * IMPORTANT: called from inside If_ObjPerformMappingAnd, BEFORE
 * If_ManDerefNodeCutSet, so pObj->pCutSet is still fully valid.
 *
 * Loop variable is pLeaf, NOT pObj — do NOT shadow the function parameter
 * pObj which is the root node being processed (FIX-3).
 *
 * is_best = (i == 0): after If_CutSort the best cut is always at index 0.
 * Comparing pCut == If_ObjCutBest(pObj) always returned 0 because CutBest
 * is a struct copy, not a ppCuts pointer (FIX-5).
 *
 * mffc_size is approximated from pCut->Area (area-flow correlates well with
 * MFFC node count; an exact walk would need stable ref-count state that is
 * not guaranteed here) (FIX-4).
 */
void If_DumpNodeCuts( If_Man_t * p, If_Obj_t * pObj )
{
    If_Cut_t * pCut;
    int i;
    (void)p;
    if (!s_pDumpFile) return;
    if (If_ObjIsCi(pObj) || If_ObjIsConst1(pObj)) return;
    If_ObjForEachCut(pObj, pCut, i)
    {
        float slack     = pObj->Required - pCut->Delay;
        int   is_best   = (i == 0) ? 1 : 0;
        int   mffc_size = (int)(pCut->Area + 0.5f);
        if (mffc_size < (int)pCut->nLeaves) mffc_size = (int)pCut->nLeaves;
        if (mffc_size > 1000)               mffc_size = 1000;
        fprintf(s_pDumpFile, "%d,%d,%d,%.6f,%.6f,%d,%.6f,%.6f,%d,%d,%d\n",
            pObj->Id, i,
            (int)pCut->nLeaves,
            (double)pCut->Delay,
            (double)pCut->Area,
            (int)pObj->Level,
            (double)pObj->Required,
            (double)slack,
            (int)pObj->nRefs,
            mffc_size,
            is_best);
    }
}
#endif /* DUMP_CUTS */
'''

if 'If_DumpCutsOpen' not in src:
    lines    = src.split('\n')
    last_inc = max((i for i, l in enumerate(lines)
                    if l.strip().startswith('#include')), default=-1)
    if last_inc < 0:
        print("ERROR: No #include found in ifCut.c")
        sys.exit(1)
    lines.insert(last_inc + 1, DUMP_BLOCK)
    src = '\n'.join(lines)
    print("ifCut.c: DUMP_CUTS helper block inserted  ✓")
else:
    print("ifCut.c: DUMP_CUTS helpers already present — skipping")

# ── 2b: ML score comparator ───────────────────────────────────────────────────
ML_CMP = '''
/* =========================================================================
   ML-score comparator — higher MLScore is a better cut.
   Requires the MLScore field added to If_Cut_t in if.h (Patch 1).
   ========================================================================= */
int If_CutCompareByMLScore( If_Cut_t ** ppC0, If_Cut_t ** ppC1 )
{
    if ( (*ppC0)->MLScore > (*ppC1)->MLScore ) return -1;
    if ( (*ppC0)->MLScore < (*ppC1)->MLScore ) return  1;
    if ( (*ppC0)->nLeaves < (*ppC1)->nLeaves ) return -1;
    if ( (*ppC0)->nLeaves > (*ppC1)->nLeaves ) return  1;
    return 0;
}
'''

if 'If_CutCompareByMLScore' not in src:
    src += ML_CMP
    print("ifCut.c: ML comparator appended  ✓")
else:
    print("ifCut.c: ML comparator already present — skipping")

with open(path, 'w') as f:
    f.write(src)
print("ifCut.c done.")
PYEOF
[[ $? -ne 0 ]] && { echo "ERROR: ifCut.c patch failed."; exit 1; }
echo ""

# ── Step 3: Patch ifMap.c ─────────────────────────────────────────────────────
echo "--- Patching ifMap.c ---"
python3 << 'PYEOF'
import sys, os

abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
path    = f"{abc_dir}/src/map/if/ifMap.c"

with open(path) as f:
    src = f.read()

changed = False

# ── 3a: Open dump file after the Mode assert inside If_ManPerformMappingRound
OPEN_ANCHOR = '    assert( Mode >= 0 && Mode <= 2 );\n'
OPEN_INJECT = (
    '#ifdef DUMP_CUTS\n'
    '    {\n'
    '        const char * pName = getenv("ABC_CIRCUIT_NAME");\n'
    '        If_DumpCutsOpen( pName ? pName : "circuit" );\n'
    '    }\n'
    '#endif /* DUMP_CUTS */\n'
)

if 'If_DumpCutsOpen' not in src:
    if OPEN_ANCHOR not in src:
        print("ERROR: 'assert( Mode >= 0 && Mode <= 2 )' not found in ifMap.c")
        sys.exit(1)
    src = src.replace(OPEN_ANCHOR, OPEN_ANCHOR + OPEN_INJECT, 1)
    changed = True
    print("ifMap.c: dump-open injected after Mode assert  ✓")
else:
    print("ifMap.c: dump-open already present — skipping")

# ── 3b: Per-node dump call inside If_ObjPerformMappingAnd, BEFORE
#        If_ManDerefNodeCutSet (cut set still valid here).
#
#   FIX-6: p->fNextRound is the actual field (set to 1 after round 0).
#           p->nRounds does not exist in If_Man_t.
#   FIX-7: a single injection here covers both the pManTim and else branches
#           in If_ManPerformMappingRound because both call
#           If_ObjPerformMappingAnd.
OLD_DEREF = (
    '    // free the cuts\n'
    '    If_ManDerefNodeCutSet( p, pObj );\n'
    '}'
)
NEW_DEREF = (
    '#ifdef DUMP_CUTS\n'
    '    /* fNextRound == 1 after round 0; Required times are valid then */\n'
    '    if ( p->fNextRound )\n'
    '        If_DumpNodeCuts( p, pObj );\n'
    '#endif /* DUMP_CUTS */\n'
    '    // free the cuts\n'
    '    If_ManDerefNodeCutSet( p, pObj );\n'
    '}'
)

if 'If_DumpNodeCuts' not in src:
    if OLD_DEREF not in src:
        print("ERROR: '// free the cuts' + If_ManDerefNodeCutSet anchor not found.")
        print("  Inspect ifMap.c: the comment may have been edited.")
        sys.exit(1)
    src = src.replace(OLD_DEREF, NEW_DEREF, 1)
    changed = True
    print("ifMap.c: per-node dump call injected inside If_ObjPerformMappingAnd  ✓")
else:
    print("ifMap.c: per-node dump call already present — skipping")

# ── 3c: Close dump file before If_ManPerformMappingRound returns.
#        Use rfind to target the LAST 'return 1;\n}' (end of that function).
CLOSE_OLD = '    return 1;\n}'
CLOSE_NEW = (
    '#ifdef DUMP_CUTS\n'
    '    If_DumpCutsClose();\n'
    '#endif /* DUMP_CUTS */\n'
    '    return 1;\n'
    '}'
)

if 'If_DumpCutsClose' not in src:
    idx = src.rfind(CLOSE_OLD)
    if idx < 0:
        print("ERROR: 'return 1;\\n}' not found at end of If_ManPerformMappingRound")
        sys.exit(1)
    src = src[:idx] + CLOSE_NEW + src[idx + len(CLOSE_OLD):]
    changed = True
    print("ifMap.c: dump-close injected before final return 1  ✓")
else:
    print("ifMap.c: dump-close already present — skipping")

if changed:
    with open(path, 'w') as f:
        f.write(src)
    print("ifMap.c patched.")
else:
    print("ifMap.c: nothing to change.")
PYEOF
[[ $? -ne 0 ]] && { echo "ERROR: ifMap.c patch failed."; exit 1; }
echo ""

# ── Step 4: Verify critical patch points before building ─────────────────────
echo "--- Pre-build verification ---"
python3 << 'PYEOF'
import sys, os, re

abc_dir = os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc')
errors  = []

# Check if.h
with open(f"{abc_dir}/src/map/if/if.h") as f:
    ifh = f.read()

if 'MLScore' not in ifh:
    errors.append("if.h: MLScore field NOT found — struct patch failed")
else:
    # Make sure it's inside the struct, not just in a comment elsewhere
    m = re.search(r'struct If_Cut_t_\s*\{([^}]*)\}', ifh, re.DOTALL)
    if m and 'MLScore' not in m.group(1):
        errors.append("if.h: MLScore found in file but NOT inside If_Cut_t struct")
    else:
        print("if.h: MLScore in If_Cut_t  ✓")

ns_pos   = ifh.find('ABC_NAMESPACE_HEADER_END')
dump_pos = ifh.find('If_DumpNodeCuts')
if dump_pos < 0:
    errors.append("if.h: If_DumpNodeCuts declaration missing")
elif dump_pos > ns_pos:
    errors.append("if.h: If_DumpNodeCuts declaration is AFTER ABC_NAMESPACE_HEADER_END (outside namespace)")
else:
    print("if.h: forward declarations correctly inside namespace  ✓")

# Check ifCut.c
with open(f"{abc_dir}/src/map/if/ifCut.c") as f:
    ifc = f.read()
for sym in ('If_DumpCutsOpen', 'If_DumpNodeCuts', 'If_CutCompareByMLScore'):
    if sym not in ifc:
        errors.append(f"ifCut.c: {sym} missing")
    else:
        print(f"ifCut.c: {sym}  ✓")

# Check ifMap.c
with open(f"{abc_dir}/src/map/if/ifMap.c") as f:
    ifm = f.read()
for sym in ('If_DumpCutsOpen', 'If_DumpNodeCuts', 'If_DumpCutsClose'):
    if sym not in ifm:
        errors.append(f"ifMap.c: {sym} missing")
    else:
        print(f"ifMap.c: {sym}  ✓")

if 'p->fNextRound' not in ifm:
    errors.append("ifMap.c: p->fNextRound not found (wrong field used?)")
if 'p->nRounds' in ifm:
    errors.append("ifMap.c: p->nRounds found — must be p->fNextRound")

if errors:
    print("")
    print("Pre-build verification FAILED:")
    for e in errors:
        print(f"  ✗ {e}")
    sys.exit(1)
print("")
print("All pre-build checks passed.")
PYEOF
[[ $? -ne 0 ]] && { echo "Aborting — fix the errors above before building."; exit 1; }
echo ""

# ── Step 5: Build abc_dump ────────────────────────────────────────────────────
echo "--- Building abc_dump (OPTFLAGS=-O2 -DDUMP_CUTS) ---"
cd "$ABC_DIR" || exit 1
NCPU=$(sysctl -n hw.ncpu 2>/dev/null || nproc 2>/dev/null || echo 4)

make clean > /dev/null 2>&1

# Run build; capture exit code correctly (avoid pipe masking it)
make -j"$NCPU" OPTFLAGS="-O2 -DDUMP_CUTS" ABC_USE_NO_READLINE=1 2>&1 \
  | grep -vE "^``|^clang: warning: overriding deployment"
BUILD_STATUS=${pipestatus[1]}   # zsh: pipestatus[1] is exit code of make

if (( BUILD_STATUS != 0 )); then
  echo ""
  echo "ERROR: abc_dump build failed.  For full error output run:"
  echo "  cd $ABC_DIR && make -j1 OPTFLAGS='-O2 -DDUMP_CUTS' ABC_USE_NO_READLINE=1 2>&1 | less"
  exit 1
fi

if [[ ! -f abc ]]; then
  echo "ERROR: make reported success but 'abc' binary not found."
  exit 1
fi
cp abc abc_dump
codesign --force --deep --sign - abc_dump
echo "Created: $ABC_DIR/abc_dump  ✓"

# ── Step 6: Build clean baseline ─────────────────────────────────────────────
echo ""
echo "--- Building clean baseline abc (no DUMP_CUTS) ---"
make clean > /dev/null 2>&1
make -j"$NCPU" OPTFLAGS="-O2" ABC_USE_NO_READLINE=1 2>&1 \
  | grep -vE "^``|^clang: warning: overriding deployment" \
  | tail -6
BASELINE_STATUS=${pipestatus[1]}

if (( BASELINE_STATUS != 0 )); then
  echo "ERROR: baseline build failed."
  exit 1
fi
echo "Created: $ABC_DIR/abc (clean baseline)  ✓"

echo ""
echo "========================================================"
echo " All patches applied and both binaries built."
echo " Next: ./04_generate_training_data.sh"
echo "========================================================"
