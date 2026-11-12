# Confucius4-R2T2 Mac/C++ read-only feasibility review

Reviewed 2026-11-10 via Exa fetch of official GitHub master and pinned raw sources. Project HEAD verified 72b81be7070095410b75ed2e11998e347f655218 (2026-11-06). No search expansion, compilation or execution.

## Conclusion

Small, bounded Mac feasibility adaptation is realistic for **stream_llama / LlamaNative**, but this is not a supported plug-and-play Mac backend and no M1 Pro latency/accuracy/RSS result is established. Prefer full llama route for a first smoke gate. The recommended **stream_llama_hybrid** is materially harder: its encoder is vLLM-native, distributed-init-dependent PyTorch defaulting CUDA/BF16, not a generic Transformers encoder that can be switched to MPS by changing one string.

The cited llama.cpp pin actually contains Metal backend source/CMake and mtmd Mac MACHO settings. Thus it is inaccurate to say the custom pin lacks Mac/Metal entirely. The wrapper imports nonpublic mtmd helper APIs, so staying on the exact pin is important.

## Evidence and difficulty

| Finding | Official code evidence | Consequence / difficulty |
|---|---|---|
| Prebuilt is Linux x86_64 CUDA + CPython3.12 | r2t2_llama/README.md pin ad6c66839af3c5646fba8c6c2e2087a1e4e38948, b10950, gcc14.4, libggml-cuda.so | Cannot use binaries on arm64 macOS; rebuild pybind + llama/ggml/mtmd. Moderate known native build work, not downloading a Mac wheel |
| Full llama route is real, not decoder-only | llama_native_backend.py Qwen3ASRNative.generate_once; native_ext.cpp mtmd audio bitmap -> tokenize -> encode -> eval -> decoder; no CUDA headers/calls observed in wrapper | Entire acoustic model can in principle use CPU/Metal through ggml; strongest Mac-first route |
| No wrapper hard-force CUDA CMake | CMakeLists.txt add_subdirectory LLAMA_CPP_DIR, builds MTMD shared, links mtmd+llama, no forced GGML_CUDA | Theory: set GGML_CUDA=OFF and GGML_METAL=ON, arm64, local pybind; NOT validated build recipe |
| Exact pinned upstream has Metal | pinned ggml/src/ggml-metal/CMakeLists.txt finds Metal/Foundation/MetalKit and builds backend; tools/mtmd/CMakeLists.txt includes MACHO_CURRENT_VERSION and Apple XCFramework comments | No source evidence for a fundamental Metal exclusion. Exact Qwen3a graphs/quantization/FlashAttention Metal numerical correctness remains untested |
| Darwin packaging not done | wrapper INSTALL_RPATH $ORIGIN/../bin; refresh instructions cp lib*.so*; package-data bin/*.so, bin/*.so.* | For distributable install use @loader_path/@rpath and include dylibs/metallib. Local build uses BUILD_RPATH absolute build/bin, so this is not automatically an in-place smoke-test blocker. Mac Python extension may itself validly have .so suffix; do not conflate with Linux ELF dependencies |
| Not ctypes in inspected current path | native/__init__.py only docstring; llama_native_backend imports .native.qwen3asr_native; PYBIND11_MODULE declared | No hardcoded ctypes.CDLL .so load found in inspected files. Actual dynamic dependency resolution through pybind extension loader/RPATH |
| vLLM still leaks into full-llama streaming | r2t2_asr.py streaming_transcribe and finish import SamplingParams from vllm if max_new_tokens is nonNone; pyproject mandatory qwen-asr[vllm] | Decouple small sampling-parameter DTO / backend interface, or constrained None path after verification; remove default install-time CUDA/vLLM dependency for Mac. Low-to-moderate Python plumbing, not changing acoustic architecture |
| No cache reuse | native_ext.cpp generate_once and external-embedding path call llama_memory_clear each generation; model adapter forwards all accumulated audio | Standard streaming_transcribe update recomputes from current state's accumulated audio. example.py run_streaming initializes state once for its input wav, not an entire user session by definition. Separate streaming_transcribe_no_reset API has a rolling16s threshold, discards earliest8s plus corresponding chunk_text. That implementation hardcodes320ms first/160ms later chunks. Cache clearing is this GGUF backend implementation, not an unavoidable LSP algorithm property. README explicitly says cache reuse/suffix verification future work. Enabling efficient cache requires higher-risk algorithm/backend engineering; do not treat160ms chunk as160ms compute |
| Native default allocations large | model.py and native config default n_ctx32768, n_batch8192, n_threads32; native n_ubatch min(n_batch,512), FlashAttention explicitly enabled | Defaults unsuitable to infer tiny RSS. Context/batch tuning may reduce memory but can break max utterance/prompt needs. Quantized weights plus mmproj do not include KV/graphs/scratch/Python/translation |
| Hybrid tightly vLLM encoder | llama_hybrid_backend.py uses Qwen3ASRAudioEncoder from qwen_asr.core.vllm_backend, vllm distributed/config, Gloo world1, device cuda, BF16; reads full HF model.safetensors audio_tower | Mac hybrid requires replace/reproduce encoder with Transformers/MPS/MLX or prove unsupported vLLM-native CPU route; high effort compared to full llama. It still loads mmproj on decode side, so memory may duplicate encoder-related state |
| Quality difference documented | README says full-llama misses quiet onset in case47; hybrid mitigates; paper results not established for GGUF routes | Even successful Metal load is not parity. Test onset, Japanese, quantized/full-precision differences before integration |

Difficulty judgment: (1) native arm64 build/import + load + one-shot sample = moderate/contained; (2) minimal stable-prefix streaming with vLLM import decoupled = moderate; (3) reliable long-running low-occupancy product under2.5GiB + Hy translation = high/uncertain; (4) recommended hybrid on Mac = high. These are qualitative engineering estimates, not measured effort promises.

U=1 is NOT a rejection reason: withheld provisional suffix is internal. Consume finalized/committed prefix (second streaming return), not mutable hypothesis. Both U=0 and U=1 can satisfy externally append-only output. Do not expose state.text indiscriminately.

GGUF around1.0GiB decoder plus0.3GiB projector, if confirmed in parent model-card inspection, means weight file total only. Not a resident-memory claim and no guarantee for combined ASR+Hy memory budget.

No separate custom llama.patch is referenced by inspected rebuild instructions: they tell clone upstream and checkout exact commit. C++ does include project-internal mtmd-helper-common.h/decode_embd_batch; exact upstream header was verified present. This is tight version coupling, not evidence of missing unpublished patch. No successful Mac compile is claimed.

## Read primary URLs

- https://github.com/netease-youdao/Confucius4-R2T2
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/README.md
- https://github.com/netease-youdao/Confucius4-R2T2/tree/master/r2t2_llama
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/pyproject.toml
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/CMakeLists.txt
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/llama_native_backend.py
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/llama_hybrid_backend.py
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/native/__init__.py
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/native_ext.cpp
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/model.py
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2_llama/example_llama.py
- https://github.com/netease-youdao/Confucius4-R2T2/blob/master/r2t2/r2t2_asr.py
- https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/ggml/src/ggml-metal/CMakeLists.txt
- https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/tools/mtmd/CMakeLists.txt
- https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/tools/mtmd/mtmd-helper-common.h
- https://github.com/ggml-org/llama.cpp/blob/ad6c66839af3c5646fba8c6c2e2087a1e4e38948/tools/mtmd/models/qwen3a.cpp

GitHub blob/main paths failed because default branch is master; corrected without general search. Some directory tree fetches unavailable; no inferred claims from those failures.



## Pinned source anchors for independent root verification

Project revision: 72b81be7070095410b75ed2e11998e347f655218. Pin verified from official commit page https://github.com/netease-youdao/Confucius4-R2T2/commit/72b81be7070095410b75ed2e11998e347f655218 .
Line indices below were counted from Exa extraction of pinned raw source; use named functions/expressions as authoritative anchors if GitHub source rendering shifts whitespace.

- r2t2/r2t2_asr.py streaming_transcribe L300–489: vllm SamplingParams importL348–349; concat audioL378–382 and full audio_accum forwardedL416. This function has no rolling truncation.
- Same file finish_streaming_transcribe L490–577: SamplingParamsL523–524; tail concatL538–541.
- Same file streaming_transcribe_no_reset L578–761: vllm importL617–618; rolling threshold/discard/chunk bookkeepingL653–672; forwardwindowL681. max_samples16s, discard8s, firstchunk5120samples, normal2560samples. This is a distinct API, NOT the one example.py calls.
- Same file finish_streaming_transcribe_no_reset L762–end: same rollinglogicL819–836.
- example.py run_streaming L92–169: initializes stateL104 once outside loop; calls streaming_transcribe, not no_reset; no VAD segmentation/reset in this function. Input is a wav passed by caller. Do not describe it as forever-user-session accumulation.
- r2t2_llama/native_ext.cpp generate_once L418–619: clearKV L451, mtmd_encode_chunk L512. End-to-end native path re-encodes supplied currentaudio each invocation.
- Same file generate_once_with_embedding L648–696 calls prefill_only_with_embeddingL662; helper beginsL700, clearKV L731. Hybrid encoder separately recomputes suppliedaudio via llama_hybrid_backend.py VllmAudioEncoder.encode and LlamaHybridStreaming.generate_from_prompt. Clear applies decoder KV to both current GGUF routes.
- Same file PYBIND11_MODULE L818 onward establishes pybind import, not ctypes.
- llama_native_backend.py LlamaNativeOnetime.__init__ imports .native.qwen3asr_native; native/__init__.py docstring only. No ctypes loading found in inspected current native chain.
- CMakeLists.txt target_link_libraries mtmd llama; BUILD_RPATH absolute build/bin; INSTALL_RPATH $ORIGIN/../bin. Need Darwin packaging/loader adaptation, and native rebuilding, not just a Metalflag.
- llama_hybrid_backend.py HybridConfig / VllmAudioEncoder.__init__: cuda/BF16 defaults, imports vllm.distributed and config plus qwen_asr.core.vllm_backend, gloo init. Changing device='mps' alone is not established.
- Pinned llama.cpp revision ad6c66839af3c5646fba8c6c2e2087a1e4e38948 has Metal backend and mtmd helper source; no separate unpublished patch requirement found.

Decision: a small capped **source/import/load/parity gate** is justified; do not directly add to a large evaluation matrix. First establish arm64 build/import, eliminate vLLM-only import/dependency leakage in full llama path, verify short Japanese onset and U=1 committed-prefix behavior, then measure process RSS + ASRcompute at actual past-only chunks. Stop if deep encoder graph surgery or unsupported kernels appear. Long-run/caching and hybrid-MPS are separate, larger projects.

