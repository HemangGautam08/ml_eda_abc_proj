#!/usr/bin/env python3
"""
debug_qor.py — Run this FIRST to diagnose why 08_compare_qor.py returns None.

Usage:  python3 debug_qor.py
"""
import subprocess, os, sys, glob, re

ABC_DEFAULT = os.path.expanduser(
    os.environ.get('ABC_DEFAULT', '/Users/hemanggautam/Desktop/eda_proj/abc/abc'))
ABC_ML = os.path.expanduser(
    os.environ.get('ABC_ML',      '/Users/hemanggautam/Desktop/eda_proj/abc/abc_ml'))
BENCH_DIR = os.path.expanduser(
    os.environ.get('BENCH_DIR',   '/Users/hemanggautam/Desktop/eda_proj/benchmarks'))

SEP = "─" * 70

# ── 1. Binary checks ──────────────────────────────────────────────────────────
print(SEP)
print("1. BINARY CHECKS")
print(SEP)
for label, path in [("ABC_DEFAULT", ABC_DEFAULT), ("ABC_ML", ABC_ML)]:
    exists = os.path.isfile(path)
    exe    = os.access(path, os.X_OK) if exists else False
    size   = f"{os.path.getsize(path)//1024} KB" if exists else "—"
    print(f"  {label}: {path}")
    print(f"    exists={exists}  executable={exe}  size={size}")
    if not exists:
        print(f"    *** NOT FOUND — fix ABC_DEFAULT / ABC_ML env vars ***")
    elif not exe:
        print(f"    *** NOT EXECUTABLE — run: chmod +x {path} ***")

# ── 2. Benchmark checks ───────────────────────────────────────────────────────
print()
print(SEP)
print("2. BENCHMARK CHECKS")
print(SEP)
benchmarks = []
for subdir in ['arithmetic', 'random_control']:
    d = os.path.join(BENCH_DIR, subdir)
    files = sorted(glob.glob(os.path.join(d, '*.aig'))) if os.path.isdir(d) else []
    print(f"  {d}: {'EXISTS' if os.path.isdir(d) else 'MISSING'}  ({len(files)} .aig files)")
    benchmarks += files

if not benchmarks:
    print(f"\n  *** No .aig files found under {BENCH_DIR} ***")
    print("  Check that BENCH_DIR is set correctly.")
    sys.exit(1)

# Pick the smallest benchmark for the test run
bench = min(benchmarks, key=os.path.getsize)
print(f"\n  Using benchmark for test: {bench}")

# ── 3. Raw ABC output ─────────────────────────────────────────────────────────
print()
print(SEP)
print("3. RAW ABC OUTPUT (baseline binary)")
print(SEP)

if not os.path.isfile(ABC_DEFAULT):
    print("  *** Skipping — binary not found ***")
else:
    cmd = [ABC_DEFAULT, '-c',
           f'read_aiger {bench}; strash; if -K 6 -C 8; print_stats;']
    print(f"  Command: {' '.join(cmd)}\n")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        print("  ── stdout ──")
        print(r.stdout if r.stdout.strip() else "    (empty)")
        print("  ── stderr ──")
        print(r.stderr if r.stderr.strip() else "    (empty)")
        print(f"  returncode: {r.returncode}")

        # Try both combined
        combined = r.stdout + r.stderr
        # Look for 'nd' pattern
        nd_match  = re.search(r'\bnd\s*=\s*(\d+)',  combined)
        lev_match = re.search(r'\blev\s*=\s*(\d+)', combined)
        print(f"\n  Regex 'nd'  match: {nd_match}")
        print(f"  Regex 'lev' match: {lev_match}")

        if not nd_match or not lev_match:
            print("\n  *** Regex DID NOT MATCH. Possible causes:")
            if not combined.strip():
                print("    - ABC produced no output at all (crash/segfault?)")
                print("    - Check: is the binary codesigned on macOS?")
                print(f"    - Try:   codesign --sign - --force {ABC_DEFAULT}")
            else:
                print("    - ABC output format differs from expected.")
                print("    - Look above for the actual output format.")
                # Try broader patterns
                nd2  = re.search(r'nd\s*[=:]\s*(\d+)', combined)
                lev2 = re.search(r'lev\s*[=:]\s*(\d+)', combined)
                print(f"    - Broader nd  match: {nd2}")
                print(f"    - Broader lev match: {lev2}")
                # Try 'nodes' / 'levels'
                nd3  = re.search(r'nodes\s*=\s*(\d+)', combined)
                lev3 = re.search(r'levels?\s*=\s*(\d+)', combined)
                print(f"    - 'nodes' match: {nd3}")
                print(f"    - 'levels' match: {lev3}")
    except subprocess.TimeoutExpired:
        print("  *** TIMEOUT after 60s ***")
    except FileNotFoundError:
        print("  *** FileNotFoundError — binary path wrong or not executable ***")

# ── 4. Raw ML-ABC output ──────────────────────────────────────────────────────
print()
print(SEP)
print("4. RAW ABC_ML OUTPUT (ML binary)")
print(SEP)

if not os.path.isfile(ABC_ML):
    print("  *** Skipping — binary not found ***")
else:
    cmd = [ABC_ML, '-c',
           f'read_aiger {bench}; strash; if -K 6 -C 8; print_stats;']
    print(f"  Command: {' '.join(cmd)}\n")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        print("  ── stdout ──")
        print(r.stdout if r.stdout.strip() else "    (empty)")
        print("  ── stderr ──")
        print(r.stderr if r.stderr.strip() else "    (empty)")
        print(f"  returncode: {r.returncode}")
        if r.returncode != 0:
            print(f"  *** Non-zero return code — likely CRASH / SEGFAULT ***")
            print(f"  *** Run manually to see the signal:  {ABC_ML} -c 'read_aiger {bench}; strash; if -K 6 -C 8; print_stats;'")
    except subprocess.TimeoutExpired:
        print("  *** TIMEOUT after 60s ***")
    except FileNotFoundError:
        print("  *** FileNotFoundError — binary path wrong ***")

# ── 5. macOS codesign check ───────────────────────────────────────────────────
print()
print(SEP)
print("5. macOS CODESIGN CHECK")
print(SEP)
for path in [ABC_DEFAULT, ABC_ML]:
    if not os.path.isfile(path):
        continue
    r = subprocess.run(['codesign', '-v', path],
                       capture_output=True, text=True)
    signed = r.returncode == 0
    print(f"  {os.path.basename(path)}: {'signed ✓' if signed else 'NOT signed ✗'}")
    if not signed:
        print(f"    Fix: codesign --sign - --force {path}")

print()
print(SEP)
print("Done. Share the output above to diagnose the issue.")
print(SEP)
