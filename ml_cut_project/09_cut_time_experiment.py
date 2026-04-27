import os
import subprocess
import time

ABC_BIN = "/Users/hemanggautam/Desktop/eda_proj/abc/abc"
BENCH_DIR = "/Users/hemanggautam/Desktop/eda_proj/benchmarks"

# We'll use a mix of small and large benchmarks to get a representative average
BENCHMARKS = [
    "arithmetic/adder.aig",
    "arithmetic/div.aig",
    "arithmetic/log2.aig",
    "arithmetic/multiplier.aig",
    "arithmetic/sqrt.aig",
    "arithmetic/square.aig",
    "random_control/mem_ctrl.aig",
    "random_control/voter.aig"
]

C_VALUES = [2, 4, 6, 8, 12, 16]

print("==================================================")
print("Experiment: Mapping Time vs Number of Cuts (-C)")
print("==================================================")

results = {c: 0.0 for c in C_VALUES}

for bench in BENCHMARKS:
    bench_path = os.path.join(BENCH_DIR, bench)
    if not os.path.exists(bench_path):
        print(f"Warning: {bench_path} not found.")
        continue
    
    print(f"\nProcessing {bench}...")
    for c in C_VALUES:
        cmd = f'{ABC_BIN} -c "read_aiger {bench_path}; strash; if -K 6 -C {c};"'
        
        start = time.time()
        # Suppress output to avoid terminal spam
        subprocess.run(cmd, shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elapsed = time.time() - start
        
        results[c] += elapsed
        print(f"  -C {c:<2} : {elapsed:.3f}s")

print("\n==================================================")
print("Final Results: Total CPU Time vs Number of Cuts")
print("==================================================")
print(" Cuts (-C) | Total Time (s) | Normalized")
print("-----------|----------------|------------")

baseline_time = results[8] if 8 in results else 1.0

for c in C_VALUES:
    t = results[c]
    norm = t / baseline_time
    print(f"    {c:<6} |   {t:>8.3f}s   |   {norm:.2f}x")

print("==================================================")
