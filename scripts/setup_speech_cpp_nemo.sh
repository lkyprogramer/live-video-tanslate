#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT}/.model-cache/speech-cpp"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

mkdir -p "${OUT_DIR}"

echo "[1/3] Cloning speech.cpp v0.11.2 (cc16a71)..."
git clone --depth 1 --branch v0.11.2 https://github.com/nyosegawa/speech.cpp "${TMP_DIR}/speech.cpp"
git -C "${TMP_DIR}/speech.cpp" submodule update --init --depth 1 ggml

echo "[2/3] Building libspeech.4.dylib (Release)..."
cmake -S "${TMP_DIR}/speech.cpp" -B "${TMP_DIR}/build" -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build "${TMP_DIR}/build" --target speech -j 6

cp -f "${TMP_DIR}/build/libspeech.4.dylib" "${OUT_DIR}/libspeech.4.dylib"
ln -sf libspeech.4.dylib "${OUT_DIR}/libspeech.dylib"

EXPECTED_MODEL_SHA="944411cb740938f858c939aa1d42f7eb4bb884fc350d9daae8b7e724853e54a3"

echo "[3/3] Downloading reazonspeech-nemo-619M-v2-Q8_0.gguf..."
"${ROOT}/engine/.venv/bin/python" -c "
from huggingface_hub import hf_hub_download
hf_hub_download('sakasegawa/reazonspeech-nemo-v2-GGUF', 'reazonspeech-nemo-619M-v2-Q8_0.gguf', local_dir='${OUT_DIR}')
"

ACTUAL_MODEL_SHA="$(shasum -a 256 "${OUT_DIR}/reazonspeech-nemo-619M-v2-Q8_0.gguf" | awk '{print $1}')"
if [[ "${ACTUAL_MODEL_SHA}" != "${EXPECTED_MODEL_SHA}" ]]; then
  echo "ERROR: SHA256 mismatch for reazonspeech-nemo-619M-v2-Q8_0.gguf: got ${ACTUAL_MODEL_SHA}, expected ${EXPECTED_MODEL_SHA}" >&2
  exit 1
fi
ACTUAL_DYLIB_SHA="$(shasum -a 256 "${OUT_DIR}/libspeech.4.dylib" | awk '{print $1}')"
echo "Verified model SHA256: ${ACTUAL_MODEL_SHA}"
echo "Built libspeech.4.dylib SHA256: ${ACTUAL_DYLIB_SHA}"

echo "Done! Staged in ${OUT_DIR}:"
ls -lh "${OUT_DIR}"
