#!/bin/zsh
# =============================================================================
# 00_setup_venv.sh  — one-time Python venv setup
# Run from: ~/Desktop/eda_proj/ml_cut_project/
# =============================================================================
set -e

VENV_DIR="${VENV_DIR:-venv}"

echo "Creating Python venv in: $VENV_DIR"
python3 -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"

echo "Upgrading pip..."
pip install --quiet --upgrade pip

echo "Installing dependencies..."
# Native arm64 PyTorch — no special URL needed on Apple Silicon
pip install --quiet torch numpy pandas scikit-learn matplotlib seaborn

echo ""
echo "Verifying imports..."
python3 -c "
import torch, sklearn, pandas, numpy, matplotlib
print(f'  numpy      {numpy.__version__}')
print(f'  pandas     {pandas.__version__}')
print(f'  sklearn    {sklearn.__version__}')
print(f'  torch      {torch.__version__}')
print(f'  matplotlib {matplotlib.__version__}')
print('  All OK ✓')
"

echo ""
echo "=== venv ready at: $VENV_DIR ==="
echo "Activate each session with:  source $VENV_DIR/bin/activate"
