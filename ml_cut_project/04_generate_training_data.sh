#!/bin/zsh
# =============================================================================
# 04_generate_training_data.sh  — CORRECTED
#
# KEY FIX: Expected CSV columns updated to include mffc_size (added in 03).
#          Validation now checks for mffc_size column.
# =============================================================================

export ABC_DIR="${ABC_DIR:-/Users/hemanggautam/Desktop/eda_proj/abc}"
export BENCH_DIR="${BENCH_DIR:-/Users/hemanggautam/Desktop/eda_proj/benchmarks}"
ABC_DUMP="$ABC_DIR/abc_dump"

mkdir -p data

if [[ ! -f "$ABC_DUMP" ]]; then
  echo "ERROR: $ABC_DUMP not found. Run 03_apply_abc_patch.sh first."
  exit 1
fi

# Collect benchmarks
BENCHMARKS=()
for subdir in arithmetic random_control; do
  for f in "$BENCH_DIR/$subdir"/*.aig; do
    [[ -f "$f" ]] && BENCHMARKS+=("$f")
  done
done

if [[ ${#BENCHMARKS[@]} -eq 0 ]]; then
  echo "ERROR: No .aig files in $BENCH_DIR/{arithmetic,random_control}"
  exit 1
fi

echo "Collecting cut data from ${#BENCHMARKS[@]} benchmarks (K=6, C=64)..."
echo "Expected CSV columns: node_id,cut_idx,n_leaves,cut_delay,area_flow,"
echo "                      node_level,required_time,slack,node_fanout,mffc_size,is_best"
echo ""

for BENCH in "${BENCHMARKS[@]}"; do
  BNAME=$(basename "$BENCH" .aig)
  OUT_FILE="data/${BNAME}_cuts.csv"

  echo -n "  $BNAME ... "

  export ABC_CIRCUIT_NAME="$BNAME"
  "$ABC_DUMP" -c "
    read_aiger $BENCH;
    strash;
    if -K 6 -C 64;
    print_stats;
  " > /dev/null 2>&1

  if [[ -f "$OUT_FILE" ]]; then
    HEADER=$(head -1 "$OUT_FILE")
    # FIX: check for both node_fanout and mffc_size
    if [[ "$HEADER" != *"node_fanout"* ]]; then
      echo "\nERROR: node_fanout column missing from $OUT_FILE"
      echo "  Header found: $HEADER"
      echo "  Re-run 03_apply_abc_patch.sh"
      exit 1
    fi
    if [[ "$HEADER" != *"mffc_size"* ]]; then
      echo "\nERROR: mffc_size column missing from $OUT_FILE"
      echo "  Header found: $HEADER"
      echo "  Re-run 03_apply_abc_patch.sh (updated version adds mffc_size)"
      exit 1
    fi
    ROWS=$(( $(wc -l < "$OUT_FILE") - 1 ))
    echo "OK  rows=$ROWS"
  else
    echo "WARNING: no output file generated (ABC_CIRCUIT_NAME may not be set)"
  fi
done

echo ""
echo "Files written:"
ls -lh data/*.csv 2>/dev/null | awk '{printf "  %s  %s\n", $5, $9}'

# ── Validation via Python ─────────────────────────────────────────────────────
python3 << 'PYEOF'
import glob, sys
import pandas as pd
import numpy as np

files = glob.glob("data/*_cuts.csv")
if not files:
    print("No CSV files found in data/")
    sys.exit(1)

EXPECTED_COLS = {
    "node_id","cut_idx","n_leaves","cut_delay","area_flow",
    "node_level","required_time","slack","node_fanout","mffc_size","is_best"
}

print(f"\nValidating {len(files)} CSV file(s)...")
errors = []
total_rows = 0
inf_rows   = 0
INF_THRESH = 1e7

for f in sorted(files):
    df = pd.read_csv(f)
    missing = EXPECTED_COLS - set(df.columns)
    if missing:
        errors.append(f"  {f}: missing columns {missing}")
        continue
    # Check for infinity rows (should be zero after round-guard fix)
    bad = (df["required_time"].abs() > INF_THRESH) | (df["slack"].abs() > INF_THRESH)
    n_bad = bad.sum()
    total_rows += len(df)
    inf_rows   += n_bad
    status = f"rows={len(df)}"
    if n_bad > 0:
        status += f"  WARNING: {n_bad} infinity-timing rows (round guard may not have fired)"
    print(f"  {f}: {status}")

if errors:
    for e in errors:
        print(e)
    sys.exit(1)

print(f"\nTotal rows: {total_rows:,}")
if inf_rows == 0:
    print("✓ No ABC_INFINITY timing rows — round guard is working correctly")
else:
    pct = 100 * inf_rows / total_rows
    print(f"WARNING: {inf_rows:,} ({pct:.1f}%) rows have infinity timing.")
    print("  These will be filtered in 05a_preprocess_mac.py but ideally should be zero.")
    print("  Check that p->nRounds > 0 guard compiled into abc_dump correctly.")
PYEOF