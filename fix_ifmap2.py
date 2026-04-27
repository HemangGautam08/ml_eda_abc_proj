#!/usr/bin/env python3
"""
fix_ifmap2.py  — line-by-line, no regex
----------------------------------------
1. Removes ALL #ifdef USE_ML ... #endif /* USE_ML */ blocks from ifMap.c
2. Injects If_ManMLPostProcess once, before the final `return 1;` line

Run:
    python3 fix_ifmap2.py

Then rebuild:
    cd /Users/hemanggautam/Desktop/eda_proj/abc
    make clean && make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
    cp abc abc_ml
    make clean && make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2" ABC_USE_NO_READLINE=1
"""

import os, sys

ABC_DIR = os.environ.get("ABC_DIR", "/Users/hemanggautam/Desktop/eda_proj/abc")
IFMAP   = f"{ABC_DIR}/src/map/if/ifMap.c"

with open(IFMAP) as f:
    lines = f.readlines()

# ── Step 1: Strip all USE_ML blocks line by line ──────────────────────────────
out        = []
inside_ml  = False
removed_blocks = 0

for line in lines:
    stripped = line.strip()
    if stripped == "#ifdef USE_ML":
        inside_ml = True
        removed_blocks += 1
        continue
    if inside_ml:
        # End marker: #endif with USE_ML comment (various spacings)
        if stripped.startswith("#endif") and "USE_ML" in stripped:
            inside_ml = False
        continue   # drop everything inside the block including #endif
    out.append(line)

print(f"Removed {removed_blocks} USE_ML block(s)")

# Sanity check
for i, line in enumerate(out, 1):
    if "USE_ML" in line:
        print(f"  WARNING: USE_ML still at line {i}: {line.rstrip()}")

# ── Step 2: Find the final `    return 1;` and inject before it ───────────────
# We want the LAST occurrence — that's the end of If_ManPerformMapping.
last_return_idx = None
for i in range(len(out) - 1, -1, -1):
    if out[i].rstrip() == "    return 1;":
        last_return_idx = i
        break

if last_return_idx is None:
    print("ERROR: Could not find `    return 1;` in ifMap.c")
    print("Add manually just before the final return 1; in If_ManPerformMapping:")
    print("  #ifdef USE_ML")
    print("  If_ManMLPostProcess( p );")
    print("  #endif /* USE_ML */")
    sys.exit(1)

inject = [
    "#ifdef USE_ML\n",
    "    If_ManMLPostProcess( p );\n",
    "#endif /* USE_ML */\n",
]
out = out[:last_return_idx] + inject + out[last_return_idx:]
print(f"Injected If_ManMLPostProcess at line {last_return_idx + 1} (before final return 1;)  ✓")

# ── Step 3: Print context for eyeball check ───────────────────────────────────
inj_line = last_return_idx  # 0-indexed, now points to #ifdef
print("\n── Context around injection ───────────────────────────────────────────")
start = max(0, inj_line - 4)
end   = min(len(out), inj_line + 7)
for i in range(start, end):
    marker = " >>>" if inj_line <= i < inj_line + 3 else "    "
    print(f"{marker} {i+1:5d}  {out[i].rstrip()}")

# ── Step 4: Write ─────────────────────────────────────────────────────────────
with open(IFMAP, "w") as f:
    f.writelines(out)
print(f"\nSaved: {IFMAP}")

# ── Step 5: Verify ordering ───────────────────────────────────────────────────
src = "".join(out)
round_lines = [i+1 for i,l in enumerate(out) if "If_ManPerformMappingRound" in l]
post_lines  = [i+1 for i,l in enumerate(out) if "If_ManMLPostProcess" in l]
print(f"\nIf_ManPerformMappingRound at lines: {round_lines}")
print(f"If_ManMLPostProcess        at lines: {post_lines}")
if round_lines and post_lines and post_lines[-1] > round_lines[-1]:
    print("Order correct: ML post-process is AFTER all mapping rounds  ✓")
else:
    print("WARNING: check ordering manually in ifMap.c")

print("""
── Next steps ──────────────────────────────────────────────────────────────────
cd /Users/hemanggautam/Desktop/eda_proj/abc

# Build abc_ml
make clean
make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2 -DUSE_ML" ABC_USE_NO_READLINE=1
cp abc abc_ml

# Restore clean baseline abc
make clean
make -j$(sysctl -n hw.ncpu) OPTFLAGS="-O2" ABC_USE_NO_READLINE=1

# Sanity check (should not abort):
./abc_ml -c "read_aiger /Users/hemanggautam/Desktop/eda_proj/benchmarks/random_control/cavlc.aig; strash; if -K 6 -C 8; print_stats;"

# Full comparison:
python3 08_compare_qor.py
────────────────────────────────────────────────────────────────────────────────
""")
