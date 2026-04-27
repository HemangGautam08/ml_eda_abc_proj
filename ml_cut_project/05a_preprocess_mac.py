#!/usr/bin/env python3
"""
05a_preprocess_mac.py
=====================================
Run on your Mac BEFORE going to Colab.

KEY DESIGN DECISIONS:
  [FIX-1] Infinity filter: rows where required_time or slack is ABC_INFINITY
          (from round-0 dumps) are dropped BEFORE the scaler is fitted.

  [FIX-2] Label uses mffc_combined = (mffc_size / n_leaves) * timing_factor.
          cut_delay, area_flow, required_time, slack are used ONLY here for
          labeling, then EXCLUDED from training features to prevent leakage.

  [FIX-3] Training features are structural/topological only — signals that
          ABC does not directly optimise. This forces the model to learn
          something genuinely new rather than approximating ABC's own
          area_flow / cut_delay comparator.

  [FIX-4] reset_index after infinity filter so pair indices are positional
          (0..N-1) and match X_scaled row indices exactly.

Run from: ~/Desktop/eda_proj/ml_cut_project/
Requires: data/*_cuts.csv   (from 04_generate_training_data.sh)
Produces: ml/train_data.npz
          ml/scaler.pkl
          ml/features.txt
"""

import os, sys, glob, time, pickle
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

# ── Config ────────────────────────────────────────────────────────────────────
DATA_DIR = "data"
ML_DIR   = "ml"
SEED     = 42

# Columns used ONLY for label construction — never passed to the model.
# Including these as features would teach the model to copy ABC's heuristic.
LABEL_ONLY_COLS = ["cut_delay", "area_flow", "required_time", "slack"]

# Training features — structural/topological signals only.
# None of these are directly optimised by ABC's cut comparator.
FEATURE_NAMES = [
    "n_leaves",         # number of inputs to this cut
    "node_level",       # topological depth of the node
    "node_fanout",      # how many nodes consume this node's output
    "is_critical",      # 1 if node is on critical path, else 0
    "slack_ratio",      # slack / |required_time|  — relative timing pressure
    "fanout_adj_area",  # area_flow / sqrt(fanout)  — structural interaction
    "area_per_leaf",    # area_flow / n_leaves      — cut efficiency ratio
    "mffc_size",        # nodes absorbed for free under this cut
    "mffc_per_leaf",    # mffc_size / n_leaves      — MFFC area efficiency
]
N_FEATURES = len(FEATURE_NAMES)   # 9

MAX_PAIRS_PER_NODE = 20
INF_THRESH         = 1e7    # values above this are ABC_INFINITY

os.makedirs(ML_DIR, exist_ok=True)
np.random.seed(SEED)

# ── 1. Load CSVs ──────────────────────────────────────────────────────────────
csv_files = sorted(glob.glob(os.path.join(DATA_DIR, "*_cuts.csv")))
if not csv_files:
    sys.exit(f"ERROR: no *_cuts.csv in {DATA_DIR}/. Run 04_generate_training_data.sh first.")

print(f"Loading {len(csv_files)} CSV file(s)...")
frames = []
for f in csv_files:
    circuit_name = os.path.basename(f).replace("_cuts.csv", "")
    df_c = pd.read_csv(f)
    df_c["circuit"] = circuit_name
    frames.append(df_c)

df = pd.concat(frames, ignore_index=True)
print(f"  Total rows (raw) : {len(df):,}")
print(f"  Columns          : {list(df.columns)}")

# ── Guard: required columns ───────────────────────────────────────────────────
for col in ["node_id", "n_leaves", "cut_delay", "area_flow",
            "node_level", "required_time", "slack", "node_fanout",
            "mffc_size", "is_best"]:
    if col not in df.columns:
        if col == "mffc_size":
            print("WARNING: mffc_size missing — re-run 03_apply_abc_patch.sh")
            print("  Falling back to floor(area_flow + 0.5) as mffc_size proxy.")
            df["mffc_size"] = (df["area_flow"] + 0.5).astype(int).clip(lower=1)
        elif col == "node_fanout":
            print("WARNING: node_fanout missing — falling back to 1.")
            df["node_fanout"] = 1
        else:
            sys.exit(f"ERROR: missing column '{col}'.")

df["node_fanout"] = df["node_fanout"].clip(lower=1)
df["mffc_size"]   = df["mffc_size"].clip(lower=1, upper=1000)

# ── FIX-1: Drop infinity-timing rows ─────────────────────────────────────────
n_before = len(df)
bad_mask = (df["required_time"].abs() > INF_THRESH) | (df["slack"].abs() > INF_THRESH)
df = df[~bad_mask].copy()
df = df.reset_index(drop=True)   # FIX-4: positional indices must match X_scaled
n_dropped = n_before - len(df)
if n_dropped > 0:
    print(f"\n[FIX-1] Dropped {n_dropped:,} rows ({100*n_dropped/n_before:.1f}%) "
          f"with ABC_INFINITY timing (round-0 dump artefacts).")
    print(f"  Remaining rows: {len(df):,}")
else:
    print(f"\n[FIX-1] ✓ No infinity-timing rows found.")

if len(df) == 0:
    sys.exit("ERROR: All rows were dropped by infinity filter.")

# ── 2. Feature engineering ────────────────────────────────────────────────────
print("\nEngineering features...")
eps     = 1e-6
req_abs = df["required_time"].abs() + eps

# Derived structural features (safe to use — lose the raw label-only values)
df["slack_ratio"]     = df["slack"]      / req_abs
df["fanout_adj_area"] = df["area_flow"]  / (np.sqrt(df["node_fanout"]) + eps)
df["area_per_leaf"]   = df["area_flow"]  / (df["n_leaves"] + eps)
df["is_critical"]     = (df["slack"] <= 0).astype(float)
df["mffc_per_leaf"]   = df["mffc_size"]  / (df["n_leaves"] + eps)

# ── FIX-2: MFFC-based label ───────────────────────────────────────────────────
# area_flow and cut_delay are used here for labeling only — not in features.
#
# mffc_score = mffc_size / n_leaves  (larger MFFC = more logic absorbed free)
# timing_factor penalises high-delay cuts on the critical path (slack <= 0).
#   alpha=0.4 for critical nodes, 0.1 for slack nodes.
#
print("[FIX-2] Computing ADP-based label (Option 1)...")

# Optimize Area-Delay Product directly. 
# We want lower ADP, so quality is -ADP (higher is better)
adp = df["area_flow"] * df["cut_delay"]
df["mffc_combined"] = -adp

# Normalise per-node to [0, 1] quality score
def node_quality_mffc(g):
    mn, mx = g.min(), g.max()
    if mx > mn:
        return (g - mn) / (mx - mn)
    return pd.Series(1.0, index=g.index)

df["quality"] = (df.groupby(["circuit", "node_id"])["mffc_combined"]
                   .transform(node_quality_mffc))

node_max = df.groupby(["circuit", "node_id"])["mffc_combined"].transform("max")
df["is_mffc_best"] = (df["mffc_combined"] - node_max).abs() < 1e-9

abc_total  = (df["is_best"] == 1).sum()
mffc_agree = (df["is_best"].astype(bool) & df["is_mffc_best"]).sum()
print(f"  ABC picks MFFC-optimal cut: {mffc_agree / abc_total:.1%}  "
      f"(lower = more room for ML to beat ABC)")

# ── 3. Pairwise training data (RankNet) ───────────────────────────────────────
print("\nGenerating pairwise training data (ranked by mffc_combined)...")
t0 = time.time()

# FIX-3: X_full uses structural features only — LABEL_ONLY_COLS are excluded
X_full   = df[FEATURE_NAMES].values.astype(np.float32)
circuits = df["circuit"].values

pair_better, pair_worse = [], []

for (circuit, node_id), grp in df.groupby(["circuit", "node_id"]):
    if len(grp) < 2:
        continue
    grp_sorted = grp.sort_values("mffc_combined", ascending=False)
    pos_idxs   = grp_sorted.index.tolist()   # positional after reset_index ✓
    n          = len(pos_idxs)

    best_pos    = pos_idxs[0]
    pairs_added = 0
    for worse_pos in pos_idxs[1:]:
        pair_better.append(best_pos)
        pair_worse.append(worse_pos)
        pairs_added += 1
        if pairs_added >= MAX_PAIRS_PER_NODE:
            break

    # Extra signal: 2nd-best vs worst
    if n >= 4 and pairs_added < MAX_PAIRS_PER_NODE:
        pair_better.append(pos_idxs[1])
        pair_worse.append(pos_idxs[-1])

pair_better = np.array(pair_better, dtype=np.int64)
pair_worse  = np.array(pair_worse,  dtype=np.int64)
print(f"  Pairs: {len(pair_better):,}  (took {time.time() - t0:.1f}s)")

# ── 4. Fit StandardScaler on structural features only ────────────────────────
print("\nFitting StandardScaler...")
scaler   = StandardScaler()
X_scaled = scaler.fit_transform(X_full).astype(np.float32)
print(f"  mean range : [{scaler.mean_.min():.3f}, {scaler.mean_.max():.3f}]")
print(f"  std  range : [{scaler.scale_.min():.3f}, {scaler.scale_.max():.3f}]")
print(f"  ✓ Scaler fitted on {N_FEATURES} structural features "
      f"(label-only cols excluded)")

# ── 5. Circuit-level train/val split ─────────────────────────────────────────
unique_circuits = np.unique(circuits)
n_circuits      = len(unique_circuits)

if n_circuits == 1:
    print("WARNING: only 1 circuit — using it for both train and val.")
    val_circuits   = set(unique_circuits)
    train_circuits = set(unique_circuits)
else:
    rng            = np.random.default_rng(SEED)
    shuffled       = rng.permutation(unique_circuits)
    n_val          = max(1, int(n_circuits * 0.2))
    val_circuits   = set(shuffled[:n_val])
    train_circuits = set(shuffled[n_val:])

print(f"\nTrain circuits ({len(train_circuits)}): {sorted(train_circuits)}")
print(f"Val   circuits ({len(val_circuits)}):   {sorted(val_circuits)}")

# ── 6. Save scaler ────────────────────────────────────────────────────────────
scaler_pkl = os.path.join(ML_DIR, "scaler.pkl")
with open(scaler_pkl, "wb") as f:
    pickle.dump({"scaler": scaler, "feature_names": FEATURE_NAMES}, f)
print(f"\nSaved: {scaler_pkl}")

with open(os.path.join(ML_DIR, "features.txt"), "w") as f:
    for i, name in enumerate(FEATURE_NAMES):
        f.write(f"{i}\t{name}\n")
print(f"Saved: ml/features.txt")

# ── 7. Export train_data.npz ──────────────────────────────────────────────────
npz_path = os.path.join(ML_DIR, "train_data.npz")
print(f"\nSaving {npz_path} ...")

np.savez_compressed(
    npz_path,
    X_scaled        = X_scaled,
    pair_better     = pair_better,
    pair_worse      = pair_worse,
    circuits        = np.array(circuits),
    unique_circuits = unique_circuits,
    quality         = df["quality"].values.astype(np.float32),
    adj_score_best  = df["is_mffc_best"].values.astype(np.bool_),
    is_best         = df["is_best"].values.astype(np.int8),
    scaler_mean     = scaler.mean_.astype(np.float32),
    scaler_scale    = scaler.scale_.astype(np.float32),
    feature_names   = np.array(FEATURE_NAMES),
)

size_kb = os.path.getsize(npz_path) / 1024
print(f"Saved: {npz_path}  ({size_kb:.0f} KB)")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Preprocessing complete.")
print(f"  Rows         : {len(df):,}")
print(f"  Features     : {N_FEATURES}  (structural only — no label leakage)")
print(f"  Label-only   : {LABEL_ONLY_COLS}  (excluded from X)")
print(f"  Pairs        : {len(pair_better):,}")
print(f"  Circuits     : {n_circuits}  (train={len(train_circuits)}, val={len(val_circuits)})")
print(f"  Label        : mffc_combined (area efficiency + timing penalty)")
print("=" * 60)
print("""
Next steps
----------
1. Upload  ml/train_data.npz  to Google Drive
   (My Drive/ml_cut_project/train_data.npz)

2. Open 05b_train_colab.ipynb in Google Colab
   (Runtime → Change runtime type → T4 GPU)

3. Run Cell 6b first to clear old checkpoints (N_FEATURES changed 14→9).

4. Run all cells — model trains on structural features only.

5. Download  ml/cut_model_mlp.pt  → place at ml/cut_model_mlp.pt

6. Run:  python3 06_export_weights_to_c.py
""")
