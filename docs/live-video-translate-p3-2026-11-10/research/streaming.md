# Streaming / architecture branch — Exa expanded ASR sweep

As-of: 2026-11-10 Beijing; exact priority window 2025-11-10–2026-11-10, plus unlimited-date architectural backfill. Read-only; no model run. sources_reviewed = 117 requested result slots across 17 Exa queries, NOT 117 pages deeply read. 117 result URLs returned, 103 unique. Exa authentication succeeded.

## Main finding

There ARE missed ecosystem developments. Strongest newly identified actionable branch is Confucius4-R2T2, not another generic Qwen checkpoint. But no source establishes a new winner for M1 Pro 32GiB, ~2.5GiB combined target, live Japanese→Simplified Chinese, irreversible output. Distinguish training improvements, source/target language directions, audio ingestion causality and output commitment. Native audio streaming alone does not imply irreversible text; irreversible text alone does not imply efficient cached audio encoding.

## Candidate matrix

| Name / checkpoint / date | Novelty and real stream definition | Language/direction and Japanese evidence | Parameters / residency | Mac implementation | Weights / code / license | Next step |
|---|---|---|---|---|---|---|
| **Confucius4-R2T2**; netease-youdao/Confucius4-R2T2; paper 2026-11-04 | Qwen3-ASR1.7B post-trained on LSP+Wait with SFT/GRPO, frozen encoder/projector; 80ms–2s audio chunks; bidirectional encoder on received window, not causalized; encoder8s window caching+decoderKV+Wait stopping+bounded context. Public finalized-prefix API no visible revisions, U=1 hidden provisional token | JA ASR transfer measured: FLEURS CER8.66(U1)/10.15(U0) at160ms; base160ms36.07; base2s/U5(revisable)7.23; baseoffline5.42. Training Chinese+English1500h, not Japanese-specialist. No speech translation | Actual ~2B incl300M encoder. Official GGUF Q4_K_M1.0GiB + mmprojQ8 .3GiB; Q8decoder1.7GiB+proj .3GiB; file sizes NOT measured working set | Official pure stream_llama and hybrid routes, but docs ship CUDA CPython3.12 artifacts; no Mac/Metal benchmark found; cannot equate GGUF with working Mac integration | [HF](https://huggingface.co/netease-youdao/Confucius4-R2T2), [GGUF](https://huggingface.co/netease-youdao/Confucius4-R2T2-GGUF), [code](https://github.com/netease-youdao/Confucius4-R2T2), [paper](https://arxiv.org/html/2611.06043v1). CodeApache2; weightsNetEase Model Use License (custom) | **High-priority small feasibility gate**: exact llama runtime Mac/quantized Japanese replay, then same irreversible semantic benchmark; not claim winner. API must consume second finalized-prefix return, NOT state.text(current hypothesis) |
| **CSSEL-P2P**; paper2607.13158;2026-07-14 | Phi4MM with replacement streaming Conformer12×1280; 800ms block+7-block left context; 4s cumulative speech+hard prefix anchoring; hidden last5tokens rewind, not user-visible revision. Teacher o3mini plans bounded-wait P2P target; student only speech prefixes at inference | Actual JA→EN and ZH→JA table, **no JA→ZH evaluation**. Inhouse avgCOMETKiwi68.96→70.50, AL2.19→2.34s; not same test as user. P2P data only X↔EN | Phi4MM backbone (multi-billion), full trained total not disclosed in reviewed paper; 43kh offlineST +200h each direction P2P; no measured residency | No Mac implementation found | [Original](https://arxiv.org/html/2607.13158). No official checkpoint/repo release found in paper or exact release query; base Phi4MM speechLoRA is NOT this model; cannot assume derivative weight license | **Research recipe**, not current plug-in. Closely matches irreversibility goal, but substantial training/data and language-direction gap |
| **Hikari-medium**; sbintuitions/hikari-medium;paper2026-03-12 | Causal Whisper-style encoder+WAIT probability+decoder time dilation+delay recovery SFT; chunk80ms. HF limitation explicitly says output may be revised; must inspect commit protocol | Released card EN→JA/DE/RU and **JA→EN**, EnglishASR only. Paper primarily evaluates EN targets. **No JA→ZH, no JapaneseASR** | ~0.8B, ~1.5GBfp16 weights plus working memory | **Real official MLX**. M4Air EnglishASR exact context100 RTF2.6; approximate refreshstride16 starts.7→1.0–1.3 after5min. M1Pro not measured; approximate limited validation | [HF](https://huggingface.co/sbintuitions/hikari-medium), [code](https://github.com/sbintuitions/hikari), [paper](https://arxiv.org/abs/2603.11578), MIT | Ecosystem/nativeMac breakthrough, **not direct replacement**; JA→EN→ZH bridge adds semantic bottleneck/MT and residency. Do not silently map English results to Japanese |
| **Index-Echo-S2ST-9B**; v4.1 export2026-10-15 | Frozen AuT+IndexTranslate decoder+Hidden2CV~30M+CosyVoice3. Clip model, stream=False, chunk=True explicitly unsupported. Different from base independent S2TT release | **Actual released JA→ZH** among12directions. ja→zh CER.056 is TTS readback compared to own translator output, NOT independent translation quality | 9B is decoder only, more total components; no low-residency proof | CUDA tested path; noMac | [S2ST HF](https://huggingface.co/IndexTeam/Index-Echo-S2ST-9B). Bundled code/weights. License not established from truncated reviewed S2STcard; independent S2TTHF saysApache2 but do not transfer automatically | Genuine open JA→ZH ecosystem availability, **exclude as live2.5GiB solution** |
| **Index-Echo-S2TT-9B**;2026-09 report | Audio/video windows with VAD, sentence timestamps, context/terminology; not native unbounded streaming | Public infer.py only **Chinese→English/Japanese/Spanish**, despite textfoundation150languages and broader report. S2ST v4.1 direction set does not transfer | 9Bdecoder+audio | CUDA noMac verified | [S2TT HF](https://huggingface.co/IndexTeam/Index-Echo-S2TT-9B);Apache2 | Exclude current directJapanese→Chinese deployment |
| **Granite-speech-4.1-2b base**;2026-04-29 | Dualhead character/BPECTC+posterior frame pooling+keyword bias. Encoder4s blockattention; examples whole-wave generate. “online” is full-audio HTTP request, NOT continuously ingested audio. NAR/Plus cannot inherit baseJapanese | Base supportsJAASR; AST to/fromEnglish plusEN→Mandarin. No documentedJA→ZH. JA trainingReazon3kh+YODAS1.4kh+TTS9.6kh+CV | Actual2B; predecessor called1B only baseLLM size. Residency not measured | Official **mlx-audio≥.4.1 + macOS llama.cpp** support on modelcard, real implementation but not causal session proof | [HF](https://huggingface.co/ibm-granite/granite-speech-4.1-2b), [GGUF](https://huggingface.co/ibm-granite/granite-speech-4.1-2b-GGUF),Apache2 | Untested largerASR+context branch, NOT Granite5Turbo470M prior test; lowerpriority than native123M/R2T2 |
| **TC-BiMamba**;2026EMNLPFindings330 | TransChunk training rearranges local backward scan while chronological crosschunkSSM state prevents futurechunkleak; constant-sizeencoderCTCstate; dynamiclatency | AISHELL1/2+LibriSpeech only; noJapanese | small47–48M; larger variant; noMacresidency | NoMacimplementation found | [Originalpaper](https://aclanthology.org/2026.findings-emnlp.330.pdf); noreleasedJAweights found | Architectural advance, needsJapanese training |
| **LC-BiMamba**;Interspeech2025 | Chunkwise backwardSSM reset;attentionfree encoder;AdaALSD C++ CPU decoder | **EnglishTEDLIUM2+LibriSpeech only**, authorsNTT Japan does NOT meanJapanese evaluation | encoder~108M+HATdecoder4.3/10M; algorithmiclatencyavg300ms at15×40mschunks | XeonCPU C++benchmark, noMac | [Originalfullpaper](https://www.isca-archive.org/interspeech_2025/moriya25_interspeech.pdf); noJAcheckpoint/code release identified | Architectural research, not overlookedJapanese dropin |
| **CHAT**;2602.24245;2026-02-27 | Chunkwise crossattention transducerjoiner relaxes withinchunkalignment; 960ms chunks6pastchunks. up1.69× inference vsRNNT sameencoder; no claim targethardware | ASR EnglishGerman; ASTEN→DE/ZH/CA; **noJapanese** | ~110MFastConformerLarge class, totalnotexplicit | NeMoCUDA experiments; noMac | [Originalpaper](https://arxiv.org/html/2602.24245); noJapanesecheckpoint found | NeedsJapanese training; not budget proof |
| **MLMA / Mamba-ASR / UMA-ASR**;2024–2026 | BiMambaConformerCTC/streamUMA token-trigger architectures; don't infer all BiMamba nativecausal | MLMA6Europeanlanguages; UMAAISHELL/HKUSTChinese; ConMambaLibriSpeechEnglish. NoJapanese releasedweights verified | Smallresearchscales; noMac residency | mamba-ssm/causalconv kernels, noMacverified | [MLMA](https://github.com/mnabihali/MLMA), [MambaASR](https://github.com/xi-j/Mamba-ASR)(GPL3,inference/checkpointsLater), [UMA](https://github.com/Audio-WestlakeU/UMA-ASR) | No immediateJAmodel |
| **SimulMEGA**;2509.01200v2 | Training-onlyMoErefiner distills read/write policy;500MS2TT. Runtimeoriginal architecture; code starts fromWhisper and requiresfine-tuning | EN,ZH,DE,ES,FR,IT, **notJapanese** |500MnoMacmeasurement | NoMacreleaseverified | [paper](https://arxiv.org/html/2509.01200v2), [officialcode](https://github.com/nethermanpro/simulmega); nofinishedJAweightlink | Research,noJapaneseplug-in |
| **RASST**;2601.22777v2 | Partial-audio multi-scale speech/text termretrieval+train translator when toinsertterm; code/weights availability notverified | EN→ZH/DE/JA; **noJA source**. Claimed40%terminologyaccuracygain is notJapaneseresult | Retriever+speechLLM, noMactotal | NoMacverified | [Original](https://arxiv.org/html/2601.22777v2) | Usefultraining/semanticinterfaceidea, notzerotrainingJapanese switch |
| **qfuxa/Qwen3-ASR-0.6B-streaming**;2026-06/07 | Real1.92sblockcausalKV;15sleftcache; changedtoweronly;appendonlyruntime, ~3×lesscompute | **English-only explicitly**; multilingualdistillationregressedZH11.4→85.7CER,FR7.8→37.9WER. Do notinheritQwenJapanese | tower746MBfp32 +baseLM; noquantizedpathverified | OfficialHF/MPS+vllmMetal measurements, notM1ProJapanese | [HF](https://huggingface.co/qfuxa/qwen3-asr-0.6b-streaming), [runtime](https://github.com/QuentinFuxa/Qwen3-ASR-causal),Apache2 | Deployablearchitecture,noJapanese. Cheapdistillationrecipe potential but needstraining |

## Already covered / not new

Parent confirmed Haas-ja-v1.0 and PengChengStarling8language streaming2025-02-10 have actual failed user-material tests. Do not count them as new, despite reappearing in Exa. Haasofficialfullcontextencoder, KDApredictornetwork not anSSMstreamingencoder; nativecodeCUDA/Triton unsupportedCPU/Mac does not negate existing userONNXconversion. Localoptimizationresults identifiesquantizedONNX+sherpa2.0.1 but notHFrevision/hash; exactweightidentity cannot be re-proven from this note. fujielab66MZipformer and84.5MCascade are interesting but currentrepo explicitly marksHFmodelsprivate contactonly; Japanesebranch covers. Japanese123McacheawareRNNT andKodama140M coveredbyJapanesebranch. Voxtral/Nemotron/Qwenbase/WhisperAlignAtt/Hibiki/etc known not reintroduced.

## Context / uncertainty interface

Released low-overhead context code is real but bounded in applicability. sherpa-onnx hotwords builds tokenAho-Corasick automaton and requires modified_beam_search, supports transducerpaths, not everyCTC/modeltype. Current Nemotronstreamingissue3572 documents lackofmodifiedbeam inNeMo-specificonlinebackend; cannot transplantZipformerhotwordAPI blindly. Rawhotwordhints must originate permittedusercontext, not futureaudio/OCR/subtitletrack. Englishpropernounrecoveries inissue are anecdotal notJapanesevalidated. No new ready-made Japanese Nbest/lattice→ZHsemanticinterface checkpoint validated in this sweep.

Sources: https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html ; https://github.com/k2-fsa/sherpa-onnx/issues/3572 ; https://github.com/k2-fsa/sherpa-onnx/blob/c6691594/sherpa-onnx/python/sherpa_onnx/online_recognizer.py

## Query ledger

### arch — numResults=10

Query: category:research paper streaming speech recognition 2026 2025 block causal encoder state space Mamba RWKV transducer CIF open source model weights multilingual Japanese

- https://arxiv.org/html/2602.11298v2
- https://aclanthology.org/2026.findings-emnlp.330.pdf
- https://arxiv.org/html/2510.18684v2
- https://www.isca-archive.org/interspeech_2025/moriya25_interspeech.html
- https://arxiv.org/html/2506.19761
- https://arxiv.org/html/2606.24169
- https://arxiv.org/html/2502.11123v3
- https://arxiv.org/html/2602.24245v1
- https://interspeech-2026-wiki.vercel.app/papers/yang26r_interspeech/markdown.md
- https://arxiv.org/abs/2501.02832v3

### found — numResults=10

Query: multilingual speech foundation model open weights Japanese streaming ASR speech translation new release 2026 2025 compact 1B 500M

- https://huggingface.co/ibm-granite/granite-4.0-1b-speech
- https://huggingface.co/blog/ibm-granite/granite-4-speech
- https://huggingface.co/ibm-granite/granite-speech-4.1-2b
- https://gigazine.net/gsc_news/en/20261102-haas-ja-v1-asr/
- https://huggingface.co/nvidia/canary-1b-v2
- https://kyutai.org/blog/2026-02-12-hibiki-zero/
- https://huggingface.co/LiquidAI/LFM2.5-Audio-1.5B-JP
- https://huggingface.co/nvidia/nemotron-labs-unified-asr-0.6b
- https://huggingface.co/IndexTeam/Index-Echo-S2ST-9B
- https://arxiv.org/html/2509.14128

### simul — numResults=10

Query: category:research paper simultaneous speech translation Japanese Chinese 2025 2026 open source streaming speech to text prefix training

- https://arxiv.org/html/2509.01200v2
- https://aclanthology.org/2026.wmt-main.15/
- https://github.com/nethermanpro/simulmega
- https://aclanthology.org/2025.iwslt-1.39.pdf
- https://arxiv.org/pdf/2508.07781
- https://www.arxiv.org/pdf/2603.11578
- https://aclanthology.org/2026.iwslt-1.30.pdf
- https://arxiv.org/html/2610.02612
- https://lacuna.tiptreesystems.com/work/do-llms-need-architectural-changes-for-simultaneous-speech
- https://aclanthology.org/2025.iwslt-1.41.pdf

### partial — numResults=10

Query: category:research paper partial speech prefix training incremental speech translation teacher student distillation streaming multilingual 2026 2025

- https://arxiv.org/html/2607.13158
- https://arxiv.org/html/2610.02612
- https://aclanthology.org/2026.wmt-main.15/
- https://arxiv.org/html/2509.01200v2
- https://www.alphaxiv.org/abs/2506.18828
- https://arxiv.org/html/2608.03610
- https://arxiv.org/pdf/2507.07803
- https://arxiv.org/html/2601.22777v2
- https://arxiv.org/html/2609.05799v1
- https://arxiv.org/html/2610.32214

### compact — numResults=7

Query: A Compact Fully-Open Cache-Aware Streaming Model for Japanese ASR Yang 2026 Interspeech checkpoint code Huggingface

- https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open
- https://interspeech-2026-wiki.vercel.app/papers/yang26r_interspeech/markdown.md
- https://interspeech-2026-wiki.vercel.app/papers/yang26r_interspeech/
- https://huggingface.co/nvidia/stt_ka_fastconformer_hybrid_transducer_ctc_large_streaming_80ms_pc
- https://huggingface.co/nvidia/stt_en_fastconformer_hybrid_large_streaming_multi
- https://huggingface.co/moonshine-ai/moonshine-streaming-small-ja
- https://huggingface.co/ayousanz/kodama-ja-streaming-small

### cat — numResults=7

Query: Chunk-wise Attention Transducers for Fast and Accurate Streaming Speech-to-Text 2602.24245 official weights Japanese languages

- https://arxiv.org/abs/2602.24245
- https://arxiv.org/html/2602.24245
- https://arxiv.org/pdf/2602.24245
- https://www.alphaxiv.org/abs/2602.24245
- https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open
- https://doi.org/10.1109/icassp55912.2026.11460802
- https://arxiv.org/pdf/2602.24245v1/fastconformer_transducer_bpe_streaming.yaml

### cif — numResults=7

Query: Japanese streaming ASR continuous integrate and fire transducer 2026 2025 open checkpoint Hugging Face WeNet Zipformer Squeezeformer

- https://github.com/fujielab/fujielab-asr
- https://huggingface.co/streaming-ja-asr-anon/parakeet-streaming-ja-fully-open
- https://huggingface.co/ayousanz/kodama-ja-streaming-small
- https://huggingface.co/fujie/espnet_asr_cejc_pron_aux_cbs_transducer_081616_hop132
- https://huggingface.co/reazon-research/japanese-zipformer-base-k2-rs35kh
- https://csukuangfj.github.io/sherpa/onnx/pretrained_models/online-transducer/index.html
- https://huggingface.co/csukuangfj/sherpa-onnx-streaming-zipformer-ar_en_id_ja_ru_th_vi_zh-2025-02-10/commit/8248322d9167a408b966f37fbc75e9f4afc70d6f

### interface — numResults=7

Query: category:research paper streaming ASR translation N-best lattice semantic interface Japanese contextual biasing 2026 2025 open source

- https://arxiv.org/html/2508.13358
- https://github.com/owaski/iwslt-2026-baselines
- https://ar5iv.labs.arxiv.org/html/2603.11578
- https://ojs.aaai.org/index.php/AAAI/article/download/40733/44694
- https://arxiv.org/html/2512.17648
- https://doi.org/10.1109/apsipaasc63619.2025.10848752
- https://aclanthology.org/2005.iwslt-1.3.pdf

### cssel-release — numResults=5

Query: CSSEL-P2P Junkun Chen Microsoft Phi-4-MM 2607.13158 model weights github release Japanese Chinese

- https://arxiv.org/html/2607.13158
- https://huggingface.co/microsoft/Phi-4-multimodal-instruct/blob/main/data_summary_card.md
- https://www.alphaxiv.org/abs/2607.13158
- https://huggingface.co/microsoft/Phi-4-multimodal-instruct/tree/2258faa22fbe042d3c73b0545fb9f1feaf59ab51/speech-lora
- https://huggingface.co/microsoft/Phi-4-multimodal-instruct/raw/main/modeling_phi4mm.py

### ssm-release — numResults=5

Query: TC-BiMamba LC-BiMamba Mamba-ASR Japanese pretrained model checkpoint github streaming

- https://github.com/xi-j/Mamba-ASR
- https://aclanthology.org/2026.findings-emnlp.330/
- https://github.com/xi-j/Mamba-ASR/issues/7
- https://github.com/Audio-WestlakeU/UMA-ASR
- https://www.isca-archive.org/interspeech_2025/moriya25_interspeech.html

### hikari-release — numResults=5

Query: Hikari Streaming Translation Transcription Speech to Text Causal Alignment SoftBank Intuitions 2603.11578 Huggingface github

- https://huggingface.co/sbintuitions/hikari-medium
- https://arxiv.org/abs/2603.11578
- https://huggingface.co/papers/2603.11578
- https://huggingface.co/cstr/hikari-medium-GGUF
- https://huggingface.co/sbintuitions

### ja-stream-more — numResults=5

Query: Japanese native streaming speech recognition model open source 2026 causal checkpoint

- https://huggingface.co/moonshine-ai/moonshine-streaming-tiny-ja
- https://huggingface.co/moonshine-ai/moonshine-streaming-small-ja
- https://huggingface.co/sbintuitions/hikari-medium
- https://github.com/QUENTINFUXA/WHISPERLIVEKIT
- https://arxiv.org/html/2609.07549v2

### context-code — numResults=5

Query: streaming speech recognition contextual biasing low memory Japanese hotword FST transducer sherpa onnx 2026 2025 implementation

- https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html
- https://github.com/k2-fsa/sherpa-onnx/issues/3572
- https://github.com/k2-fsa/sherpa-onnx/blob/c6691594/sherpa-onnx/python/sherpa_onnx/online_recognizer.py
- https://github.com/k2-fsa/sherpa-onnx/issues/1910
- https://k2-fsa.github.io/sherpa/onnx/c-api/html/online_asr.html

### simul-ja-more — numResults=5

Query: simultaneous speech translation Japanese Chinese source streaming open source weights StreamUni StreamSpeech SimulMask DiSeg 2026

- https://github.com/ictnlp/StreamSpeech
- https://github.com/topics/simultaneous-translation
- https://github.com/LeiLiLab/InfiniSST
- https://ictnlp.github.io/StreamSpeech-site/
- https://aclanthology.org/2026.emnlp-main.1859.pdf

### prefix-small — numResults=5

Query: streaming translation stable prefix training open source weights Japanese Chinese 2026 small model no revision

- https://aclanthology.org/2026.wmt-main.15.pdf
- https://github.com/netease-youdao/Confucius4-R2T2
- https://paperlayer.ai/abs/2610.02612v1
- https://arxiv.org/html/2611.06043v1
- https://kyutai.org/blog/2026-02-12-hibiki-zero/

## Quality / caveats

Primary original papers plus official HF/GitHub prioritized. Vercel Interspeech wiki, roundupGIGAZINE and aggregator pages used only to discoveroriginal sources, not final language/deployment proof. Noabsence claim is absolute: ‘not found’ means not verified in these scopedExa queries. Fetch failures onR2T2 llama-specificREADME/rawpath/license were CRAWL_NOT_FOUND, not authentication; relied on mainrepo/HF+GGUF for facts and markedMacunknown. No benchmark executed. Model-file sizes are not actual unified-memory footprint or combinedASR+HyMTbudget.

## R2T2 adjacent-candidate followup

| Candidate | Genuine training vs wrapper | Japanese / direct goal | Mac / size / license | Decision |
|---|---|---|---|---|
| X-ASR-zh-en; firstrepo2026-05-23 | Real1million-hour ChineseEnglish Zipformertransducer, offline/streamunified160/480/960/1920ms; notwindowwrapper | **OnlyZH/EN**, nextroadmapThai/Indonesian/Vietnamese; noJapaneseproof | Realofficial VibeXASR signednotarizedmacOS15universal app;CPUONNX inferenceexample. Exactparameter/workingmemorynotestablished;Apache2 | **Languageexclude**, notmissedJAcompetitor |
| WhisperRT / MLSpeech/WhisperRT-Streaming;paper2508.12301 revised2026-04-05 | RealLoRAcausalWhisperencoder+decoderpartialcontextalignment; fine-tunedcheckpoints, notAlignAttwrapper; confidencebasedrevision remains | base/small/large-v2 ENonly; onlymultilinguallarge-v2_300ms supportsEN/FR/ES/DE/PT, **notJapanese** | CUDA12.4tested;CPUfallbackexample, noMacverification;standardWhisperbase/small/large parameterclasses notnewtotalclaim. OriginalcodeCCBYNC4,WhisperportionMIT | Languageexclude. CannotinferJAfromoriginalWhisper99languages |
| GPT-Live-Transcribe | GenuineOpenAIclosedAPI lowlatencytranscriptionmodel, notR2T2authorswrapper | Multilingualhints, butnoJapaneseindependentmetricreviewedhere;irrelevanttolocalweightsconstraint | Endpoint/realtime/transcription_sessions. Noopenlocalcheckpoint / Macinference | Cloudexclude |

Primaryoriginalsources: https://github.com/Gilgamesh-J/X-ASR ; https://huggingface.co/GilgameshWind/X-ASR-zh-en ; https://github.com/tomer9080/WhisperRT-Streaming ; https://huggingface.co/MLSpeech/WhisperRT-Streaming ; https://arxiv.org/html/2508.12301 ; https://developers.openai.com/api/docs/models/gpt-live-transcribe

### r2t2-adjacent — numResults=7

Query: X-ASR Guo 2026 streaming Zipformer WhisperRT Krichli 2026 causal Whisper open model Japanese GitHub

- https://github.com/tomer9080/WhisperRT-Streaming
- https://github.com/Gilgamesh-J/X-ASR
- https://github.com/k2-fsa/sherpa-onnx/releases
- https://huggingface.co/GilgameshWind/X-ASR-zh-en
- https://github.com/k2-fsa/sherpa-onnx/releases/tag/v1.13.3
- https://github.com/fujielab/fujielab-asr
- https://k2-fsa.github.io/sherpa/onnx/pretrained_models/online-transducer/zipformer-transducer-models.html

### gpt-live-adjacent — numResults=7

Query: GPT-Live-Transcribe open source model weights streaming Japanese WhisperRT causal encoder 2026

- https://developers.openai.com/api/docs/models/gpt-live-transcribe
- https://github.com/tomer9080/WhisperRT-Streaming
- https://huggingface.co/MLSpeech/WhisperRT-Streaming
- https://developers.openai.com/api/docs/guides/realtime-transcription
- https://arxiv.org/html/2508.12301
- https://developers.openai.com/cookbook/examples/migrating_from_whisper_to_gpt_transcribe
- https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/


