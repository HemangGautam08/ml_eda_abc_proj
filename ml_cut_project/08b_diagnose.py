#!/usr/bin/env python3
"""
08b_diagnose.py
================
Step-by-step diagnosis for why abc_ml == abc (all-zeros QoR).
Run this FIRST. It tells you exactly which of the 3 causes applies.

Usage:  python3 08b_diagnose.py
"""

import os, sys, subprocess, re, hashlib, glob, struct
import numpy as np

ABC_DIR     = os.path.expanduser(os.environ.get('ABC_DIR',
              '/Users/hemanggautam/Desktop/eda_proj/abc'))
ABC_BIN     = os.path.join(ABC_DIR, 'abc')
ABC_ML_BIN  = os.path.join(ABC_DIR, 'abc_ml')
BENCH_DIR   = os.path.expanduser(os.environ.get('BENCH_DIR',
              '/Users/hemanggautam/Desktop/eda_proj/benchmarks'))
IF_DIR      = os.path.join(ABC_DIR, 'src/map/if')

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
WARN = "\033[93m⚠\033[0m"

def md5(path):
    h = hashlib.md5()
    with open(path, 'rb') as f:
        h.update(f.read())
    return h.hexdigest()

def section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)

# ─────────────────────────────────────────────────────────────
# CHECK 1: Are the binaries actually different?
# ─────────────────────────────────────────────────────────────
section("CHECK 1: Binary identity")

if not os.path.isfile(ABC_BIN):
    print(f"  {FAIL} abc not found at {ABC_BIN}")
    sys.exit(1)
if not os.path.isfile(ABC_ML_BIN):
    print(f"  {FAIL} abc_ml not found at {ABC_ML_BIN}")
    print("       Run: ./07_install_ml_into_abc.sh")
    sys.exit(1)

h_abc  = md5(ABC_BIN)
h_ml   = md5(ABC_ML_BIN)
sz_abc = os.path.getsize(ABC_BIN)
sz_ml  = os.path.getsize(ABC_ML_BIN)

print(f"  abc    : {sz_abc:,} bytes  md5={h_abc[:12]}")
print(f"  abc_ml : {sz_ml:,} bytes  md5={h_ml[:12]}")

if h_abc == h_ml:
    print(f"  {FAIL}  BINARIES ARE IDENTICAL — abc_ml was NOT rebuilt with USE_ML.")
    print("       Fix: cd $ABC_DIR && make clean")
    print("            make -j$(nproc) OPTFLAGS='-O2 -DUSE_ML' ABC_USE_NO_READLINE=1")
    print("            cp abc abc_ml")
    sys.exit(1)
else:
    print(f"  {PASS}  Binaries differ (sizes differ by {abs(sz_ml-sz_abc):,} bytes)")

# ─────────────────────────────────────────────────────────────
# CHECK 2: Does abc_ml binary contain USE_ML symbol strings?
# ─────────────────────────────────────────────────────────────
section("CHECK 2: USE_ML symbols in abc_ml binary")

def grep_binary(path, needle):
    """Search for ASCII string in binary."""
    needle_b = needle.encode()
    with open(path, 'rb') as f:
        data = f.read()
    return needle_b in data

markers = [
    ("If_ManMLPostProcess", "ML post-process function"),
    ("ML_INPUT_DIM",        "weight header macro (may be optimised away)"),
    ("USE_ML",              "compile flag string (may not appear)"),
]

all_ok = True
for sym, desc in markers:
    found = grep_binary(ABC_ML_BIN, sym)
    status = PASS if found else WARN
    print(f"  {status}  '{sym}' ({desc}): {'found' if found else 'NOT found'}")
    if sym == "If_ManMLPostProcess" and not found:
        all_ok = False

if not all_ok:
    print(f"\n  {FAIL}  If_ManMLPostProcess not in binary!")
    print("       ifML.c was NOT linked. Check module.make or CMakeLists.")
    sys.exit(1)

# ─────────────────────────────────────────────────────────────
# CHECK 3: Does If_ManMLPostProcess fire AFTER If_ManDeriveMapping?
# ─────────────────────────────────────────────────────────────
section("CHECK 3: Injection order in ifMap.c")

ifmap_path = os.path.join(IF_DIR, 'ifMap.c')
if not os.path.isfile(ifmap_path):
    print(f"  {WARN}  ifMap.c not found at {ifmap_path} — skipping")
else:
    with open(ifmap_path) as f:
        src = f.read()

    ml_positions     = [m.start() for m in re.finditer(r'If_ManMLPostProcess', src)]
    derive_positions = [m.start() for m in re.finditer(r'If_ManDeriveMapping', src)]
    round_positions  = [m.start() for m in re.finditer(r'If_ManPerformMappingRound', src)]

    print(f"  If_ManMLPostProcess    occurrences : {len(ml_positions)}")
    print(f"  If_ManDeriveMapping    occurrences : {len(derive_positions)}")
    print(f"  If_ManPerformMappingRound occurrences: {len(round_positions)}")

    if not ml_positions:
        print(f"  {FAIL}  If_ManMLPostProcess NOT in ifMap.c! Injection failed.")
        sys.exit(1)

    # Check: ML must come AFTER last round but BEFORE If_ManDeriveMapping
    # (or If_ManDeriveMapping is in the caller, not here)
    if derive_positions:
        last_derive = max(derive_positions)
        last_ml     = max(ml_positions)
        if last_ml > last_derive:
            print(f"  {FAIL}  CRITICAL: If_ManMLPostProcess (pos {last_ml}) is AFTER")
            print(f"           If_ManDeriveMapping (pos {last_derive}).")
            print("           The mapping netlist is committed before ML runs!")
            print("           Fix: move If_ManMLPostProcess to just before If_ManDeriveMapping.")
            print()
            # Show the lines
            lines = src.split('\n')
            for i, line in enumerate(lines):
                if 'If_ManDeriveMapping' in line or 'If_ManMLPostProcess' in line:
                    print(f"    line {i+1:4d}: {line.rstrip()}")
        else:
            print(f"  {PASS}  ML post-process (pos {last_ml}) is BEFORE")
            print(f"           If_ManDeriveMapping (pos {last_derive}) — ordering OK")
    else:
        print(f"  {PASS}  If_ManDeriveMapping not in ifMap.c")
        print("           (called from caller — ML fires before it, ordering OK)")

    if round_positions and ml_positions:
        last_round = max(round_positions)
        last_ml    = max(ml_positions)
        if last_ml > last_round:
            print(f"  {PASS}  ML fires after last mapping round — timing is converged")
        else:
            print(f"  {WARN}  ML fires BEFORE some mapping rounds — may not see converged timing")

# ─────────────────────────────────────────────────────────────
# CHECK 4: Run one benchmark and count how many cuts ML overrides
# ─────────────────────────────────────────────────────────────
section("CHECK 4: Runtime override count (needs verbose ifML.c)")

benches = []
for sub in ['arithmetic', 'random_control']:
    benches += glob.glob(os.path.join(BENCH_DIR, sub, '*.aig'))
benches.sort()

if not benches:
    print(f"  {WARN}  No .aig files found — skipping runtime check")
else:
    bench = benches[0]
    bname = os.path.basename(bench)
    print(f"  Testing on: {bname}")
    cmd = [ABC_ML_BIN, '-c',
           f'read_aiger {bench}; strash; if -K 6 -C 8; print_stats;']
    result = subprocess.run(cmd, capture_output=True, text=True,
                            stdin=subprocess.DEVNULL, timeout=60)
    out = result.stdout + result.stderr

    # Look for ML override count lines (if ifML.c has fprintf(stderr,...))
    ml_lines = [l for l in out.split('\n')
                if any(k in l.lower() for k in ['ml override', 'ml:', '[ml]',
                                                  'override', 'mlpostprocess'])]
    if ml_lines:
        print(f"  {PASS}  ML output found:")
        for l in ml_lines[:10]:
            print(f"    {l}")
    else:
        print(f"  {WARN}  No ML diagnostic output found in stderr/stdout.")
        print("       Add fprintf(stderr, ...) to If_ManMLPostProcess in ifML.c")
        print("       to count how many cuts are actually being overridden.")
        print()
        print("  Suggested addition to If_ManMLPostProcess in ifML.c:")
        print("""
    int n_overridden = 0, n_nodes = 0;
    // ... your existing loop ...
    // inside the loop, when you change pObj->pCutBest:
    //   n_overridden++;
    // n_nodes++
    fprintf(stderr, "[ML] PostProcess: %d/%d nodes overridden\\n",
            n_overridden, n_nodes);
""")

# ─────────────────────────────────────────────────────────────
# CHECK 5: Model quality on training data
# ─────────────────────────────────────────────────────────────
section("CHECK 5: Model agreement with ABC on training data")

npz_path = "ml/train_data.npz"
pt_path  = "ml/cut_model_mlp.pt"

if not os.path.isfile(npz_path):
    print(f"  {WARN}  {npz_path} not found — run 05a_preprocess_mac.py first")
elif not os.path.isfile(pt_path):
    print(f"  {WARN}  {pt_path} not found — train model in Colab first")
else:
    try:
        import torch
        data = np.load(npz_path, allow_pickle=True)
        X        = data['X_scaled']
        is_best  = data['is_best']
        circuits = data['circuits']

        ckpt  = torch.load(pt_path, map_location='cpu')
        state = ckpt['model_state']
        n_feat   = ckpt['n_features']
        hidden   = ckpt['hidden_sizes']  # [64, 32]

        # Forward pass (ReLU MLP)
        def relu(x): return np.maximum(0, x)
        def forward(X):
            W0,b0 = state['fc0.weight'].numpy(), state['fc0.bias'].numpy()
            W1,b1 = state['fc1.weight'].numpy(), state['fc1.bias'].numpy()
            W2,b2 = state['fc2.weight'].numpy(), state['fc2.bias'].numpy()
            h = relu(X @ W0.T + b0)
            h = relu(h @ W1.T + b1)
            return (h @ W2.T + b2).squeeze(-1)

        scores = forward(X.astype(np.float32))

        # For each node group, check if ML top-1 == ABC top-1
        from collections import defaultdict
        import pandas as pd

        # Reconstruct node groups from pair data
        # Use is_best column directly: node groups where is_best==1 is ABC's choice
        # We need node_id groupings — approximate via is_best alignment
        pair_b = data['pair_better']
        pair_w = data['pair_worse']

        # Check: does ML rank "better" cuts higher than "worse" cuts?
        s_better = scores[pair_b]
        s_worse  = scores[pair_w]
        pairwise_acc = (s_better > s_worse).mean()

        print(f"  Pairwise ranking accuracy : {pairwise_acc:.1%}")
        print(f"    (% of pairs where ML scores the 'better' cut higher)")
        print(f"    50% = random, 100% = perfect ranking")

        if pairwise_acc < 0.55:
            print(f"  {FAIL}  Near-random ranking — model did not learn.")
            print("       Re-train with more epochs or check training loss.")
        elif pairwise_acc < 0.70:
            print(f"  {WARN}  Weak ranking — model learned something but not strongly.")
        else:
            print(f"  {PASS}  Good ranking accuracy.")

        # ABC agreement: does the cut ABC picked get the highest ML score?
        # Group by circuit+node using pair indices
        abc_top1_rows = np.where(is_best == 1)[0]
        n_nodes_checked = len(abc_top1_rows)
        ml_agrees = 0

        # For each ABC-best cut, check if ML score > all scores at same node
        # Use pair_better/worse as proxy: if abc_best==pair_better, ML agrees
        abc_set = set(abc_top1_rows)
        pair_b_set = set(pair_b)
        overlap = len(abc_set & pair_b_set)
        agree_pct = overlap / max(len(abc_top1_rows), 1)

        print(f"\n  ABC cut overlap with ML 'better' set : {agree_pct:.1%}")
        print(f"    (how often ABC's choice is also ML's 'better' cut in pairs)")
        print(f"    High overlap → model mimics ABC → no QoR improvement possible")

        score_abc_best  = scores[abc_top1_rows].mean()
        score_abc_worst = scores[np.where(is_best == 0)[0]].mean()
        print(f"\n  Mean ML score — ABC-best cuts  : {score_abc_best:+.4f}")
        print(f"  Mean ML score — ABC-other cuts : {score_abc_worst:+.4f}")
        if score_abc_best > score_abc_worst:
            print(f"  {PASS}  ML assigns higher scores to ABC-best cuts (expected)")
        else:
            print(f"  {WARN}  ML assigns LOWER scores to ABC-best cuts — model inverted?")

    except Exception as e:
        print(f"  {WARN}  Could not run model check: {e}")

# ─────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────
section("DIAGNOSIS SUMMARY")
print("""
  Most likely root causes (check in order):

  1. If_ManDeriveMapping called BEFORE If_ManMLPostProcess
     → Fix: In ifMap.c, move ML call to just before If_ManDeriveMapping

  2. ifML.c compiled but If_ManMLPostProcess does nothing visible
     → Add fprintf(stderr, "[ML] overrode %d cuts\\n", count)
     → Rebuild and re-run — if still 0 overrides, pCutBest is not being read

  3. Model learned ABC's heuristic (pairwise acc near 50-60%)
     → Use a different label (e.g. pure mffc_size, or area_flow rank)
     → Retrain with more diverse circuits

  4. abc_ml is identical binary to abc (check 1 catches this)

  Run:  python3 08c_show_model_metrics.py
  to see model predictions independently of ABC's C code.
""")
