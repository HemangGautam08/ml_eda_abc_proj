#!/usr/bin/env python3
"""
02_analyze_profile.py
Task 1: Analyze and visualize how mapping time varies with number of cuts.

Run from: ~/Desktop/eda_proj/ml_cut_project/
Requires: results/profiling_results.csv  (from 01_profile_cuts.sh)
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use('Agg')   # non-interactive backend — safe everywhere
import matplotlib.pyplot as plt
import os, sys

CSV_PATH = "results/profiling_results.csv"

if not os.path.exists(CSV_PATH):
    print(f"ERROR: {CSV_PATH} not found. Run 01_profile_cuts.sh first.")
    sys.exit(1)

df = pd.read_csv(CSV_PATH)
df.columns = df.columns.str.strip()

df['cut_limit'] = df['cut_limit'].astype(int)
df['luts']      = pd.to_numeric(df['luts'],     errors='coerce')
df['levels']    = pd.to_numeric(df['levels'],   errors='coerce')
df['time_sec']  = pd.to_numeric(df['time_sec'], errors='coerce')
df.dropna(subset=['time_sec', 'luts', 'levels'], inplace=True)

# Aggregate: median over repeated runs
agg = (df.groupby(['benchmark', 'cut_limit'])
         .agg(time_sec=('time_sec', 'median'),
              luts=('luts', 'median'),
              levels=('levels', 'median'))
         .reset_index())

# ── Print tables ──────────────────────────────────────────────────────────────
print("=" * 70)
print("TASK 1 — Mapping Time (median CPU sec) vs Number of Cuts")
print("=" * 70)

pivot_time = agg.pivot(index='cut_limit', columns='benchmark', values='time_sec')
pivot_luts = agg.pivot(index='cut_limit', columns='benchmark', values='luts')
pivot_lvl  = agg.pivot(index='cut_limit', columns='benchmark', values='levels')

print("\n--- Median CPU Time (seconds) ---")
print(pivot_time.round(4).to_string())
print("\n--- LUT Count ---")
print(pivot_luts.round(0).astype('Int64').to_string())
print("\n--- Levels (Critical-Path Depth) ---")
print(pivot_lvl.round(0).astype('Int64').to_string())

avg_time  = agg.groupby('cut_limit')['time_sec'].mean()
avg_luts  = agg.groupby('cut_limit')['luts'].mean()
avg_level = agg.groupby('cut_limit')['levels'].mean()

summary = pd.DataFrame({
    'avg_time_sec': avg_time,
    'avg_luts':     avg_luts,
    'avg_levels':   avg_level,
})
print("\n--- Average Across All Benchmarks ---")
print(summary.round(4).to_string())

# Normalized time (baseline C=8, which is ABC's default)
baseline_C = 8
if baseline_C in avg_time.index:
    base = avg_time[baseline_C]
    print(f"\n  Mapping time normalized to C={baseline_C} (ABC default):")
    for C, t in avg_time.sort_index().items():
        bar  = '█' * int(round(t / base * 10))
        flag = '  ← default' if C == baseline_C else ''
        print(f"    C={C:3d}:  {t/base:6.2f}x  {bar}{flag}")

# Area-Delay Product
adp = avg_luts * avg_level
if baseline_C in adp.index:
    base_adp = adp[baseline_C]
    print("\n--- Area-Delay Product (avg LUTs × avg Levels) ---")
    for C, v in adp.sort_index().items():
        flag = '  ← default' if C == baseline_C else ''
        print(f"    C={C:3d}:  ADP={v:.0f}  ({(v/base_adp-1)*100:+.1f}%){flag}")

# ── Save text report ──────────────────────────────────────────────────────────
os.makedirs("results", exist_ok=True)
report_path = "results/profiling_report.txt"
with open(report_path, 'w') as f:
    f.write("Task 1 — Mapping Time vs Number of Cuts\n")
    f.write("=" * 70 + "\n\n")
    f.write("Median CPU Time (seconds):\n")
    f.write(pivot_time.round(4).to_string() + "\n\n")
    f.write("Average across benchmarks:\n")
    f.write(summary.round(4).to_string() + "\n\n")
    f.write("ADP (LUTs × Levels):\n")
    f.write(adp.round(0).to_string() + "\n")
print(f"\nText report saved to {report_path}")

# ── Plots (3 panels) ──────────────────────────────────────────────────────────
cut_vals = sorted(agg['cut_limit'].unique())
fig, axes = plt.subplots(1, 3, figsize=(15, 5))
fig.suptitle("Task 1: Mapping Time vs Number of Cuts (K=6 LUT mapping)", fontsize=13)

# Panel 1: per-benchmark time
ax = axes[0]
for bench in agg['benchmark'].unique():
    sub = agg[agg['benchmark'] == bench].sort_values('cut_limit')
    ax.plot(sub['cut_limit'], sub['time_sec'], marker='o', label=bench,
            alpha=0.75, linewidth=1.5)
ax.set_xlabel('Cut Limit (C)')
ax.set_ylabel('Median Time (s)')
ax.set_title('Per-Benchmark Mapping Time')
ax.set_xscale('log', base=2)
ax.set_xticks(cut_vals)
ax.set_xticklabels(cut_vals)
ax.legend(fontsize=7, ncol=2)
ax.grid(True, alpha=0.3)

# Panel 2: average time with annotations
ax = axes[1]
ax.plot(avg_time.index, avg_time.values, 'b-o', linewidth=2, markersize=9,
        markerfacecolor='white', markeredgewidth=2)
ax.fill_between(avg_time.index, avg_time.values, alpha=0.1, color='blue')
for C, t in avg_time.items():
    ax.annotate(f'{t:.2f}s', (C, t), textcoords="offset points",
                xytext=(0, 10), ha='center', fontsize=8, color='blue')
ax.set_xlabel('Cut Limit (C)')
ax.set_ylabel('Average Time (s)')
ax.set_title('Average Mapping Time vs Cut Limit')
ax.set_xscale('log', base=2)
ax.set_xticks(cut_vals)
ax.set_xticklabels(cut_vals)
ax.grid(True, alpha=0.3)

# Panel 3: QoR dual-axis (LUTs + Levels)
ax  = axes[2]
ax2 = ax.twinx()
ln1 = ax.plot(avg_luts.index,  avg_luts.values,  'r-o', label='Avg LUTs',   linewidth=2)
ln2 = ax2.plot(avg_level.index, avg_level.values, 'g-s', label='Avg Levels', linewidth=2)
ax.set_xlabel('Cut Limit (C)')
ax.set_ylabel('Avg LUT Count',            color='r')
ax2.set_ylabel('Avg Critical-Path Depth', color='g')
ax.tick_params(axis='y', colors='r')
ax2.tick_params(axis='y', colors='g')
ax.set_title('QoR vs Cut Limit')
ax.set_xscale('log', base=2)
ax.set_xticks(cut_vals)
ax.set_xticklabels(cut_vals)
lns  = ln1 + ln2
labs = [l.get_label() for l in lns]
ax.legend(lns, labs, loc='upper right')
ax.grid(True, alpha=0.3)

plt.tight_layout()
out_png = "results/profiling_plots.png"
plt.savefig(out_png, dpi=150, bbox_inches='tight')
print(f"Plot saved to {out_png}")
print("\nTask 1 done.")
