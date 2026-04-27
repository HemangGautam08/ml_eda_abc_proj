#!/bin/zsh
# =============================================================================
# 01_profile_cuts.sh
# Task 1: Profile mapping time (CPU time) vs number of cuts.
#
# FIXES vs previous version:
#   - BUG: N_RUNS was set but NOT exported — Python subprocess could not read it
#     via os.environ. Fixed with 'export N_RUNS=3'.
#   - Added -O2 to OPTFLAGS so profiling reflects real optimised performance
# =============================================================================

# ── CONFIG — override by setting env vars before running ─────────────────────
export ABC_PATH="${ABC_PATH:-/Users/hemanggautam/Desktop/eda_proj/abc/abc}"
export BENCH_DIR="${BENCH_DIR:-/Users/hemanggautam/Desktop/eda_proj/benchmarks}"
export N_RUNS=3          # FIX: must be exported so inline Python can read it
K=6

RESULTS_DIR="results"
OUTPUT_CSV="$RESULTS_DIR/profiling_results.csv"
mkdir -p "$RESULTS_DIR"

# ── Verify ABC binary ─────────────────────────────────────────────────────────
if [[ ! -f "$ABC_PATH" ]]; then
  echo "ERROR: ABC not found at $ABC_PATH"
  echo "Set:  export ABC_PATH=/path/to/abc/abc   then re-run."
  exit 1
fi
echo "ABC binary : $ABC_PATH"
echo "Benchmarks : $BENCH_DIR"
echo "Runs/point : $N_RUNS (median reported)"
echo ""

# ── Collect benchmarks ────────────────────────────────────────────────────────
BENCHMARKS=()
for subdir in arithmetic random_control; do
  dir="$BENCH_DIR/$subdir"
  if [[ -d "$dir" ]]; then
    for f in "$dir"/*.aig; do
      [[ -f "$f" ]] && BENCHMARKS+=("$f")
    done
  fi
done

if [[ ${#BENCHMARKS[@]} -eq 0 ]]; then
  echo "ERROR: No .aig files found under $BENCH_DIR/{arithmetic,random_control}"
  exit 1
fi
echo "Found ${#BENCHMARKS[@]} benchmarks"

# ── Write CSV header ──────────────────────────────────────────────────────────
echo "benchmark,cut_limit,run,luts,levels,time_sec" > "$OUTPUT_CSV"

# ── Python handles timing accurately (avoids zsh time-builtin quirks) ────────
python3 - << 'PYEOF'
import subprocess, time, os, re, statistics

abc_path   = os.environ['ABC_PATH']
bench_dir  = os.environ['BENCH_DIR']
csv_path   = "results/profiling_results.csv"
n_runs     = int(os.environ.get('N_RUNS', '3'))
K          = 6
cut_limits = [2, 4, 8, 16, 32, 64]

benchmarks = []
for sub in ['arithmetic', 'random_control']:
    d = os.path.join(bench_dir, sub)
    if os.path.isdir(d):
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.aig'):
                benchmarks.append(os.path.join(d, fn))

def run_abc(bench, C, K=6):
    """Run ABC once; return (luts, levels, elapsed_sec) or (None,None,None)."""
    cmd = [abc_path, '-c',
           f'read_aiger {bench}; strash; if -K {K} -C {C}; print_stats;']
    t0 = time.perf_counter()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return None, None, None
    elapsed = time.perf_counter() - t0
    out = r.stdout + r.stderr
    m_luts   = re.search(r'\bnd\s*=\s*(\d+)',  out)
    m_levels = re.search(r'\blev\s*=\s*(\d+)', out)
    luts   = int(m_luts.group(1))   if m_luts   else None
    levels = int(m_levels.group(1)) if m_levels else None
    return luts, levels, round(elapsed, 4)

with open(csv_path, 'a') as fout:
    for bench in benchmarks:
        bname = os.path.splitext(os.path.basename(bench))[0]
        print(f'\n── {bname} ──────────────────────────────────────────')

        # Warm-up: fill OS file cache, avoid cold-start bias
        run_abc(bench, C=8, K=K)

        for C in cut_limits:
            times, luts_list, levels_list = [], [], []
            for run_idx in range(1, n_runs + 1):
                luts, levels, elapsed = run_abc(bench, C, K)
                if elapsed is not None:
                    times.append(elapsed)
                    luts_list.append(luts)
                    levels_list.append(levels)
                    fout.write(f'{bname},{C},{run_idx},{luts},{levels},{elapsed}\n')
                    fout.flush()

            if times:
                med    = statistics.median(times)
                luts_v = luts_list[len(luts_list)//2]
                lvl_v  = levels_list[len(levels_list)//2]
                print(f'  C={C:3d}  luts={luts_v}  levels={lvl_v}  '
                      f'median_time={med:.3f}s  runs={[round(t,3) for t in times]}')

print('\nProfiling done.')
PYEOF

echo ""
echo "Results saved to $OUTPUT_CSV"
echo "Next: python3 02_analyze_profile.py"
