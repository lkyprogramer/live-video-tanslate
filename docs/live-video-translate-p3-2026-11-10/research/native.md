# Native runtime workstream — 2026-11-10
Date window: 2026-11-10 minus one year = 2025-11-10; discover within this year plus older foundations. Read-only; no model downloads/inference. Exa skill and searching/code/source-quality instructions fully read.
sources_reviewed: 108 (search result slots, not deduplicated sources or full-text page count). Exa calls succeeded; no permission blocker.

## Findings with classification

### New omission, weak evidence: Edge0 Audio8-ASR-0.1B / iOS-ANE
Primary card: https://huggingface.co/Edge0/Audio8-ASR-0.1B
Deployment: https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE
Pinned artifact snapshot 6a3f52c400cd436e1bd32c0514758345ddf14bfa (API: created Jul10, lastmodified Jul15 2026). Old Audio8 namespace redirects Edge0.
Important: 0.1B means LM only (103,502,336), total unique parameters323,990,528. Qwen3-ASR audio encoder+MLP and 8-layer decoder. Claims JA among seven languages, but public accuracy table only seven EN OpenASR sets plus Chinese WenetSpeech, no Japanese CER; EN BF16 H200 giant batch is not M1.
Package CoreML mixed FP16/INT8 tower + CPU ORT INT4 decoder. iOS18/macOS15, Swift6, fullXcode, CLI works on macOS per docs. Artifact bundle ~437MB; top README screenshot183MB iPhone process footprint, internal SDK README says ~600MB resident: inconsistent contexts, not M1 measured. Do not assert183MB M1. License CC-BY-NC4.0.
Read source AudioTower.swift: dictionary buckets500/1000/3000 frames ->5/10/30s functions, .cpuAndNeuralEngine requested; projector Accelerate CPU. ASRTranscriber.swift: one-shot full samples, min.25s, maxNewTokens128, cancellation, serialized queue, extraction+encoder+generate each call. Not audio-input streaming; VAD explicitly excluded. Existing methods async are not streaming. First ANE compile30–60s docs.
Best use: low-memory candidate to audit JA with original short-cuts; no evidence it beats Reazon/Haas locally. Do not add whole framework just because small brand name.

### New omission from root, deployment audit: Omnilingual CTC
PR https://github.com/soniqo/speech-swift/pull/201 merged Apr11, merge03920e17671fc79d59da1573923958304e233c42.
Current source inspected a57702e6f523fa36ac5532d2bdc5f35f701a052c:
https://github.com/soniqo/speech-swift/blob/a57702e6f523fa36ac5532d2bdc5f35f701a052c/Sources/OmnilingualASR/OmnilingualASR.swift
- Actual default .cpuAndGPU through resolver, comment ANE compile unreliable. Guide says ANE; code wins. Use precompiled.mlmodelc required (e43ed41233b16ee845ef20f50607170ab9784a33 Apr17), not local compile.
- Batch-only; fixed5/10s chunks nonoverlap; perchunk layernorm then zero pad; concatenates output; 40s cap; language argument ignored; no encoder cache streaming. MLX wholeutterance cap40s.
- CoreML ~312MB on disk, docs M3Pro~600MB resident,120ms per10s window; not JA M1. 300M4bit193MB and1B4bit549MB are weights disk, not RSS.
- PR E2E fixtures only EN HI FR AR. No JA deployed parity evidence. Source list contains jpn_Jpan, not accuracy evidence.
- v2 WARNING: docs/models says “CTC300M v2”, but CoreML HF API card base_model facebook/omniASR-CTC-300M (no-v2), sha a755e007d0a56bd8c7e9885f55bb2112e52b4d51 createdApr11 lastApr17. MLX1B card also cites facebook/omniASR-CTC-1B; configs lack source revision. Cannot assert v2 checkpoint purely on docs. Root researching Meta official dates.
- Quantized1B PR English sanity text degrades; card says “should within~1% WER” not measured parity. Quantization must be measured in Japanese.
Sources:
https://huggingface.co/aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8
https://huggingface.co/aufklarer/Omnilingual-ASR-CTC-1B-MLX-4bit
https://soniqo.audio/guides/omnilingual
https://github.com/soniqo/speech-swift/blob/main/docs/inference/omnilingual-asr-inference.md

### Optimization not new quality: speech-swift CoreML Qwen/Nemotron
v0.0.20 https://github.com/soniqo/speech-swift/releases/tag/v0.0.20 adds Nemotron multilingual cache-aware600M INT8 ~612MB, M5ProRTF~.07. Model already tested by user; backend only.
https://github.com/soniqo/speech-swift/blob/main/docs/inference/qwen3-asr-inference.md
CoreML Qwen rebuild #292 fixes WRONG global padded attention causing early EOS: English n200 M5Pro48GB WER24.88->3.02; MLX.6B8bit1.82,4bit2.20; CoreML RTF.098 vsMLX8bit.015, so ANE not necessarily faster. Native transcribeWithoutMLX path is truly Metal-free; ordinary CoreML transcribe still uses MLXArray mel/output. Stateful KV cache for text tokens is not streaming audio encoder. MLX .6B reusable cache ceiling min1GiB,RAM/8 is NOT total footprint. Cannot assert GPU queue bottleneck given user's measured queueP95<1ms.

### Already tested / already in previous notes: Haas all3 paths
Prior project evidence in [optimization-plan.md at 16567cb](https://github.com/lkyprogramer/live-video-tanslate/blob/16567cb00f86ac6e131745f7b75c20a1c271cb33/docs/optimization-plan.md) pins oboroge0/Haas-ja-v1.0-onnx-quantized@7f5ab0a4b1bf39db78afd6691eeecbc4f9d42e39. Same as discovery. The corresponding prior optimization-results records user M1 Pro 2 threads, same cuts, 275/516 ms and 731.7 MiB, without a quality win. Prior next.md already mentions Sanami and speech.cpp. These are historical lines of evidence, not new P3 measurements; see the package source ledger before reuse. Do not re-label new.
Sanami source snapshot62f542657b454afa7873424e3dfb6d7a1d9c270d:
https://huggingface.co/Sanami/haas-ja-coreml
Read HaasASR.swift. window1504mel=15.04s, context208=2.08s, encode all audio overlapping chunks then decode; short clips padded but true length passed. CPU decoder/joint+projector, ANE requested encoder only. No live incremental API, no publicRSS. M1Air33clips32/33 NeMo tokens,4x120s broadcastsCER8.57; offline whole processRTFx85 vsMetal63vsONNXRust26. Raw eval clips not redistributable. Not candidate to bypass future-audio/no-rewrite contract.
ONNX upstream M1Pro4threads1.13.6FLEURS650CER4.34/RTF.039,SpeechBSD2093CER5.96/RTF.040; peakRSS.8GB12s1.12GB29s. API compatible viaKDAstate spoof LSTM and #tdt metadata; different logmel frontends; cannot compare officialjacer6.12 vsplainCER.
speech.cppv0.10.0 (not transcribe.cpp) all model nativeC++ggml Metal, one-shotVAD driven“live”. M2independent8utterance3–8s .29–.41s request service time,footprint712MBidle750MBmax https://zenn.dev/airtanker/articles/haas-ja-mac-dictation . NotM1 norfirstsubtitle.
https://github.com/nyosegawa/speech.cpp currenttree03555100287c118b41d1b475044e5ab1e07baa8c.

### New runtime but not applicable M1: NemotronCoreAI
https://github.com/mweinbach/NemotronCoreAI v0.2.0. True PCM streaming encoder/predictor states,80/160/320/560/1120ms. Requires macOS27/iOS27; AOTmatrixM3Ultra/M4/M5/A19, noM1artifact. GPU source fallback exists; NeuralEngine path explicitly guarded because rank6transpose compilerfails. No JapaneseM1memory/latency. Not an ANE breakthrough or evidence currentM1blockedGPU.

### Narrow browser native paths are duplicate models
https://github.com/khawjaahmad/nemotron-asr-webgpu : cache-awareFastConformer encoderWebGPU,decoder/jointWASM deliberately avoids per-token readback; ownenergyVAD200ms feed,~750MBdownloadNOTRSS, noM1Japaneseeval. ReadREADME; nonnative app can complement architecture notprovebettermodel.
https://huggingface.co/goryodog/tokihisu-nemotron-3.5-asr-streaming-0.6b-webgpu-fp16 : explicit cache-FREE chunk encoder, RNNT predictorstateonly; runtimecard states notnativeTransformers.js pipeline. No encoderKV/convstate; nottruefullstream.
https://huggingface.co/onnx-community/cohere-transcribe-03-2026-ONNX :14languagesja,q4WebGPUone-shot; alreadyknownmodel. TinyMoonshineJAONNX is originalsingleutterance model and not123Mstreaming.
hayamimi is actual Japanese practitioner but README defaultReazon alreadybaseline, Windows11tested; macOS/Linuxexpected notendtoendCI. “100ms afterstop” + defaultminsilence.35s ambiguousmeasurement,twopassrefineafter2s incompatiblefinal no-rewrite. CPU-only≠lowenergy.

## Source quality / negative coverage
Primary conversion codecards/runtimePRs privileged; vendor “should <1% WER”, M5Englishspeed, byte size and requestedANE explicitly notvalidatedquality/RSS/dispatch. Searches mentioningReddit and authorprojects returned mostlyGitHub/HF/Zenn, no genuineRedditM1JApractitioner matched. AppleNN-specific queries returnedCoreML/CoreAI implementations, notanindependent verified JapaneseM1AppleNNruntime.

## Complete search log (all result URLs)
### Query 1; numResults=10
日本語 音声認識 M1 Mac CoreML ANE リアルタイム 2025 2026 GitHub

- FluidInference/FluidAudio: https://github.com/FluidInference/fluidaudio
- Sanami/haas-ja-coreml · Hugging Face: https://huggingface.co/Sanami/haas-ja-coreml
- README_ja.md: https://github.com/soniqo/speech-swift/blob/main/README_ja.md
- FluidInference/parakeet-0.6b-ja-coreml · Hugging Face: https://huggingface.co/FluidInference/parakeet-0.6b-ja-coreml
- Documentation/ASR/NemotronMultilingual.md: https://github.com/FluidInference/FluidAudio/blob/6428e291/Documentation/ASR/NemotronMultilingual.md
- sagostin/gotranscribesrv: https://github.com/sagostin/gotranscribesrv
- Documentation/Models.md: https://github.com/FluidInference/FluidAudio/blob/main/Documentation/Models.md
- sebastian-software/whisper-coreml: https://github.com/sebastian-software/whisper-coreml
- jkf87/murmur: https://github.com/jkf87/murmur
- AustinJiangH/voixful: https://github.com/AustinJiangH/voixful

### Query 2; numResults=10
GitHub Apple Neural Engine Core ML speech recognition Japanese streaming ASR Swift C++ runtime 2026 AppleNN

- mweinbach/NemotronCoreAI: https://github.com/mweinbach/NemotronCoreAI
- Documentation/ASR/NemotronMultilingual.md: https://github.com/FluidInference/FluidAudio/blob/6428e291/Documentation/ASR/NemotronMultilingual.md
- feat(asr): Nemotron 3.5 ASR Streaming Multilingual 0.6B (40 locales, on-device CoreML/ANE): https://github.com/FluidInference/FluidAudio/pull/657
- v0.0.20: https://github.com/soniqo/speech-swift/releases/tag/v0.0.20
- Add multilingual Nemotron-3.5 streaming ASR support: https://github.com/k2-fsa/sherpa-onnx/pull/3671
- OpenVoiceOS/ovos-stt-plugin-coreml: https://github.com/OpenVoiceOS/ovos-stt-plugin-coreml
- sagostin/gotranscribesrv: https://github.com/sagostin/gotranscribesrv
- AustinJiangH/voixful: https://github.com/AustinJiangH/voixful
- soniqo/speech-swift: https://github.com/soniqo/speech-swift/
- ggml-org/whisper.cpp: https://github.com/ggml-org/whisper.cpp/

### Query 3; numResults=10
Reddit developer speech recognition Apple Silicon M1 Japanese streaming quantized encoder CoreML Metal problems

- whisper.cppのCore ML版をM1 MacBook Proで動かす: https://zenn.dev/shu223/articles/whisper-coreml
- Why Voice Type turned off the Core ML encoder: https://carelesswhisper.app/blog/core-ml-metal-dictation
- FluidInference/parakeet-0.6b-ja-coreml · Hugging Face: https://huggingface.co/FluidInference/parakeet-0.6b-ja-coreml
- Apple Neural Engine on M1 Macs?  · Issue #51 · SYSTRAN/faster-whisper: https://github.com/SYSTRAN/faster-whisper/issues/51
- separated model support as well · FluidInference/parakeet-tdt-0.6b-v3-coreml at b10f709: https://huggingface.co/FluidInference/parakeet-tdt-0.6b-v3-coreml/commit/b10f7099a2118cf07cf96f0ee758779f64336a4e
- CoreML on M1 gives a wrong transcription: https://github.com/ggerganov/whisper.cpp/issues/1901
- dvoitekh/WhisperCoreML: https://github.com/dvoitekh/whispercoreml
- faster-whisper vs whisper.cpp with CoreML · Issue #168 · SYSTRAN/faster-whisper: https://github.com/SYSTRAN/faster-whisper/issues/168
- ontypehq/mlx-swift-asr: https://github.com/ontypehq/mlx-swift-asr
- aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-MLX-4bit · Hugging Face: https://huggingface.co/aufklarer/Nemotron-3.5-ASR-Streaming-0.6B-MLX-4bit

### Query 4; numResults=10
GitHub sherpa onnx icefall streaming Japanese multilingual speech recognition CPU small model 2025 2026

- Gilgamesh-J/deployment-for-icefall-streaming-model: https://github.com/Gilgamesh-J/deployment-for-icefall-streaming-model
- oboroge0/hayamimi: https://github.com/oboroge0/hayamimi
- docs/STREAMING_ASR.md: https://github.com/sandraschi/speech-mcp/blob/main/docs/STREAMING_ASR.md
- k2-fsa/icefall: https://github.com/k2-fsa/icefall/
- Online transducer models — sherpa 1.3 documentation: https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/index.html
- README.md: https://github.com/speechmux/plugin-stt-sherpa-onnx/blob/main/README.md
- k2-fsa/sherpa-onnx: https://github.com/k2-fsa/sherpa-onnx/
- Gilgamesh-J/X-ASR: https://github.com/Gilgamesh-J/X-ASR
- PCL-Voice/PengChengStarling: https://github.com/yangb05/PengChengStarling
- Add multilingual Nemotron-3.5 streaming ASR support: https://github.com/k2-fsa/sherpa-onnx/pull/3671

### Query 5; numResults=10
GitHub transformers.js ONNX WebGPU Japanese speech recognition streaming new models 2026

- khawjaahmad/nemotron-asr-webgpu: https://github.com/khawjaahmad/nemotron-asr-webgpu
- ysdede/parakeet.js: https://github.com/ysdede/parakeet.js/
- README.md · ibm-granite/granite-speech-webgpu at main: https://huggingface.co/spaces/ibm-granite/granite-speech-webgpu/blob/main/README.md
- snomiao/otoji: https://github.com/snomiao/otoji
- goryodog/tokihisu-nemotron-3.5-asr-streaming-0.6b-webgpu-fp16 · Hugging Face: https://huggingface.co/goryodog/tokihisu-nemotron-3.5-asr-streaming-0.6b-webgpu-fp16
- Add multilingual Nemotron-3.5 streaming ASR support: https://github.com/k2-fsa/sherpa-onnx/pull/3671
- onnx-community/cohere-transcribe-03-2026-ONNX · Hugging Face: https://huggingface.co/onnx-community/cohere-transcribe-03-2026-ONNX
- transformers.js-examples/moonshine-web at main · huggingface/transformers.js-examples · GitHub: https://github.com/huggingface/transformers.js-examples/tree/main/moonshine-web
- wmoto-ai/moonshine-tiny-ja-ONNX · Hugging Face: https://huggingface.co/wmoto-ai/moonshine-tiny-ja-ONNX
- webgpu.md - transformers.js: https://github.com/huggingface/transformers.js/blob/main/packages/transformers/docs/source/guides/webgpu.md

### Query 6; numResults=8
AppleNN ANE ASR speech recognition small Japanese model Swift CoreML encoder quantization 2026 GitHub

- Sanami/haas-ja-coreml · Hugging Face: https://huggingface.co/Sanami/haas-ja-coreml
- README.md · josscii/parakeet-0.6b-ja-coreml at 6d945d20adaa63fbb3f5a4590940971995955d32: https://huggingface.co/josscii/parakeet-0.6b-ja-coreml/blob/6d945d20adaa63fbb3f5a4590940971995955d32/README.md
- v0.0.20: https://github.com/soniqo/speech-swift/releases/tag/v0.0.20
- OpenVoiceOS/ovos-stt-plugin-coreml: https://github.com/OpenVoiceOS/ovos-stt-plugin-coreml
- soniqo/speech-swift: AI speech toolkit for Apple Silicon — ASR, TTS, ...: https://github.com/soniqo/speech-swift
- README.md · Audio8/Audio8-ASR-0.1B-iOS-ANE at 1b26b5d95b235b7edc142d15d9e3003894cc32f7: https://huggingface.co/Audio8/Audio8-ASR-0.1B-iOS-ANE/blob/1b26b5d95b235b7edc142d15d9e3003894cc32f7/README.md
- beshkenadze/nemotron-3.5-asr-streaming-0.6b-coreml-ane · Hugging Face: https://huggingface.co/beshkenadze/nemotron-3.5-asr-streaming-0.6b-coreml-ane
- feat(nemotron): offline CoreML/ANE encoder (drop-in for the MLX Conformer): https://github.com/Blaizzy/mlx-audio-swift/pull/202

### Query 7; numResults=8
soniqo speech-swift Qwen3 ASR CoreML MLState M1 encoder decoder benchmark memory parity

- docs/inference/qwen3-asr-inference.md: https://github.com/soniqo/speech-swift/blob/main/docs/inference/qwen3-asr-inference.md
- docs/benchmarks/asr-wer.md: https://github.com/soniqo/speech-swift/blob/main/docs/benchmarks/asr-wer.md
- feat(asr): bench scaffold + Qwen3-ASR CoreML encoder chunked-attention rebuild: https://github.com/soniqo/speech-swift/pull/292
- Model compression experiments: mixed-bit palettization, outlier decomposition, stateful CoreML: https://github.com/soniqo/speech-swift/issues/110
- soniqo/speech-swift: https://github.com/soniqo/speech-swift/
- aufklarer/Qwen3-ASR-CoreML · Hugging Face: https://huggingface.co/aufklarer/Qwen3-ASR-CoreML
- FAQ — Soniqo Docs: https://soniqo.audio/faq
- Architecture — Apple — Soniqo Docs: https://soniqo.audio/architecture

### Query 8; numResults=8
Sanami Haas-ja CoreML M1 speech.cpp 0.10 quantization short chunk Japanese latency GitHub

- Sanami/haas-ja-coreml · Hugging Face: https://huggingface.co/Sanami/haas-ja-coreml
- nyosegawa/speech.cpp: https://github.com/nyosegawa/speech.cpp
- Sanami/haas-ja-coreml at main: https://huggingface.co/Sanami/haas-ja-coreml/tree/main
- CER 6.1% の日本語音声認識 Haas-ja-v1.0 で、認識0.3〜0.4秒・メモリ約710MBの Mac ローカル音声入力を作る: https://zenn.dev/airtanker/articles/haas-ja-mac-dictation
- Haas-ASR - third-intelligence: https://github.com/third-intelligence/Haas-ASR
- sakasegawa/Haas-ja-v1.0-GGUF · Hugging Face: https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF
- Building speech.cpp, a Speech Synthesis and Recognition Engine for Voice Conversation - Sakasegawa's Blog: https://nyosegawa.com/en/posts/speech-cpp/
- docs/cli.md: https://github.com/nyosegawa/speech.cpp/blob/main/docs/cli.md

### Query 9; numResults=8
Reddit Haas ja speech.cpp Mac M1 Japanese recognition author Sanami sakasegawa

- nyosegawa/speech.cpp: Speech synthesis ...: https://github.com/nyosegawa/speech.cpp
- Sanami/haas-ja-coreml · Hugging Face: https://huggingface.co/Sanami/haas-ja-coreml
- sakasegawa/Haas-ja-v1.0-GGUF · Hugging Face: https://huggingface.co/sakasegawa/Haas-ja-v1.0-GGUF
- masatatsu8/haas-input: https://github.com/masatatsu8/haas-input
- Building speech.cpp, a Speech Synthesis and Recognition ...: https://nyosegawa.com/en/posts/speech-cpp/
- CER 6.1% の日本語音声認識 Haas-ja-v1.0 で、認識0.3〜0.4秒・メモリ約710MBの Mac ローカル音声入力を作る: https://zenn.dev/airtanker/articles/haas-ja-mac-dictation
- speech.cppで日本語の文字起こし｜Macで72分を1分 | AI活用顧問 | 専属AI担当: https://fyve.co.jp/ai-advisor/articles/speech-cpp-haas-ja-mac-transcription
- oboroge0/Haas-ja-v1.0-onnx · Hugging Face: https://huggingface.co/oboroge0/Haas-ja-v1.0-onnx

### Query 10; numResults=8
Audio8 ASR 0.1B Japanese benchmark CER WER Edge0 Audio8 iOS ANE GitHub streaming speech recognition

- Edge0/Audio8-ASR-0.1B · Hugging Face: https://huggingface.co/Edge0/Audio8-ASR-0.1B
- Edge0/Audio8-ASR-0.1B-iOS-ANE · Hugging Face: https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE
- On-demand speech recognition in your pocket - edge0: https://edge0.ai/models/asr-0.1b
- scrappylabsai/audio8-asr-lean: https://github.com/scrappylabsai/audio8-asr-lean
- クラウド不要、0.1Bで音声を聞き取る——Audio8-ASR-0.1Bの仕組みと実力を徹底解説｜アイドリ | AI-Driven Lab: https://note.com/ai_driven/n/n36dc0235c5b1
- 「Audio8/Audio8-ASR-0.1B」（旧名: AutoArk-AI/Audio8-ASR-0.1B）を試す: https://zenn.dev/kun432/scraps/f8b16a3139ebb1
- Medium: https://ai-engineering-trend.medium.com/can-a-100m-parameter-on-device-asr-model-even-transcribe-rap-7c52783a2c66
- Edge0 open-sources Audio8 on-device speech models ...: https://www.voiceaispace.com/articles/edge0-open-sources-audio8-on-device-speech-models-and-a-35b-llm-it-says-runs-in

### Query 11; numResults=6
speech-swift Omnilingual CTC v2 coreml ANE CPU GPU japanese issue regression 2026

- docs/inference/omnilingual-asr-inference.md: https://github.com/soniqo/speech-swift/blob/main/docs/inference/omnilingual-asr-inference.md
- Add OmnilingualASR — 1,672 languages on CoreML + MLX, with shared SentencePiece/SDPA extract: https://github.com/soniqo/speech-swift/pull/201
- Releases · soniqo/speech-swift · GitHub: https://github.com/soniqo/speech-swift/releases
- Omnilingual ASR — 1672 Languages on Apple Silicon: https://soniqo.audio/guides/omnilingual
- aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8 at main: https://huggingface.co/aufklarer/Omnilingual-ASR-CTC-300M-CoreML-INT8/blob/main/README.md
- fix(asr): stride-aware NaN-safe argmax for CoreML decoder (+ CI diagnostics): https://github.com/soniqo/speech-swift/pull/278

### Query 12; numResults=6
AppleNN Swift ANE speech ASR runtime Japanese 2026 GitHub

- mweinbach/NemotronCoreAI: https://github.com/mweinbach/NemotronCoreAI
- v0.0.20: https://github.com/soniqo/speech-swift/releases/tag/v0.0.20
- AustinJiangH/voixful: https://github.com/AustinJiangH/voixful
- soniqo/speech-swift: https://github.com/soniqo/speech-swift/
- mweinbach/parakeet-coreml-swift: https://github.com/mweinbach/parakeet-coreml-swift
- Feature request: expose Qwen3-ASR API for multilingual (Japanese) support: https://github.com/FluidInference/fluidaudio-rs/issues/7

### Query 13; numResults=6
Reddit Audio8 ASR Edge0 Japanese Apple ANE M1 transcription developer

- Edge0/Audio8-ASR-0.1B-iOS-ANE · Hugging Face: https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE
- Blame for SpeechKit/README.md · Edge0/Audio8-ASR-0.1B-iOS-ANE at main: https://huggingface.co/Edge0/Audio8-ASR-0.1B-iOS-ANE/blame/main/SpeechKit/README.md
- N/A: https://huggingface.co/Audio8/Audio8-ASR-0.1B-iOS-ANE/raw/main/GETTING_STARTED.md
- Edge0/Audio8-ASR-0.1B · Hugging Face: https://huggingface.co/Edge0/Audio8-ASR-0.1B
- whisper.cppのCore ML版をM1 MacBook Proで動かす: https://zenn.dev/shu223/articles/whisper-coreml
- Apple Neural Engine on M1 Macs?  · Issue #51 · SYSTRAN/faster-whisper: https://github.com/SYSTRAN/faster-whisper/issues/51

