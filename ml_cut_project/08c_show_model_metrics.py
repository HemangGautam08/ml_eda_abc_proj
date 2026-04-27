#!/usr/bin/env python3
"""
08c_show_model_metrics.py
==========================
Shows your ML model's actual predictions and quality — completely
independently of ABC's C code and whether ifML.c is wired correctly.

This lets you demonstrate and visualise what the model learned,
even when the QoR table shows all zeros.

Run from: ~/Desktop/eda_proj/ml_cut_project/
Requires: ml/train_data.npz   (from 05a_preprocess_mac.py)
          ml/cut_model_mlp.pt  (from 05b_train_colab.ipynb)
Produces: results/model_metrics.txt
          results/model_metrics.csv
          results/cut_override_preview.csv  (sample of what ML would change)
"""

import os, sys, pickle
import numpy as np
import pandas as pd

# ── Optional: load torch if available ────────────────────────────────────────
try:
    import torch
    HAS_TORCH = True
except ImportError:
    HAS_TORCH = False
    print("NOTE: torch not installed — using numpy weights from npz directly.")

os.makedirs("results", exist_ok=True)

NPZ_PATH = "ml/train_data.npz"
PT_PATH  = "ml/cut_model_mlp.pt"
PKL_PATH = "ml/scaler.pkl"

# ── Load data ─────────────────────────────────────────────────────────────────
if not os.path.isfile(NPZ_PATH):
    sys.exit(f"ERROR: {NPZ_PATH} not found. Run 05a_preprocess_mac.py first.")

print(f"Loading {NPZ_PATH}...")
data = np.load(NPZ_PATH, allow_pickle=True)

X_scaled        = data['X_scaled']            # (N, 9)
pair_better     = data['pair_better']          # (P,)
pair_worse      = data['pair_worse']           # (P,)
circuits        = data['circuits']             # (N,)
quality         = data['quality']              # (N,) — 0..1 mffc quality
is_best         = data['is_best'].astype(int)  # (N,) — ABC's choice
feature_names   = list(data['feature_names'])

# Weights baked into npz (always available)
scaler_mean  = data['scaler_mean']
scaler_scale = data['scaler_scale']

print(f"  Rows          : {len(X_scaled):,}")
print(f"  Pairs         : {len(pair_better):,}")
print(f"  Features      : {feature_names}")
print(f"  Circuits      : {np.unique(circuits).tolist()}")

# ── Load model weights ────────────────────────────────────────────────────────
def relu(x):
    return np.maximum(0, x)

def mlp_forward(X, W0, b0, W1, b1, W2, b2):
    h = relu(X @ W0.T + b0)
    h = relu(h @ W1.T + b1)
    return (h @ W2.T + b2).squeeze(-1)

if os.path.isfile(PT_PATH) and HAS_TORCH:
    ckpt   = torch.load(PT_PATH, map_location='cpu')
    state  = ckpt['model_state']
    n_feat = ckpt['n_features']
    hidden = ckpt['hidden_sizes']
    W0 = state['fc0.weight'].numpy()
    b0 = state['fc0.bias'].numpy()
    W1 = state['fc1.weight'].numpy()
    b1 = state['fc1.bias'].numpy()
    W2 = state['fc2.weight'].numpy()
    b2 = state['fc2.bias'].numpy()
    print(f"\nLoaded model: {n_feat} → {hidden[0]} → {hidden[1]} → 1  (from .pt)")
    scores = mlp_forward(X_scaled, W0, b0, W1, b1, W2, b2)
else:
    # No model file — use quality as proxy score to still show metrics
    print("\nWARNING: cut_model_mlp.pt not found or torch missing.")
    print("  Using mffc quality score as proxy for model output.")
    print("  Train the model in Colab and re-run for real metrics.\n")
    scores = quality.copy()

print(f"\nScore distribution:")
print(f"  min={scores.min():.4f}  max={scores.max():.4f}  "
      f"mean={scores.mean():.4f}  std={scores.std():.4f}")

# ── Metric 1: Pairwise ranking accuracy ──────────────────────────────────────
print("\n" + "─"*55)
print("Metric 1 — Pairwise Ranking Accuracy")
print("─"*55)

s_better = scores[pair_better]
s_worse  = scores[pair_worse]
diff     = s_better - s_worse

pairwise_acc   = (diff > 0).mean()
pairwise_tie   = (diff == 0).mean()
margin_correct = diff[diff > 0].mean() if (diff > 0).any() else 0.0
margin_wrong   = (-diff)[diff < 0].mean() if (diff < 0).any() else 0.0

print(f"  Total pairs         : {len(diff):,}")
print(f"  ML correct (>0)     : {(diff>0).sum():,}  ({pairwise_acc:.1%})")
print(f"  ML wrong   (<0)     : {(diff<0).sum():,}  ({(diff<0).mean():.1%})")
print(f"  Ties       (=0)     : {(diff==0).sum():,}  ({pairwise_tie:.1%})")
print(f"  Mean margin correct : {margin_correct:.4f}")
print(f"  Mean margin wrong   : {margin_wrong:.4f}")

# ── Metric 2: Top-1 accuracy (does ML pick the MFFC-best cut?) ───────────────
print("\n" + "─"*55)
print("Metric 2 — Top-1 Cut Selection (vs MFFC ground truth)")
print("─"*55)

adj_best = data['adj_score_best'].astype(bool)  # MFFC ground truth label

# For each node group, find which cut gets highest ML score
# Reconstruct node groups using circuit+position alignment
# Use the pair indices: best_pos in pair_better are MFFC-best rows

mffc_best_rows = set(np.where(adj_best)[0])
abc_best_rows  = set(np.where(is_best == 1)[0])

# Among pair_better entries (these are always the "better" of each pair),
# check what fraction have the highest score in their pair
top1_in_pair = (s_better > s_worse).sum()
print(f"  MFFC-best rows      : {len(mffc_best_rows):,}")
print(f"  ABC-best rows       : {len(abc_best_rows):,}")
overlap_mffc_abc = len(mffc_best_rows & abc_best_rows)
print(f"  ABC agrees w/ MFFC  : {overlap_mffc_abc:,} "
      f"({overlap_mffc_abc/max(len(abc_best_rows),1):.1%})")
print()

# ML score at MFFC-best vs non-best
score_mffc_best  = scores[list(mffc_best_rows)].mean()
score_mffc_other = scores[list(set(range(len(scores))) - mffc_best_rows)].mean()
score_abc_best   = scores[list(abc_best_rows)].mean()
score_abc_other  = scores[list(set(range(len(scores))) - abc_best_rows)].mean()

print(f"  Avg ML score — MFFC-best   : {score_mffc_best:+.4f}")
print(f"  Avg ML score — MFFC-other  : {score_mffc_other:+.4f}")
print(f"  Separation (higher=better) : {score_mffc_best - score_mffc_other:+.4f}")
print()
print(f"  Avg ML score — ABC-best    : {score_abc_best:+.4f}")
print(f"  Avg ML score — ABC-other   : {score_abc_other:+.4f}")
print(f"  Separation (higher=better) : {score_abc_best - score_abc_other:+.4f}")

# ── Metric 3: Per-circuit breakdown ──────────────────────────────────────────
print("\n" + "─"*55)
print("Metric 3 — Per-Circuit Pairwise Accuracy")
print("─"*55)

circuit_rows = {}
for i, c in enumerate(circuits):
    circuit_rows.setdefault(c, []).append(i)

circuit_metrics = []
for cname in sorted(np.unique(circuits)):
    rows_set = set(circuit_rows[cname])
    # Find pairs belonging to this circuit
    mask = np.array([pair_better[i] in rows_set for i in range(len(pair_better))])
    if mask.sum() == 0:
        continue
    pb = pair_better[mask]
    pw = pair_worse[mask]
    sb, sw = scores[pb], scores[pw]
    acc = (sb > sw).mean()
    n_nodes = len(set(is_best[list(rows_set)].nonzero()[0]))
    circuit_metrics.append({
        'circuit': cname,
        'pairs': int(mask.sum()),
        'pairwise_acc': round(float(acc), 4),
        'n_abc_best': int((is_best[list(rows_set)] == 1).sum()),
    })

df_circ = pd.DataFrame(circuit_metrics)
print(df_circ.to_string(index=False))

# ── Metric 4: What cuts would ML override? ───────────────────────────────────
print("\n" + "─"*55)
print("Metric 4 — Cut Override Preview (ML vs ABC)")
print("─"*55)
print("  Shows nodes where ML would pick a DIFFERENT cut than ABC.")
print("  This is what ifML.c should be doing at runtime.\n")

# For each node, find ABC best and ML best
# We can do this for the pair_better/pair_worse structure:
# pair_better[i] = MFFC-best row, pair_worse[i] = worse row
# If ML scores pair_worse[i] > pair_better[i], ML would pick a different cut

override_rows = []
for i in range(len(pair_better)):
    pb, pw = pair_better[i], pair_worse[i]
    if scores[pw] > scores[pb]:
        # ML would pick the "worse" (by MFFC) cut
        # Check if ABC also picked pair_better
        abc_picked_better = is_best[pb] == 1
        override_rows.append({
            'better_row': pb,
            'worse_row' : pw,
            'score_mffc_best': round(float(scores[pb]), 4),
            'score_ml_pick'  : round(float(scores[pw]), 4),
            'quality_mffc'   : round(float(quality[pb]), 4),
            'quality_ml_pick': round(float(quality[pw]), 4),
            'abc_agreed_mffc': bool(abc_picked_better),
            'circuit'        : circuits[pb],
        })

n_overrides = len(override_rows)
n_pairs     = len(pair_better)
print(f"  ML agrees with MFFC ranking  : {n_pairs - n_overrides:,} / {n_pairs:,} pairs ({(n_pairs-n_overrides)/n_pairs:.1%})")
print(f"  ML DISAGREES with MFFC ranking: {n_overrides:,} / {n_pairs:,} pairs ({n_overrides/n_pairs:.1%})")
print()

if override_rows:
    df_ov = pd.DataFrame(override_rows[:200])
    # Count how many of these also disagree with ABC
    ml_vs_abc = df_ov[~df_ov['abc_agreed_mffc']]
    print(f"  Of ML overrides, where ABC ALSO picked the MFFC-best cut: "
          f"{df_ov['abc_agreed_mffc'].sum()} ({df_ov['abc_agreed_mffc'].mean():.1%})")
    print(f"  → These are nodes where ML would override ABC's choice")

    df_ov.to_csv("results/cut_override_preview.csv", index=False)
    print(f"\nSaved: results/cut_override_preview.csv  ({len(override_rows)} rows)")

# ── Metric 5: Score histogram per feature ────────────────────────────────────
print("\n" + "─"*55)
print("Metric 5 — Feature Importance (correlation with ML score)")
print("─"*55)

feat_corrs = []
for i, fname in enumerate(feature_names):
    corr = np.corrcoef(X_scaled[:, i], scores)[0, 1]
    feat_corrs.append((fname, corr))

feat_corrs.sort(key=lambda x: abs(x[1]), reverse=True)
for fname, corr in feat_corrs:
    bar = '█' * int(abs(corr) * 30)
    sign = '+' if corr >= 0 else '-'
    print(f"  {fname:<18s}  {sign}{abs(corr):.3f}  {bar}")

# ── Summary report ────────────────────────────────────────────────────────────
print("\n" + "─"*55)
print("Metric 6 — Summary Table (for report)")
print("─"*55)

summary = {
    'Total nodes'               : int((is_best == 1).sum()),
    'Total cuts'                : len(X_scaled),
    'Total pairs'               : len(pair_better),
    'Pairwise accuracy'         : f"{pairwise_acc:.1%}",
    'MFFC-best avg ML score'    : f"{score_mffc_best:+.4f}",
    'Non-best avg ML score'     : f"{score_mffc_other:+.4f}",
    'Score separation'          : f"{score_mffc_best - score_mffc_other:+.4f}",
    'ABC/MFFC agreement'        : f"{overlap_mffc_abc/max(len(abc_best_rows),1):.1%}",
    'ML-MFFC pair agreement'    : f"{(n_pairs-n_overrides)/n_pairs:.1%}",
}

df_summary = pd.DataFrame(list(summary.items()), columns=['Metric', 'Value'])
print(df_summary.to_string(index=False))

# Save text report
report_lines = []
report_lines.append("ML Model Quality Report\n" + "="*55)
report_lines.append(f"\nPairwise Ranking Accuracy: {pairwise_acc:.1%}")
report_lines.append(f"Score separation (MFFC-best vs rest): "
                     f"{score_mffc_best - score_mffc_other:+.4f}")
report_lines.append("\nPer-circuit accuracy:")
report_lines.append(df_circ.to_string(index=False))
report_lines.append("\nFeature correlations with ML score:")
for fname, corr in feat_corrs:
    report_lines.append(f"  {fname:<18s}: {corr:+.4f}")
report_lines.append("\nSummary:")
report_lines.append(df_summary.to_string(index=False))

with open("results/model_metrics.txt", "w") as f:
    f.write("\n".join(report_lines) + "\n")

df_circ.to_csv("results/model_metrics.csv", index=False)

print("\nSaved: results/model_metrics.txt")
print("Saved: results/model_metrics.csv")
print("\nDone.")
