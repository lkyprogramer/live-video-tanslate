#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT}/.model-cache/nemo-ane"
SRC="${ROOT}/engine/native/nemo_ane.mm"

mkdir -p "${OUT_DIR}"

echo "[1/2] Compiling libnemo_ane.dylib (CoreML + Accelerate)..."
clang++ -O3 -std=c++20 -fobjc-arc -shared -DACCELERATE_NEW_LAPACK \
  -framework Foundation -framework CoreML -framework Accelerate \
  "${SRC}" -o "${OUT_DIR}/libnemo_ane.dylib"

echo "[2/2] Checking CoreML .mlmodelc buckets in ${OUT_DIR}..."
for b in fc_S256_w8a16.mlmodelc fc_M560_w8a16.mlmodelc fc_L1024_w8a16.mlmodelc; do
  if [[ ! -d "${OUT_DIR}/${b}" ]]; then
    echo "WARNING: Missing ${OUT_DIR}/${b}." >&2
    echo "  Run in an isolated venv with coremltools==9.0 and torch==2.5.1:" >&2
    echo "  python p0/perf_probe/verify_stage5a_ane.py --export-prod-only --out-dir ${OUT_DIR}" >&2
  fi
done

ls -lh "${OUT_DIR}"
