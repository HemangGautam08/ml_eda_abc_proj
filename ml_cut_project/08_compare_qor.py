#!/usr/bin/env python3
"""
08_compare_qor.py
Task 3: Compare area-delay QoR between baseline ABC and ML-guided ABC.
Improvements:
  - Geometric mean improvement (standard in EDA benchmarking)
  - Area-Delay Product (ADP) as primary QoR metric
  - Speedup column (wall-clock time ratio)
  - Handles missing binaries gracefully
  - Saves both CSV and a nicely formatted text table
"""

import subprocess, re, os, sys, time, glob
import pandas as pd
import numpy as np

ABC_DEFAULT = os.path.expanduser(
    os.environ.get('ABC_DEFAULT', '/Users/hemanggautam/Desktop/eda_proj/abc/abc'))
ABC_ML      = os.path.expanduser(
    os.environ.get('ABC_ML',      '/Users/hemanggautam/Desktop/eda_proj/abc/abc_ml'))
BENCH_DIR   = os.path.expanduser(
    os.environ.get('BENCH_DIR',   '/Users/hemanggautam/Desktop/eda_proj/benchmarks'))
K           = 6
C_DEFAULT   = 8     # Standard ABC: C=8
C_ML        = 8     # ML-ABC uses same C but better cut selection

os.makedirs("results", exist_ok=True)

# ── Find benchmarks ───────────────────────────────────────────────────────────
benchmarks = []
for subdir in ['arithmetic', 'random_control']:
    d = os.path.join(BENCH_DIR, subdir)
    if os.path.isdir(d):
        benchmarks += sorted(glob.glob(os.path.join(d, '*.aig')))

if not benchmarks:
    print(f"ERROR: No .aig files found under {BENCH_DIR}")
    sys.exit(1)
print(f"Found {len(benchmarks)} benchmarks\n")

# Warn if ML binary not built
if not os.path.isfile(ABC_ML):
    print(f"WARNING: {ABC_ML} not found — ML column will be empty.")
    print("Run 07_install_ml_into_abc.sh first.\n")

# ── ABC runner ────────────────────────────────────────────────────────────────
def run_abc(abc_bin, bench_path, K=6, C=8):
    """Return (luts, levels, cpu_sec) or (None, None, None) on error."""
    if not os.path.isfile(abc_bin):
        return None, None, None
    cmd = [abc_bin, '-c',
           f'read_aiger {bench_path}; strash; if -K {K} -C {C}; print_stats;']
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=300)
    except subprocess.TimeoutExpired:
        return None, None, None
    elapsed = round(time.perf_counter() - t0, 4)
    out = r.stdout + r.stderr
    lm = re.search(r'\bnd\s*=\s*(\d+)',  out)
    vm = re.search(r'\blev\s*=\s*(\d+)', out)
    return (int(lm.group(1)) if lm else None,
            int(vm.group(1)) if vm else None,
            elapsed)

# ── Main loop ─────────────────────────────────────────────────────────────────
rows = []
for bench in benchmarks:
    name     = os.path.splitext(os.path.basename(bench))[0]
    category = os.path.basename(os.path.dirname(bench))

    luts_b,  lev_b,  t_b  = run_abc(ABC_DEFAULT, bench, K, C_DEFAULT)
    luts_ml, lev_ml, t_ml = run_abc(ABC_ML,      bench, K, C_ML)

    row = dict(benchmark=name, category=category,
               abc_luts=luts_b,  abc_levels=lev_b,  abc_time=t_b,
               ml_luts=luts_ml, ml_levels=lev_ml, ml_time=t_ml)

    if all(v is not None for v in [luts_b, lev_b, luts_ml, lev_ml]):
        adp_b    = luts_b  * lev_b
        adp_ml   = luts_ml * lev_ml
        # positive = ML is better (smaller)
        row['luts_imp%']   = round(100 * (luts_b  - luts_ml)  / (luts_b  + 1e-9), 2)
        row['levels_imp%'] = round(100 * (lev_b   - lev_ml)   / (lev_b   + 1e-9), 2)
        row['adp_abc']     = adp_b
        row['adp_ml']      = adp_ml
        row['adp_imp%']    = round(100 * (adp_b   - adp_ml)   / (adp_b   + 1e-9), 2)
        row['speedup']     = round(t_b / t_ml, 3) if t_ml else None
    else:
        row.update({'luts_imp%': None, 'levels_imp%': None,
                    'adp_abc': None, 'adp_ml': None,
                    'adp_imp%': None, 'speedup': None})

    print(f"[{category}] {name}")
    print(f"  ABC    luts={luts_b}  levels={lev_b}  time={t_b}s")
    print(f"  ML     luts={luts_ml}  levels={lev_ml}  time={t_ml}s")
    if 'adp_imp%' in row and row['adp_imp%'] is not None:
        print(f"  ADP improvement: {row['adp_imp%']:+.2f}%  "
              f"(LUTs: {row['luts_imp%']:+.2f}%  Levels: {row['levels_imp%']:+.2f}%)")
    rows.append(row)

df = pd.DataFrame(rows)
df.to_csv("results/qor_comparison.csv", index=False)

# ── Summary stats ──────────────────────────────────────────────────────────────
df_v = df.dropna(subset=['adp_imp%'])

print("\n" + "="*95)
print("TASK 3 — QoR Comparison: Baseline ABC vs ML-Guided ABC")
print("="*95)

cols = ['benchmark', 'abc_luts', 'ml_luts', 'luts_imp%',
        'abc_levels', 'ml_levels', 'levels_imp%',
        'adp_abc', 'adp_ml', 'adp_imp%', 'speedup']
print(df[cols].to_string(index=False))

if len(df_v) > 0:
    print("\n--- Summary Statistics ---")
    for col, label in [('luts_imp%',   'LUT reduction  '),
                        ('levels_imp%', 'Level reduction'),
                        ('adp_imp%',    'ADP reduction  '),
                        ('speedup',     'Speedup        ')]:
        vals = df_v[col].dropna()
        if len(vals) == 0:
            continue
        # Geometric mean of ratios for EDA-standard reporting
        ratios = 1 + vals / 100
        geo    = float(np.exp(np.log(ratios.clip(1e-6)).mean()))
        print(f"  {label}: arith_mean={vals.mean():+6.2f}%  "
              f"geo_mean={(geo-1)*100:+6.2f}%  "
              f"min={vals.min():+6.2f}%  max={vals.max():+6.2f}%")

# ── Write text report ─────────────────────────────────────────────────────────
report_path = "results/qor_comparison.txt"
with open(report_path, "w") as f:
    f.write("Task 3 — QoR Comparison: Baseline ABC vs ML-Guided ABC\n")
    f.write("=" * 95 + "\n")
    f.write(f"ABC binary : {ABC_DEFAULT}\n")
    f.write(f"ML  binary : {ABC_ML}\n")
    f.write(f"K={K}  C_default={C_DEFAULT}  C_ML={C_ML}\n\n")
    f.write(df[cols].to_string(index=False))
    f.write("\n\nSummary:\n")
    if len(df_v) > 0:
        for col, label in [('luts_imp%','LUT'), ('levels_imp%','Levels'), ('adp_imp%','ADP')]:
            vals = df_v[col].dropna()
            if len(vals):
                f.write(f"  {label:8s}: mean={vals.mean():+.2f}%  min={vals.min():+.2f}%  max={vals.max():+.2f}%\n")

print(f"\nSaved: results/qor_comparison.csv")
print(f"Saved: {report_path}")
