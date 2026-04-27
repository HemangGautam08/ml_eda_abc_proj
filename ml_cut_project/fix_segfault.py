#!/usr/bin/env python3
"""
fix_v3.py — Clean, no-crash fix for the ML pipeline.

Problems solved:
  1. Injection is inside If_ManPerformMappingRound (called N times per mapping),
     meaning subsequent rounds silently overwrite all ML choices. The outer
     function If_ManPerformMapping lives in a different ABC source file.
  2. If_CutCopy may not exist in this ABC version → segfault.
  3. subprocess.run needs stdin=DEVNULL when running ABC headlessly.
  4. ValueError in previous fix script (slice step=0 bug) — fixed.

Run from:  ~/Desktop/eda_proj/ml_cut_project/
"""

import os, re, shutil, subprocess, sys, glob

ABC_DIR = os.path.expanduser(
    os.environ.get('ABC_DIR', '/Users/hemanggautam/Desktop/eda_proj/abc'))
IF_DIR  = os.path.join(ABC_DIR, 'src/map/if')
SRC_DIR = os.path.join(ABC_DIR, 'src')

IFMAP_C = os.path.join(IF_DIR, 'ifMap.c')
IFML_C  = os.path.join(IF_DIR, 'ifML.c')

SEP = '─' * 70

def die(msg): print(f"\nFATAL: {msg}"); sys.exit(1)

# ─── STEP 1: Find where If_ManPerformMapping (the outer function) lives ───────
print(SEP); print("STEP 1 — Find If_ManPerformMapping outer function"); print(SEP)

if not os.path.isdir(SRC_DIR):
    die(f"ABC src not found at {SRC_DIR}")

# grep all .c files for the function definition (not just a call)
result = subprocess.run(
    ['grep', '-rn', '--include=*.c', 'If_ManPerformMapping[^R]', SRC_DIR],
    capture_output=True, text=True)

matches = result.stdout.splitlines()
print(f"  Matches for 'If_ManPerformMapping' (not ...Round):")
for m in matches:
    print(f"    {m}")

# Find the file that DEFINES it (has the function body, not just a call)
outer_file = None
outer_func_name = 'If_ManPerformMapping'
for line in matches:
    # Lines like:  /path/to/file.c:123:int If_ManPerformMapping(
    if re.search(r'(int|void)\s+If_ManPerformMapping\b', line):
        outer_file = line.split(':')[0]
        break

if outer_file:
    print(f"\n  Outer function defined in: {outer_file}  ✓")
else:
    # Fallback: look for any file that has both the function name and a `return 1;` nearby
    print("  Definition not found via int/void prefix — trying broader search...")
    for line in matches:
        fname = line.split(':')[0]
        if fname.endswith('.c') and fname != IFMAP_C:
            with open(fname) as f:
                content = f.read()
            if re.search(r'If_ManPerformMapping\s*\(', content) and \
               'If_ManPerformMappingRound' in content:
                outer_file = fname
                print(f"  Outer function likely in: {outer_file}")
                break

if not outer_file:
    print("""
  Could not auto-detect outer function file.
  Please run:  grep -rn "If_ManPerformMapping[^R]" ~/Desktop/eda_proj/abc/src/
  and look for the file that DEFINES (not just calls) If_ManPerformMapping.
  Common names: ifCore.c, ifMan.c, ifInt.c

  Then re-run this script with:
    OUTER_IF_FILE=/path/to/file.c python3 fix_v3.py
""")
    # Try env override
    env_file = os.environ.get('OUTER_IF_FILE', '')
    if env_file and os.path.isfile(env_file):
        outer_file = env_file
        print(f"  Using OUTER_IF_FILE={outer_file}")
    else:
        die("Set OUTER_IF_FILE env var and retry.")

# ─── STEP 2: Remove injection from If_ManPerformMappingRound (wrong place) ────
print(); print(SEP); print("STEP 2 — Remove injection from IfManPerformMappingRound"); print(SEP)

with open(IFMAP_C) as f:
    ifmap = f.read()

if 'If_ManMLPostProcess' in ifmap:
    # Back up first
    bak = IFMAP_C + '.bak_v3'
    if not os.path.isfile(bak):
        shutil.copy2(IFMAP_C, bak)
        print(f"  Backed up to {bak}")

    # Remove the block
    cleaned = re.sub(
        r'\n?#ifdef USE_ML\n\s*If_ManMLPostProcess\s*\(\s*p\s*\)\s*;\n#endif\s*/\*\s*USE_ML\s*\*/\n?',
        '\n', ifmap)
    with open(IFMAP_C, 'w') as f:
        f.write(cleaned)
    print("  Removed If_ManMLPostProcess from If_ManPerformMappingRound  ✓")
else:
    print("  No injection found in ifMap.c (already clean)  ✓")

# ─── STEP 3: Inject at end of If_ManPerformMapping (outer, correct location) ──
print(); print(SEP); print("STEP 3 — Inject into outer mapping function"); print(SEP)

with open(outer_file) as f:
    outer = f.read()

if 'If_ManMLPostProcess' in outer:
    print(f"  Injection already present in {outer_file}  ✓")
else:
    # Brace-match to find If_ManPerformMapping body boundaries
    fn_match = re.search(r'(int|void)\s+If_ManPerformMapping\s*\(', outer)
    if not fn_match:
        die(f"'If_ManPerformMapping' definition not found in {outer_file}.")

    fn_start = fn_match.start()
    brace_start = outer.index('{', fn_start)
    depth, pos, fn_end = 0, brace_start, -1
    while pos < len(outer):
        if outer[pos] == '{':   depth += 1
        elif outer[pos] == '}':
            depth -= 1
            if depth == 0:
                fn_end = pos
                break
        pos += 1

    if fn_end == -1:
        die("Could not brace-match If_ManPerformMapping body.")

    fn_body = outer[fn_start:fn_end]
    fn_end_line = outer[:fn_end].count('\n') + 1
    print(f"  If_ManPerformMapping body: lines "
          f"{outer[:fn_start].count(chr(10))+1}–{fn_end_line}")

    # Verify all If_ManPerformMappingRound calls are inside the body
    round_calls = [m.start() for m in re.finditer(r'If_ManPerformMappingRound', fn_body)]
    print(f"  Contains {len(round_calls)} If_ManPerformMappingRound call(s)  ✓")

    # Find last `return` inside the body
    returns = list(re.finditer(r'^([ \t]*)return\s+[01]\s*;', fn_body, re.MULTILINE))
    if not returns:
        # No explicit return — inject just before closing brace
        indent = '    '
        inject_at = fn_end
        ml_block = (f'#ifdef USE_ML\n'
                    f'{indent}If_ManMLPostProcess( p );\n'
                    f'#endif /* USE_ML */\n')
        outer = outer[:inject_at] + ml_block + outer[inject_at:]
        print(f"  Injected before closing brace (no explicit return found)")
    else:
        last_ret = returns[-1]
        indent = last_ret.group(1)
        inject_at = fn_start + last_ret.start()
        ml_block = (f'#ifdef USE_ML\n'
                    f'{indent}If_ManMLPostProcess( p );\n'
                    f'#endif /* USE_ML */\n'
                    f'{indent}')
        outer = outer[:inject_at] + ml_block + outer[inject_at:]
        ret_line = outer[:inject_at].count('\n') + 1
        print(f"  Injected before return at line {ret_line}")

    bak2 = outer_file + '.bak_v3'
    if not os.path.isfile(bak2):
        shutil.copy2(outer_file, bak2)
    with open(outer_file, 'w') as f:
        f.write(outer)
    print(f"  Written: {outer_file}  ✓")

    # Verify order
    with open(outer_file) as f:
        verify = f.read()
    round_pos = [m.start() for m in re.finditer(r'If_ManPerformMappingRound', verify)]
    post_pos  = [m.start() for m in re.finditer(r'If_ManMLPostProcess', verify)]
    if round_pos and post_pos and post_pos[-1] > round_pos[-1]:
        print("  Order verified: PostProcess is AFTER last Round call  ✓")
    else:
        print("  WARNING: order check inconclusive — verify manually")

# ─── STEP 4: Check If_CutCopy existence ───────────────────────────────────────
print(); print(SEP); print("STEP 4 — Check If_CutCopy in ABC source"); print(SEP)

r2 = subprocess.run(
    ['grep', '-rn', '--include=*.c', '--include=*.h', 'If_CutCopy', SRC_DIR],
    capture_output=True, text=True)

cutcopy_lines = [l for l in r2.stdout.splitlines() if 'ifML' not in l]
if cutcopy_lines:
    print("  If_CutCopy found (excluding ifML.c):")
    for l in cutcopy_lines[:6]:
        print(f"    {l}")
    if_cutcopy_ok = True
else:
    print("  If_CutCopy NOT found in ABC source — safe fallback will be used.")
    if_cutcopy_ok = False

# ─── STEP 5: Write fixed ifML.c ───────────────────────────────────────────────
print(); print(SEP); print("STEP 5 — Write fixed ifML.c"); print(SEP)

# Check which If_Cut_t fields actually exist
IFH = os.path.join(IF_DIR, 'if.h')
cut_fields = []
if os.path.isfile(IFH):
    with open(IFH) as f:
        ifh_src = f.read()
    m = re.search(r'struct\s+\w*\s*If_Cut_t_\s*\{(.+?)\}', ifh_src, re.DOTALL)
    if m:
        cut_fields = re.findall(r'\b(\w+)\s*;', m.group(1))
        print(f"  If_Cut_t fields: {cut_fields}")

# Build the copy code based on actual fields
ALWAYS_FIELDS = ['Delay', 'Area', 'nLeaves', 'nLimit', 'uSign']
OPTIONAL_FIELDS = ['fCompl', 'fUser', 'fUseless', 'fMark0', 'fMark1']
copy_lines = []
for f in ALWAYS_FIELDS:
    copy_lines.append(f'    pDst->{f} = pSrc->{f};')
for f in OPTIONAL_FIELDS:
    if not cut_fields or f in cut_fields:
        copy_lines.append(f'    pDst->{f} = pSrc->{f};  /* if present */')
copy_block = '\n'.join(copy_lines)

ifml_src = f'''/**
 * ifML.c — Inline MLP inference for ML-guided cut selection in ABC.
 *
 * Place at:  abc/src/map/if/ifML.c
 * Compile:   make -j$(nproc) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
 *
 * Architecture: 9 -> ReLU -> 64 -> ReLU -> 32 -> sigmoid(1)
 *
 * FIX: If_CutCopy replaced with If_CutCopySafe (field-by-field + memcpy)
 *      because If_CutCopy may not exist, and struct assignment of a type with
 *      a flexible array member is undefined behaviour.
 */

#include "if.h"
#include "model_weights.h"
#include <math.h>
#include <string.h>
#include <stdlib.h>

#define ML_MIN_MARGIN  0.05f
#define ML_MAX_LEAVES  16

static float ml_relu(float x) {{ return x > 0.0f ? x : 0.0f; }}
static float ml_sigmoid(float x)
{{
    if (x >= 0.0f) return 1.0f / (1.0f + expf(-x));
    float e = expf(x); return e / (1.0f + e);
}}

static void ml_linear(const float *x, int in_dim,
                      const float *W, const float *b,
                      float *out,     int out_dim)
{{
    int i, j;
    for (i = 0; i < out_dim; i++) {{
        float s = b[i];
        for (j = 0; j < in_dim; j++) s += W[i * in_dim + j] * x[j];
        out[i] = s;
    }}
}}

static float If_MLScoreCut(float area_flow, float cut_delay,
                            int n_leaves, int node_level,
                            float required_time, int node_fanout)
{{
    float input[ML_INPUT_DIM], h1[ML_HIDDEN1], h2[ML_HIDDEN2], out1[1];
    int   i;
    const float eps    = 1e-6f;
    const float fanout = (float)(node_fanout < 1 ? 1 : node_fanout);

    if (required_time >  1e7f || required_time < -1e7f)
        required_time = cut_delay + 100.0f;
    float slack = required_time - cut_delay;
    if (slack >  1e7f) slack =  100.0f;
    if (slack < -1e7f) slack = -100.0f;

    const float req_abs = fabsf(required_time) + eps;
    int mffc_size = (int)(area_flow + 0.5f);
    if (mffc_size < n_leaves) mffc_size = n_leaves;
    if (mffc_size > 1000)     mffc_size = 1000;
    const float mffc_f = (float)mffc_size;

    i = 0;
    input[i++] = (float)n_leaves;
    input[i++] = (float)node_level;
    input[i++] = fanout;
    input[i++] = (slack <= 0.0f) ? 1.0f : 0.0f;
    input[i++] = slack / req_abs;
    input[i++] = area_flow / (sqrtf(fanout) + eps);
    input[i++] = area_flow / ((float)n_leaves + eps);
    input[i++] = mffc_f;
    input[i++] = mffc_f / ((float)n_leaves + eps);

    for (i = 0; i < ML_INPUT_DIM; i++)
        input[i] = (input[i] - scaler_mean[i]) / (scaler_scale[i] + 1e-8f);

    ml_linear(input, ML_INPUT_DIM, L0_weight, L0_bias, h1, ML_HIDDEN1);
    for (i = 0; i < ML_HIDDEN1; i++) h1[i] = ml_relu(h1[i]);
    ml_linear(h1, ML_HIDDEN1, L1_weight, L1_bias, h2, ML_HIDDEN2);
    for (i = 0; i < ML_HIDDEN2; i++) h2[i] = ml_relu(h2[i]);
    ml_linear(h2, ML_HIDDEN2, L2_weight, L2_bias, out1, 1);
    return ml_sigmoid(out1[0]);
}}

/* Safe cut copy: copies fixed header fields then memcpy's the leaf array.
 * Never uses struct assignment on a flexible-array-member type. */
static void If_CutCopySafe(If_Cut_t *pDst, const If_Cut_t *pSrc)
{{
    int nL;
    if (!pDst || !pSrc) return;
    nL = (int)pSrc->nLeaves;
    if (nL < 0 || nL > ML_MAX_LEAVES) return;
{copy_block}
    if (nL > 0)
        memcpy(pDst->pLeaves, pSrc->pLeaves, sizeof(int) * (unsigned)nL);
}}

void If_ObjOverrideCutWithML(If_Man_t *p, If_Obj_t *pObj)
{{
    If_Cut_t *pCut, *pCutBestML = NULL;
    float     bestScore = -1.0f, abcScore = -1.0f;
    int       k = 0, nL;
    (void)p;

    if (If_ObjIsCi(pObj) || If_ObjIsConst1(pObj)) return;
    if (!pObj->pCutSet || pObj->pCutSet->nCuts <= 0) return;

    If_Cut_t *pBest    = If_ObjCutBest(pObj);
    float abcDelay     = pBest->Delay;
    float abcArea      = pBest->Area;
    int   abcLeaves    = (int)pBest->nLeaves;
    float slack        = pObj->Required - abcDelay;

    If_ObjForEachCut(pObj, pCut, k)
    {{
        if (slack < 0.5f && pCut->Delay > abcDelay + 1e-6f)
            continue;

        float score = If_MLScoreCut(
            pCut->Area, pCut->Delay, (int)pCut->nLeaves,
            (int)pObj->Level, pObj->Required, (int)pObj->nRefs);

        if (score > bestScore) {{ bestScore = score; pCutBestML = pCut; }}
        if (fabsf(pCut->Delay - abcDelay) < 1e-9f &&
            fabsf(pCut->Area  - abcArea)  < 1e-9f &&
            (int)pCut->nLeaves == abcLeaves)
            abcScore = score;
    }}

    if (!pCutBestML)                          return;
    if (bestScore - abcScore < ML_MIN_MARGIN) return;
    nL = (int)pCutBestML->nLeaves;
    if (nL < 1 || nL > ML_MAX_LEAVES)        return;

    If_CutCopySafe(If_ObjCutBest(pObj), pCutBestML);
}}

void If_ManMLPostProcess(If_Man_t *p)
{{
    If_Obj_t *pObj;
    int i;
    If_ManForEachNode(p, pObj, i)
        If_ObjOverrideCutWithML(p, pObj);
}}

void If_ManMLScoreAllCuts(If_Man_t *p) {{ (void)p; }}
'''

os.makedirs('abc_patch', exist_ok=True)
with open('abc_patch/ifML.c', 'w') as f:
    f.write(ifml_src)
shutil.copy2('abc_patch/ifML.c', IFML_C)
print(f"  Written abc_patch/ifML.c and {IFML_C}  ✓")

# ─── STEP 6: Fix 08_compare_qor.py ───────────────────────────────────────────
print(); print(SEP); print("STEP 6 — Fix 08_compare_qor.py (stdin=DEVNULL)"); print(SEP)
QOR = '08_compare_qor.py'
if os.path.isfile(QOR):
    with open(QOR) as f: q = f.read()
    old = 'r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)'
    new = ('r = subprocess.run(cmd, capture_output=True, text=True,\n'
           '                          stdin=subprocess.DEVNULL, timeout=300)')
    if 'DEVNULL' in q:
        print(f"  Already patched  ✓")
    elif old in q:
        with open(QOR, 'w') as f: f.write(q.replace(old, new))
        print(f"  Added stdin=DEVNULL  ✓")
    else:
        print(f"  Pattern not found — add stdin=subprocess.DEVNULL to the "
              "subprocess.run call in run_abc() manually.")

# ─── STEP 7: Build ────────────────────────────────────────────────────────────
print(); print(SEP); print("STEP 7 — Build"); print(SEP)
NCPU = os.cpu_count() or 4

def build(flags, dest_name):
    print(f"  Building {dest_name}  (flags: {flags})")
    subprocess.run('make clean', shell=True, cwd=ABC_DIR,
                   capture_output=True)
    r = subprocess.run(
        f'make -j{NCPU} OPTFLAGS="{flags}" ABC_USE_NO_READLINE=1 2>&1 | tail -8',
        shell=True, cwd=ABC_DIR, capture_output=True, text=True)
    print(r.stdout)
    ok = os.path.isfile(os.path.join(ABC_DIR, 'abc'))
    if not ok:
        print(f"  BUILD FAILED — check output above"); return False
    if dest_name != 'abc':
        shutil.copy2(os.path.join(ABC_DIR, 'abc'),
                     os.path.join(ABC_DIR, dest_name))
    subprocess.run(f'codesign --sign - --force {dest_name}',
                   shell=True, cwd=ABC_DIR, capture_output=True)
    print(f"  {dest_name}: signed ✓")
    return True

ok1 = build('-O2 -DUSE_ML', 'abc_ml')
ok2 = build('-O2', 'abc')
if not (ok1 and ok2): sys.exit(1)

# ─── STEP 8: Smoke test ───────────────────────────────────────────────────────
print(); print(SEP); print("STEP 8 — Smoke test"); print(SEP)
bench_files = glob.glob(os.path.expanduser(
    os.environ.get('BENCH_DIR','~/Desktop/eda_proj/benchmarks')) + '/**/*.aig',
    recursive=True)
bench = min(bench_files, key=os.path.getsize) if bench_files else None
if not bench:
    print("  No benchmark found — skipping smoke test")
else:
    for label, binary in [('abc (baseline)', 'abc'), ('abc_ml', 'abc_ml')]:
        bpath = os.path.join(ABC_DIR, binary)
        cmd = [bpath, '-c',
               f'read_aiger {bench}; strash; if -K 6 -C 8; print_stats;']
        r = subprocess.run(cmd, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60)
        out = r.stdout + r.stderr
        nd  = re.search(r'\bnd\s*=\s*(\d+)', out)
        lev = re.search(r'\blev\s*=\s*(\d+)', out)
        if r.returncode == 0 and nd and lev:
            print(f"  {label}: luts={nd.group(1)}  levels={lev.group(1)}  ✓")
        else:
            print(f"  {label}: FAILED  rc={r.returncode}")
            if r.stdout: print(f"    stdout: {r.stdout[:400]}")
            if r.stderr: print(f"    stderr: {r.stderr[:400]}")

print()
print(SEP)
print("Done.  If smoke test passed:  python3 08_compare_qor.py")
print(SEP)