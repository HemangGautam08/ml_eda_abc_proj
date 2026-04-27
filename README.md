# ML-Guided Cut Selection for ASIC Technology Mapping in ABC

## Overview
Technology mapping is a critical step in the ASIC design flow that relies on generating and selecting structural "cuts" in an And-Inverter Graph (AIG). 

The industry-standard open-source tool **ABC** evaluates cuts using localized, greedy heuristics (Area Flow, Delay, MFFC). While increasing the maximum number of cuts per node (e.g., from `C=8` to `C=16`) yields superior Quality of Results (QoR), it nearly doubles the CPU runtime.

This project introduces a machine-learning-guided methodology to intelligently score and select cuts within ABC. By using a lightweight Multi-Layer Perceptron (MLP) to evaluate 9 structural features, the ML model acts as an intelligent tie-breaker during ABC's mapping rounds.

### Key Innovations:
1. **Structural Features Only**: Avoids "label leakage" by refusing to train on ABC's exact internal heuristics. Instead, it learns from features like `n_leaves`, `slack_ratio`, and `mffc_per_leaf`.
2. **Area-Delay Product (ADP) Label**: Uses a Pairwise RankNet architecture to optimize for overall Area-Delay convergence.
3. **Inline Area Blending**: Rather than acting as an external filter (like the SLAP framework), the ML model's inference is natively compiled in C (`ifML.c`) and gently blends into ABC's internal area estimate: `Area' = Area * (1 - α * tanh(ML_Score))` where `α = 0.02`.

## Results
The ML-guided mapper (`abc_ml`) was evaluated against baseline ABC (`C=8`) across 19 AIGER benchmarks (arithmetic and random control suites):

- **Average ADP Reduction**: **2.63%** across all circuits.
- **Maximum ADP Reduction**: **12.38%** on the `square` arithmetic multiplier.
- **Regressions**: **0%** (No degradation observed on any benchmark).

## Repository Structure

- `abc/` - Submodule of the Berkeley ABC logic synthesis tool.
- `benchmarks/` - A suite of AIGER (.aig) circuit files separated into `arithmetic/` and `random_control/`.
- `ml_cut_project/` - The core ML pipeline and integration framework:
  - `00` to `09` scripts: End-to-end pipeline covering cut profiling, feature engineering, training, and final evaluation.
  - `abc_patch/`: Custom C-hooks (`ifML.c`) and exported model weights (`model_weights.h`) for ABC injection.
  - `ml/`: The trained PyTorch model (`cut_model_mlp.pt`) and Scikit-Learn scaler (`scaler.pkl`).

## How to Run the Pipeline

### Prerequisites
- Python 3.x
- GCC/Clang (for compiling ABC)
- Read/Write access to the local directories

### 1. Setup Environment
Initialize the submodules and set up the Python virtual environment:
```bash
git submodule update --init --recursive
cd ml_cut_project
./00_setup_venv.sh
source venv/bin/activate
```

### 2. Inject ML and Build ABC
To inject the custom C inference engine and the compiled ML weights into the ABC source tree:
```bash
./07_install_ml_into_abc.sh
```
This will compile a new binary: `abc/abc_ml`.

### 3. Evaluate Quality of Results (QoR)
To run the automated comparison suite across all AIGER benchmarks:
```bash
python3 08_compare_qor.py
```
This script will output the baseline ABC performance versus the ML-guided ABC performance, detailing the LUT count, Delay Levels, and Area-Delay Product (ADP) improvements.

### (Optional) Training a New Model
If you wish to train a new model from scratch:
1. `01_profile_cuts.sh` (Dump millions of cuts from standard ABC)
2. `04_generate_training_data.sh` (Aggregate the CSV dumps)
3. `python3 05a_preprocess_mac.py` (Feature engineering)
4. Use the generated `train_data.npz` with the `05b_train_colab.ipynb` notebook.
5. `python3 06_export_weights_to_c.py` (Export PyTorch weights to a C header)
