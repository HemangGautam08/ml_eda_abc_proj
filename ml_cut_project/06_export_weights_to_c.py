#!/usr/bin/env python3
"""
06_export_weights_to_c.py
=========================
Reads ml/cut_model_mlp.pt and ml/scaler.pkl, writes abc_patch/model_weights.h.

The model uses 9 structural features (defined in 05a_preprocess_mac.py):
  n_leaves, node_level, node_fanout, is_critical, slack_ratio,
  fanout_adj_area, area_per_leaf, mffc_size, mffc_per_leaf

ML_INPUT_DIM is read dynamically from the checkpoint's n_features field
so it is always written correctly into model_weights.h.

Run from: ~/Desktop/eda_proj/ml_cut_project/
"""

import os, sys, pickle
import numpy as np
import torch

MODEL_PT   = "ml/cut_model_mlp.pt"
SCALER_PKL = "ml/scaler.pkl"
OUT_HEADER = "abc_patch/model_weights.h"

# ── Load model ────────────────────────────────────────────────────────────────
if not os.path.exists(MODEL_PT):
    sys.exit(
        f"ERROR: {MODEL_PT} not found.\n"
        "Download from Colab and place at ml/cut_model_mlp.pt"
    )

ckpt = torch.load(MODEL_PT, map_location="cpu", weights_only=False)
print(f"Loaded: {MODEL_PT}")

# Read n_features from checkpoint; fall back to inferring from weight shape
if "n_features" in ckpt:
    n_feat = int(ckpt["n_features"])
else:
    # Infer from first layer weight shape (rows=hidden[0], cols=n_feat)
    state_tmp = ckpt["model_state"]
    n_feat = state_tmp["fc0.weight"].shape[1]
    print(f"  WARNING: 'n_features' key missing from checkpoint; "
          f"inferred n_feat={n_feat} from fc0.weight shape.")

if "hidden_sizes" in ckpt:
    hidden = list(ckpt["hidden_sizes"])
else:
    state_tmp = ckpt["model_state"]
    hidden = [state_tmp["fc0.bias"].shape[0], state_tmp["fc1.bias"].shape[0]]
    print(f"  WARNING: 'hidden_sizes' key missing; inferred {hidden}.")

state = ckpt["model_state"]

print(f"  n_features  : {n_feat}")
print(f"  hidden_sizes: {hidden}")
if "feature_names" in ckpt:
    print(f"  features    : {ckpt['feature_names']}")

expected_keys = {"fc0.weight","fc0.bias","fc1.weight","fc1.bias","fc2.weight","fc2.bias"}
missing = expected_keys - set(state.keys())
if missing:
    sys.exit(f"ERROR: model_state missing keys: {missing}\nFound: {list(state.keys())}")

def to_np(v):
    return v.numpy() if hasattr(v, "numpy") else np.array(v)

L0_w = to_np(state["fc0.weight"])   # (hidden[0], n_feat)
L0_b = to_np(state["fc0.bias"])
L1_w = to_np(state["fc1.weight"])   # (hidden[1], hidden[0])
L1_b = to_np(state["fc1.bias"])
L2_w = to_np(state["fc2.weight"])   # (1, hidden[1])
L2_b = to_np(state["fc2.bias"])

print(f"\nLayer shapes:")
print(f"  L0_weight: {L0_w.shape}  L0_bias: {L0_b.shape}")
print(f"  L1_weight: {L1_w.shape}  L1_bias: {L1_b.shape}")
print(f"  L2_weight: {L2_w.shape}  L2_bias: {L2_b.shape}")

# Verify shapes are consistent
assert L0_w.shape == (hidden[0], n_feat), f"L0_w shape mismatch: {L0_w.shape}"
assert L1_w.shape == (hidden[1], hidden[0]), f"L1_w shape mismatch: {L1_w.shape}"
assert L2_w.shape == (1, hidden[1]), f"L2_w shape mismatch: {L2_w.shape}"

# ── Load scaler ───────────────────────────────────────────────────────────────
if not os.path.exists(SCALER_PKL):
    sys.exit(f"ERROR: {SCALER_PKL} not found.\nRun python3 05a_preprocess_mac.py to generate it.")

with open(SCALER_PKL, "rb") as f:
    scaler_data = pickle.load(f)

scaler = scaler_data["scaler"]
scaler_mean  = scaler.mean_.astype(np.float32)
scaler_scale = scaler.scale_.astype(np.float32)

print(f"\nScaler loaded: mean range [{scaler_mean.min():.3f}, {scaler_mean.max():.3f}]")
print(f"              std  range [{scaler_scale.min():.3f}, {scaler_scale.max():.3f}]")

# Sanity check
feat_names = list(scaler_data.get("feature_names", []))
if "required_time" in feat_names:
    idx = list(feat_names).index("required_time")
    if scaler_mean[idx] > 1e7:
        sys.exit(
            f"FATAL: scaler_mean[required_time]={scaler_mean[idx]:.2e} — "
            "looks like infinity rows infected the scaler.\n"
            "Re-run 05a_preprocess_mac.py with the corrected version."
        )
    print(f"  required_time mean = {scaler_mean[idx]:.2f}  ✓")

assert len(scaler_mean) == n_feat, (
    f"Scaler has {len(scaler_mean)} features but model expects {n_feat}. "
    "Re-run 05a_preprocess_mac.py and retrain."
)

# ── Format helper ─────────────────────────────────────────────────────────────
def fmt_array(arr, name, comment=""):
    arr  = np.array(arr).flatten()
    n    = len(arr)
    cols = 8
    lines = []
    if comment:
        lines.append(f"/* {comment} */")
    lines.append(f"static const float {name}[{n}] = {{")
    for i in range(0, n, cols):
        chunk  = arr[i:i+cols]
        row    = ", ".join(f"{v:.8f}f" for v in chunk)
        suffix = "," if i + cols < n else ""
        lines.append(f"    {row}{suffix}")
    lines.append("};")
    lines.append("")
    return "\n".join(lines) + "\n"

# ── Write header ──────────────────────────────────────────────────────────────
os.makedirs("abc_patch", exist_ok=True)

with open(OUT_HEADER, "w") as f:
    f.write("/* model_weights.h\n")
    f.write(" * Auto-generated by 06_export_weights_to_c.py\n")
    f.write(f" * Architecture: {n_feat} -> {hidden[0]} -> {hidden[1]} -> 1\n")
    f.write(f" * Features ({n_feat}): {', '.join(feat_names)}\n")
    f.write(" * Do NOT edit by hand — re-run 06_export_weights_to_c.py to regenerate.\n")
    f.write(" */\n\n")
    f.write("#ifndef MODEL_WEIGHTS_H\n")
    f.write("#define MODEL_WEIGHTS_H\n\n")
    f.write("#include <math.h>\n\n")
    f.write(f"#define ML_INPUT_DIM  {n_feat}\n")
    f.write(f"#define ML_HIDDEN1    {hidden[0]}\n")
    f.write(f"#define ML_HIDDEN2    {hidden[1]}\n\n")
    f.write(fmt_array(scaler_mean,  "scaler_mean",  "StandardScaler: per-feature mean"))
    f.write(fmt_array(scaler_scale, "scaler_scale", "StandardScaler: per-feature std"))
    f.write(fmt_array(L0_w, "L0_weight", f"fc0 weight ({n_feat} -> {hidden[0]})"))
    f.write(fmt_array(L0_b, "L0_bias",   f"fc0 bias   ({hidden[0]},)"))
    f.write(fmt_array(L1_w, "L1_weight", f"fc1 weight ({hidden[0]} -> {hidden[1]})"))
    f.write(fmt_array(L1_b, "L1_bias",   f"fc1 bias   ({hidden[1]},)"))
    f.write(fmt_array(L2_w, "L2_weight", f"fc2 weight ({hidden[1]} -> 1)"))
    f.write(fmt_array(L2_b, "L2_bias",   f"fc2 bias   (1,)"))
    f.write("#endif /* MODEL_WEIGHTS_H */\n")

size_kb = os.path.getsize(OUT_HEADER) / 1024
print(f"\nExported: {OUT_HEADER}  ({size_kb:.1f} KB)  ✓")
print(f"  ML_INPUT_DIM = {n_feat}")
print("\nNext: ./07_install_ml_into_abc.sh")