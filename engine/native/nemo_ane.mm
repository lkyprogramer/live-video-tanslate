// Native CoreML ANE FastConformer Encoder + Accelerate AMX RNNT Decoder.
// Compile:
//   clang++ -O3 -std=c++20 -fobjc-arc -shared -framework Foundation -framework CoreML -framework Accelerate \
//     engine/native/nemo_ane.mm -o .model-cache/nemo-ane/libnemo_ane.dylib

#import <Accelerate/Accelerate.h>
#import <CoreML/CoreML.h>
#import <Foundation/Foundation.h>

#include <dispatch/dispatch.h>
#include <mach/mach.h>
#include <sys/types.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cmath>
#include <condition_variable>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <memory>
#include <mutex>
#include <stdexcept>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

extern "C" {

typedef int (*nemo_ane_cancel_cb)(void* user_data);

typedef struct {
  int32_t token_id;
  int32_t frame;
} NemoAneToken;

typedef struct {
  double frontend_ms;
  double predict_ms;
  double output_copy_ms;
  double decode_ms;
  double total_ms;
  int32_t bucket_mel;
  int32_t mel_frames;
  int32_t enc_frames;
} NemoAneTimings;

void* nemo_ane_create(const char* model_dir, const char* gguf_path, char* err_buf, size_t err_len);
void nemo_ane_destroy(void* handle);
double nemo_ane_wired_mb(void);
int nemo_ane_retired(void* handle);
uint64_t nemo_ane_eval_count(void* handle);
int nemo_ane_last_timings(void* handle, NemoAneTimings* out);
int nemo_ane_transcribe(
    void* handle,
    const float* samples,
    int32_t n_samples,
    int32_t pad_samples,
    int32_t use_beam,
    nemo_ane_cancel_cb cancel_cb,
    void* user_data,
    char* out_text,
    size_t out_text_cap,
    NemoAneToken* out_tokens,
    int32_t max_tokens,
    int32_t* out_n_tokens,
    char* out_token_texts,
    size_t out_token_texts_cap);
int nemo_ane_detokenize(
    void* handle,
    const int32_t* ids,
    int32_t n_ids,
    char* out_text,
    size_t out_text_cap);

}  // extern "C"

namespace {

inline double now_ms() {
  using namespace std::chrono;
  return duration<double, std::milli>(steady_clock::now().time_since_epoch()).count();
}

bool is_non_speech_codepoint(uint32_t cp) {
  if (cp <= 0x20) return true;
  if ((cp >= 0x21 && cp <= 0x2F) || (cp >= 0x3A && cp <= 0x40) ||
      (cp >= 0x5B && cp <= 0x60) || (cp >= 0x7B && cp <= 0x7E)) {
    return true;
  }
  switch (cp) {
    case 0x3000:  // 　
    case 0x3001:  // 、
    case 0x3002:  // 。
    case 0xFF1F:  // ？
    case 0xFF01:  // ！
    case 0xFF0C:  // ，
    case 0xFF0E:  // ．
    case 0x300C:  // 「
    case 0x300D:  // 」
    case 0x300E:  // 『
    case 0x300F:  // 』
    case 0xFF08:  // （
    case 0xFF09:  // ）
    case 0x30FB:  // ・
    case 0x2026:  // …
    case 0x301C:  // 〜
    case 0xFF5E:  // ～
    case 0x30FC:  // ー
      return true;
    default:
      return false;
  }
}

int count_speech_codepoints(const std::string& s) {
  int count = 0;
  const size_t n = s.size();
  size_t i = 0;
  while (i < n) {
    uint8_t c = static_cast<uint8_t>(s[i]);
    uint32_t cp = 0;
    size_t step = 1;
    if ((c & 0x80u) == 0) {
      cp = c;
    } else if ((c & 0xE0u) == 0xC0u && i + 1 < n) {
      cp = ((uint32_t)(c & 0x1Fu) << 6) | (uint32_t)(s[i + 1] & 0x3Fu);
      step = 2;
    } else if ((c & 0xF0u) == 0xE0u && i + 2 < n) {
      cp = ((uint32_t)(c & 0x0Fu) << 12) | ((uint32_t)(s[i + 1] & 0x3Fu) << 6) |
           (uint32_t)(s[i + 2] & 0x3Fu);
      step = 3;
    } else if ((c & 0xF8u) == 0xF0u && i + 3 < n) {
      cp = ((uint32_t)(c & 0x07u) << 18) | ((uint32_t)(s[i + 1] & 0x3Fu) << 12) |
           ((uint32_t)(s[i + 2] & 0x3Fu) << 6) | (uint32_t)(s[i + 3] & 0x3Fu);
      step = 4;
    } else {
      i++;
      continue;
    }
    i += step;
    if (!is_non_speech_codepoint(cp)) count++;
  }
  return count;
}

float fp16_to_f32(uint16_t h) {
  uint32_t sign = (uint32_t)(h & 0x8000u) << 16;
  uint32_t exp = (h >> 10) & 0x1Fu;
  uint32_t mant = h & 0x03FFu;
  uint32_t bits = 0;
  if (exp == 0) {
    if (mant == 0) {
      bits = sign;
    } else {
      exp = 1;
      while ((mant & 0x0400u) == 0) {
        mant <<= 1;
        exp--;
      }
      mant &= 0x03FFu;
      bits = sign | ((exp + 127 - 15) << 23) | (mant << 13);
    }
  } else if (exp == 31) {
    bits = sign | 0x7F800000u | (mant << 13);
  } else {
    bits = sign | ((exp + 127 - 15) << 23) | (mant << 13);
  }
  float out = 0.0f;
  std::memcpy(&out, &bits, sizeof(out));
  return out;
}

// ---------------------------------------------------------------------------
// Minimal GGUF Reader for Frontend, Decoder & Tokenizer
// ---------------------------------------------------------------------------
struct GgufReader {
  std::ifstream f;
  uint32_t align = 32;
  uint64_t data_start = 0;

  // Metadata
  int n_fft = 512;
  int hop = 160;
  int n_mels = 80;
  float preemph = 0.97f;
  float log_guard = 5.960464477539063e-8f;
  float std_guard = 1e-5f;
  int blank_id = 3000;
  int beam_size = 4;
  bool score_norm = true;
  float max_target_ratio = 1.0f;
  int pred_layers = 2;
  int unk_id = 0;
  std::string unk_surface = " \xe2\x81\x87 ";
  bool strip_leading_space = true;
  std::vector<std::string> vocab;
  std::vector<std::string> punctuation;

  struct TensorInfo {
    std::vector<uint64_t> dims;
    uint32_t ttype;
    uint64_t off;
  };
  std::unordered_map<std::string, TensorInfo> tensors;

  template <typename T>
  T read_pod() {
    T v{};
    f.read(reinterpret_cast<char*>(&v), sizeof(T));
    if (!f) throw std::runtime_error("GGUF unexpected EOF");
    return v;
  }

  std::string read_str() {
    uint64_t n = read_pod<uint64_t>();
    std::string s(n, '\0');
    if (n > 0) f.read(s.data(), n);
    if (!f) throw std::runtime_error("GGUF unexpected EOF reading string");
    return s;
  }

  void skip_val(uint32_t t) {
    if (t == 0 || t == 1 || t == 7) { f.seekg(1, std::ios::cur); return; }
    if (t == 2 || t == 3) { f.seekg(2, std::ios::cur); return; }
    if (t == 4 || t == 5 || t == 6) { f.seekg(4, std::ios::cur); return; }
    if (t == 8) { (void)read_str(); return; }
    if (t == 9) {
      uint32_t et = read_pod<uint32_t>();
      uint64_t cnt = read_pod<uint64_t>();
      for (uint64_t i = 0; i < cnt; ++i) skip_val(et);
      return;
    }
    if (t == 10 || t == 11 || t == 12) { f.seekg(8, std::ios::cur); return; }
    throw std::runtime_error("Unsupported GGUF value type");
  }

  explicit GgufReader(const char* path) : f(path, std::ios::binary) {
    if (!f) throw std::runtime_error(std::string("Cannot open GGUF: ") + path);
    char magic[4];
    f.read(magic, 4);
    if (std::memcmp(magic, "GGUF", 4) != 0) throw std::runtime_error("Invalid GGUF magic");
    (void)read_pod<uint32_t>();  // ver
    uint64_t ntensors = read_pod<uint64_t>();
    uint64_t nkv = read_pod<uint64_t>();

    for (uint64_t i = 0; i < nkv; ++i) {
      std::string k = read_str();
      uint32_t t = read_pod<uint32_t>();
      if (k == "general.alignment" && t == 4) { align = read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.frontend.n_fft" && t == 4) { n_fft = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.frontend.hop_length" && t == 4) { hop = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.frontend.n_mels" && t == 4) { n_mels = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.frontend.preemphasis" && t == 6) { preemph = read_pod<float>(); continue; }
      if (k == "fastconformer.frontend.log_guard" && t == 6) { log_guard = read_pod<float>(); continue; }
      if (k == "fastconformer.frontend.std_guard" && t == 6) { std_guard = read_pod<float>(); continue; }
      if (k == "fastconformer.decoder.blank_id" && t == 4) { blank_id = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.decoder.prediction_layers" && t == 4) { pred_layers = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.decoder.rnnt.beam_size" && t == 4) { beam_size = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.decoder.rnnt.score_norm" && (t == 7 || t == 0)) { score_norm = read_pod<uint8_t>() != 0; continue; }
      if (k == "fastconformer.decoder.rnnt.max_target_ratio" && t == 6) { max_target_ratio = read_pod<float>(); continue; }
      if (k == "fastconformer.tokenizer.unknown_id" && t == 4) { unk_id = (int)read_pod<uint32_t>(); continue; }
      if (k == "fastconformer.tokenizer.unknown_surface" && t == 8) { unk_surface = read_str(); continue; }
      if (k == "fastconformer.tokenizer.strip_leading_space" && (t == 7 || t == 0)) { strip_leading_space = read_pod<uint8_t>() != 0; continue; }
      if (k == "fastconformer.tokenizer.tokens" && t == 9) {
        uint32_t et = read_pod<uint32_t>();
        uint64_t cnt = read_pod<uint64_t>();
        if (et != 8) throw std::runtime_error("Expected string array for tokens");
        vocab.resize(cnt);
        for (uint64_t j = 0; j < cnt; ++j) vocab[j] = read_str();
        continue;
      }
      if (k == "fastconformer.tokenizer.punctuation" && t == 9) {
        uint32_t et = read_pod<uint32_t>();
        uint64_t cnt = read_pod<uint64_t>();
        if (et != 8) throw std::runtime_error("Expected string array for punctuation");
        punctuation.resize(cnt);
        for (uint64_t j = 0; j < cnt; ++j) punctuation[j] = read_str();
        continue;
      }
      skip_val(t);
    }

    for (uint64_t i = 0; i < ntensors; ++i) {
      std::string name = read_str();
      uint32_t ndim = read_pod<uint32_t>();
      TensorInfo ti;
      ti.dims.resize(ndim);
      for (uint32_t d = 0; d < ndim; ++d) ti.dims[d] = read_pod<uint64_t>();
      ti.ttype = read_pod<uint32_t>();
      ti.off = read_pod<uint64_t>();
      tensors.emplace(std::move(name), std::move(ti));
    }

    uint64_t pos = (uint64_t)f.tellg();
    data_start = ((pos + align - 1) / align) * align;
  }

  std::vector<float> load_f32(const std::string& name, std::vector<uint64_t>* out_shape_rowmajor = nullptr) {
    auto it = tensors.find(name);
    if (it == tensors.end()) throw std::runtime_error("Missing tensor: " + name);
    const auto& ti = it->second;
    if (out_shape_rowmajor) {
      out_shape_rowmajor->assign(ti.dims.rbegin(), ti.dims.rend());
    }
    uint64_t ne = 1;
    for (uint64_t d : ti.dims) ne *= d;
    f.seekg(data_start + ti.off, std::ios::beg);
    std::vector<float> out(ne);
    if (ti.ttype == 0) {
      f.read(reinterpret_cast<char*>(out.data()), ne * sizeof(float));
    } else if (ti.ttype == 8) {
      uint64_t nblk = ne / 32;
      std::vector<uint8_t> raw(nblk * 34);
      f.read(reinterpret_cast<char*>(raw.data()), raw.size());
      for (uint64_t b = 0; b < nblk; ++b) {
        const uint8_t* p = raw.data() + b * 34;
        uint16_t d16;
        std::memcpy(&d16, p, 2);
        float d = fp16_to_f32(d16);
        const int8_t* qs = reinterpret_cast<const int8_t*>(p + 2);
        float* dst = out.data() + b * 32;
        for (int k = 0; k < 32; ++k) dst[k] = d * (float)qs[k];
      }
    } else {
      throw std::runtime_error("Unsupported tensor type for " + name);
    }
    if (!f) throw std::runtime_error("Read failed for " + name);
    return out;
  }
};

// ---------------------------------------------------------------------------
// Frontend (vDSP FFT)
// ---------------------------------------------------------------------------
struct Frontend {
  int n_fft = 512;
  int hop = 160;
  int n_mels = 80;
  int bins = 257;
  float preemph = 0.97f;
  float log_guard = 5.960464477539063e-8f;
  float std_guard = 1e-5f;
  std::vector<float> window;      // [n_fft]
  std::vector<float> filterbank;  // [n_mels * bins]
  FFTSetup fft_setup = nullptr;
  int log2n = 9;

  void init(GgufReader& r) {
    n_fft = r.n_fft;
    hop = r.hop;
    n_mels = r.n_mels;
    bins = n_fft / 2 + 1;
    preemph = r.preemph;
    log_guard = r.log_guard;
    std_guard = r.std_guard;
    auto w = r.load_f32("frontend.window");
    window.assign(n_fft, 0.0f);
    int left = (n_fft - (int)w.size()) / 2;
    for (size_t i = 0; i < w.size(); ++i) window[left + i] = w[i];
    filterbank = r.load_f32("frontend.filterbank");  // row-major [80, 257]
    log2n = (int)std::lrint(std::log2((double)n_fft));
    fft_setup = vDSP_create_fftsetup(log2n, kFFTRadix2);
  }

  ~Frontend() {
    if (fft_setup) vDSP_destroy_fftsetup(fft_setup);
  }

  int compute(const float* samples, int32_t n_samples, std::vector<float>& out_mel) const {
    const int frames = n_samples / hop;
    if (frames < 2) return 0;

    std::vector<float> pre(n_samples);
    pre[0] = samples[0];
    for (int i = 1; i < n_samples; ++i) {
      pre[i] = samples[i] - preemph * samples[i - 1];
    }

    std::vector<float> realp(n_fft / 2);
    std::vector<float> imagp(n_fft / 2);
    DSPSplitComplex split{realp.data(), imagp.data()};
    std::vector<float> buf(n_fft);
    std::vector<float> power(bins);
    std::vector<double> mel_raw((size_t)frames * n_mels);
    const int half = n_fft / 2;

    for (int f = 0; f < frames; ++f) {
      const int start = f * hop - half;
      for (int k = 0; k < n_fft; ++k) {
        const int idx = start + k;
        buf[k] = (idx >= 0 && idx < n_samples) ? (pre[idx] * window[k]) : 0.0f;
      }
      vDSP_ctoz((const DSPComplex*)buf.data(), 2, &split, 1, n_fft / 2);
      vDSP_fft_zrip(fft_setup, &split, 1, log2n, kFFTDirection_Forward);
      // vDSP_fft_zrip scales by 2.0 compared to standard DFT
      const float inv4 = 0.25f;
      power[0] = split.realp[0] * split.realp[0] * inv4;
      power[half] = split.imagp[0] * split.imagp[0] * inv4;
      for (int k = 1; k < half; ++k) {
        power[k] = (split.realp[k] * split.realp[k] + split.imagp[k] * split.imagp[k]) * inv4;
      }
      for (int m = 0; m < n_mels; ++m) {
        const float* fb_row = filterbank.data() + (size_t)m * bins;
        float dot = 0.0f;
        vDSP_dotpr(fb_row, 1, power.data(), 1, &dot, bins);
        mel_raw[(size_t)f * n_mels + m] = std::log((double)dot + (double)log_guard);
      }
    }

    out_mel.resize((size_t)frames * n_mels);
    for (int m = 0; m < n_mels; ++m) {
      double mean = 0.0;
      for (int f = 0; f < frames; ++f) mean += mel_raw[(size_t)f * n_mels + m];
      mean /= (double)frames;
      double var = 0.0;
      for (int f = 0; f < frames; ++f) {
        double d = mel_raw[(size_t)f * n_mels + m] - mean;
        var += d * d;
      }
      double std_v = std::sqrt(var / (double)(frames - 1)) + (double)std_guard;
      for (int f = 0; f < frames; ++f) {
        out_mel[(size_t)f * n_mels + m] = (float)((mel_raw[(size_t)f * n_mels + m] - mean) / std_v);
      }
    }
    return frames;
  }
};

// ---------------------------------------------------------------------------
// Detokenizer (matches speech.cpp Detokenizer)
// ---------------------------------------------------------------------------
struct Detokenizer {
  std::vector<std::string> pieces;
  std::vector<std::string> punctuation;
  int unk_id = 0;
  std::string unk_surface;
  bool strip_leading_space = true;

  static void replace_all(std::string& s, const std::string& from, const std::string& to) {
    for (size_t at = s.find(from); at != std::string::npos; at = s.find(from, at + to.size())) {
      s.replace(at, from.size(), to);
    }
  }

  std::string text(const int32_t* ids, int32_t n) const {
    const std::string kSpace = "\xe2\x96\x81";
    std::string out;
    bool empty = true;
    for (int32_t i = 0; i < n; ++i) {
      int32_t tid = ids[i];
      if (tid < 0 || (size_t)tid >= pieces.size()) continue;
      std::string p = (tid == unk_id) ? unk_surface : pieces[tid];
      if (tid != unk_id) {
        if (strip_leading_space && empty && p.compare(0, kSpace.size(), kSpace) == 0) {
          p.erase(0, kSpace.size());
        }
        replace_all(p, kSpace, " ");
      }
      if (!p.empty()) empty = false;
      out += p;
    }
    // Strip ASCII spaces before punctuation marks
    for (const auto& p : punctuation) {
      if (p.empty()) continue;
      for (;;) {
        size_t pos = out.find(" " + p);
        if (pos == std::string::npos) break;
        out.erase(pos, 1);
      }
    }
    return out;
  }
};

// ---------------------------------------------------------------------------
// Accelerate AMX RNNT Decoder (greedy + ALSD beam=4)
// ---------------------------------------------------------------------------
struct RnntDecoder {
  int blank = 3000;
  int beam = 4;
  bool score_norm = true;
  float max_ratio = 1.0f;
  int layers = 2;
  int hidden = 640;
  int outputs = 3001;

  std::vector<float> embed;                // [3001, 640]
  std::vector<std::vector<float>> lstm_ih; // [2560, 640]
  std::vector<std::vector<float>> lstm_hh; // [2560, 640]
  std::vector<std::vector<float>> lstm_b;  // [2560]
  std::vector<float> joint_pred_w;         // [640, 640]
  std::vector<float> joint_pred_b;         // [640]
  std::vector<float> joint_out_w;          // [3001, 640]
  std::vector<float> joint_out_b;          // [3001]

  void init(GgufReader& r) {
    blank = r.blank_id;
    beam = std::min(r.beam_size, blank);
    score_norm = r.score_norm;
    max_ratio = r.max_target_ratio;
    layers = r.pred_layers;
    std::vector<uint64_t> shp;
    embed = r.load_f32("pred.embed.weight", &shp);
    hidden = (int)shp[1];
    outputs = (int)shp[0];
    lstm_ih.resize(layers);
    lstm_hh.resize(layers);
    lstm_b.resize(layers);
    for (int l = 0; l < layers; ++l) {
      std::string p = "pred.lstm." + std::to_string(l);
      lstm_ih[l] = r.load_f32(p + ".ih.weight");
      lstm_hh[l] = r.load_f32(p + ".hh.weight");
      lstm_b[l] = r.load_f32(p + ".bias");
    }
    joint_pred_w = r.load_f32("joint.pred.weight");
    joint_pred_b = r.load_f32("joint.pred.bias");
    joint_out_w = r.load_f32("joint.out.weight");
    joint_out_b = r.load_f32("joint.out.bias");
  }

  struct PredCacheEntry {
    std::vector<float> pred_proj;  // [hidden]
    std::vector<float> h;          // [layers * hidden]
    std::vector<float> c;          // [layers * hidden]
  };

  struct PredScratch {
    std::vector<float> x;
    std::vector<float> gates;
    std::vector<float> joint_h;
  };

  void pred_step(
      int32_t label,
      const PredCacheEntry* prev,
      PredCacheEntry& out,
      PredScratch& scratch) const {
    const int H = hidden;
    out.h.assign(layers * H, 0.0f);
    out.c.assign(layers * H, 0.0f);
    if (prev) {
      out.h = prev->h;
      out.c = prev->c;
    }
    std::memcpy(scratch.x.data(), embed.data() + (size_t)label * H, (size_t)H * sizeof(float));
    for (int l = 0; l < layers; ++l) {
      std::memcpy(scratch.gates.data(), lstm_b[l].data(), (size_t)4 * H * sizeof(float));
      cblas_sgemv(CblasRowMajor, CblasNoTrans, 4 * H, H, 1.0f, lstm_ih[l].data(), H, scratch.x.data(), 1, 1.0f, scratch.gates.data(), 1);
      cblas_sgemv(CblasRowMajor, CblasNoTrans, 4 * H, H, 1.0f, lstm_hh[l].data(), H, out.h.data() + l * H, 1, 1.0f, scratch.gates.data(), 1);
      float* hc = out.h.data() + l * H;
      float* cc = out.c.data() + l * H;
      for (int j = 0; j < H; ++j) {
        float ig = 1.0f / (1.0f + std::exp(-scratch.gates[j]));
        float fg = 1.0f / (1.0f + std::exp(-scratch.gates[H + j]));
        float gg = std::tanh(scratch.gates[2 * H + j]);
        float og = 1.0f / (1.0f + std::exp(-scratch.gates[3 * H + j]));
        cc[j] = fg * cc[j] + ig * gg;
        hc[j] = og * std::tanh(cc[j]);
        scratch.x[j] = hc[j];
      }
    }
    out.pred_proj = joint_pred_b;
    cblas_sgemv(CblasRowMajor, CblasNoTrans, H, H, 1.0f, joint_pred_w.data(), H, scratch.x.data(), 1, 1.0f, out.pred_proj.data(), 1);
  }

  void joint_logits(
      const float* enc_vec,
      const float* pred_proj,
      float* logits,
      std::vector<float>& h_buf) const {
    const int H = hidden;
    for (int j = 0; j < H; ++j) {
      float v = enc_vec[j] + pred_proj[j];
      h_buf[j] = v > 0.0f ? v : 0.0f;
    }
    std::memcpy(logits, joint_out_b.data(), (size_t)outputs * sizeof(float));
    cblas_sgemv(CblasRowMajor, CblasNoTrans, outputs, H, 1.0f, joint_out_w.data(), H, h_buf.data(), 1, 1.0f, logits, 1);
  }

  int decode_greedy(
      const float* enc_proj,
      int T,
      nemo_ane_cancel_cb cancel_cb,
      void* user_data,
      std::vector<NemoAneToken>& out_tokens) const {
    out_tokens.clear();
    PredScratch scratch{
        std::vector<float>(hidden),
        std::vector<float>((size_t)4 * hidden),
        std::vector<float>(hidden),
    };
    PredCacheEntry cur;
    pred_step(blank, nullptr, cur, scratch);
    std::vector<float> logits(outputs);
    for (int t = 0; t < T; ++t) {
      if (cancel_cb && cancel_cb(user_data)) return 1;
      const float* f = enc_proj + (size_t)t * hidden;
      for (int syms = 0; syms < 10; ++syms) {
        joint_logits(f, cur.pred_proj.data(), logits.data(), scratch.joint_h);
        int best = 0;
        float best_v = logits[0];
        for (int c = 1; c < outputs; ++c) {
          if (logits[c] > best_v) {
            best_v = logits[c];
            best = c;
          }
        }
        if (best == blank) break;
        out_tokens.push_back(NemoAneToken{best, t});
        PredCacheEntry next;
        pred_step(best, &cur, next, scratch);
        cur = std::move(next);
      }
    }
    return 0;
  }

  struct Hyp {
    std::vector<int32_t> labels;
    std::vector<int32_t> frames;
    double score = 0.0;
    std::shared_ptr<const PredCacheEntry> from;
  };

  static double logaddexp(double x, double y) {
    if (x == y) return x + std::log(2.0);
    double d = x - y;
    return d > 0.0 ? x + std::log1p(std::exp(-d)) : y + std::log1p(std::exp(d));
  }

  int decode_beam(
      const float* enc_proj,
      int T,
      nemo_ane_cancel_cb cancel_cb,
      void* user_data,
      std::vector<NemoAneToken>& out_tokens) const {
    out_tokens.clear();
    PredScratch scratch{
        std::vector<float>(hidden),
        std::vector<float>((size_t)4 * hidden),
        std::vector<float>(hidden),
    };
    const int max_labels = (int)((double)max_ratio * (double)T);
    std::map<std::vector<int32_t>, std::shared_ptr<const PredCacheEntry>> cache;
    std::vector<std::shared_ptr<Hyp>> beam_hyps{std::make_shared<Hyp>(Hyp{{blank}, {}, 0.0, nullptr})};
    std::shared_ptr<Hyp> finished;
    auto key_fn = [&](const Hyp& h) { return score_norm ? h.score / (double)h.labels.size() : h.score; };

    std::vector<float> logits(outputs);
    std::vector<double> logp(outputs);
    std::vector<int> tokens(blank);

    for (int i = 0; i < T + max_labels; ++i) {
      if (cancel_cb && cancel_cb(user_data)) return 1;
      std::vector<std::shared_ptr<Hyp>> active;
      std::vector<int> at;
      for (const auto& h : beam_hyps) {
        int t = i - (int)(h->labels.size() - 1);
        if (t <= T - 1) {
          active.push_back(h);
          at.push_back(t);
        }
      }
      if (active.empty()) break;

      std::vector<std::shared_ptr<Hyp>> expanded, finishing;
      for (size_t j = 0; j < active.size(); ++j) {
        const Hyp& h = *active[j];
        auto it = cache.find(h.labels);
        std::shared_ptr<const PredCacheEntry> pred;
        if (it != cache.end()) {
          pred = it->second;
        } else {
          auto p = std::make_shared<PredCacheEntry>();
          pred_step(h.labels.back(), h.from ? h.from.get() : nullptr, *p, scratch);
          cache[h.labels] = p;
          pred = std::move(p);
        }
        joint_logits(enc_proj + (size_t)at[j] * hidden, pred->pred_proj.data(), logits.data(), scratch.joint_h);
        double top = logits[0], sum = 0.0;
        for (int c = 1; c < outputs; ++c) top = std::max(top, (double)logits[c]);
        for (int c = 0; c < outputs; ++c) sum += std::exp((double)logits[c] - top);
        double lse = top + std::log(sum);
        for (int c = 0; c < outputs; ++c) logp[c] = (double)logits[c] - lse;

        auto b_hyp = std::make_shared<Hyp>(Hyp{h.labels, h.frames, h.score + logp[blank], h.from});
        expanded.push_back(b_hyp);
        if (at[j] == T - 1) finishing.push_back(b_hyp);

        for (int c = 0; c < blank; ++c) tokens[c] = c;
        std::partial_sort(tokens.begin(), tokens.begin() + beam, tokens.end(), [&](int a, int b) {
          return logp[a] > logp[b] || (logp[a] == logp[b] && a < b);
        });
        for (int k = 0; k < beam; ++k) {
          int tok = tokens[k];
          std::vector<int32_t> nl = h.labels;
          nl.push_back(tok);
          std::vector<int32_t> nf = h.frames;
          nf.push_back(at[j]);
          expanded.push_back(std::make_shared<Hyp>(Hyp{std::move(nl), std::move(nf), h.score + logp[tok], pred}));
        }
      }

      std::stable_sort(expanded.begin(), expanded.end(), [](const auto& a, const auto& b) {
        return a->score > b->score;
      });
      if ((int)expanded.size() > beam) expanded.resize(beam);
      beam_hyps.clear();
      for (const auto& h : expanded) {
        auto same = std::find_if(beam_hyps.begin(), beam_hyps.end(), [&](const auto& k) {
          return k->labels == h->labels;
        });
        if (same != beam_hyps.end()) {
          (*same)->score = logaddexp((*same)->score, h->score);
        } else {
          beam_hyps.push_back(h);
        }
      }
      std::map<std::vector<int32_t>, std::shared_ptr<const PredCacheEntry>> kept;
      for (const auto& h : beam_hyps) {
        auto it = cache.find(h->labels);
        if (it != cache.end()) kept.insert(*it);
      }
      cache.swap(kept);
      for (const auto& h : finishing) {
        if (!finished || key_fn(*h) > key_fn(*finished)) finished = h;
      }
    }

    const Hyp& best = finished ? *finished : *beam_hyps.front();
    for (size_t i = 1; i < best.labels.size(); ++i) {
      out_tokens.push_back(NemoAneToken{best.labels[i], best.frames[i - 1]});
    }
    return 0;
  }
};

// ---------------------------------------------------------------------------
// CoreML ANE Bucket + Watchdog + Keepalive Context
// ---------------------------------------------------------------------------
struct AneBucket {
  int t_mel = 0;
  int t_enc = 0;
  MLModel* model = nil;
  MLMultiArray* mel_arr = nil;
  MLMultiArray* am_arr = nil;
  MLMultiArray* cm_arr = nil;
};

struct KeepState {
  std::mutex m;
  std::condition_variable cv;
  bool stop = false;
  std::atomic<bool> done{false};
  std::timed_mutex eval_mu;
};

struct NemoAneContext {
  Frontend frontend;
  RnntDecoder decoder;
  Detokenizer detokenizer;
  AneBucket buckets[3];
  dispatch_queue_t ane_queue = nil;
  std::atomic<bool> retired{false};
  std::atomic<double> last_real_use_ms{0.0};
  std::atomic<double> last_keep_ms{0.0};
  std::atomic<uint64_t> eval_count{0};
  std::mutex timings_mu;
  NemoAneTimings last_timings{};
  std::shared_ptr<KeepState> keep_st = std::make_shared<KeepState>();
  std::thread keep_thread;

  static int subsampled_len(int frames) {
    for (int i = 0; i < 3; ++i) frames = (frames - 1) / 2 + 1;
    return frames;
  }

  bool eval_bucket(
      AneBucket& b,
      int valid_mel,
      int valid_enc,
      std::vector<float>& out_enc,
      bool is_real_req,
      double* out_predict_ms = nullptr,
      double* out_copy_ms = nullptr) {
    if (retired.load(std::memory_order_acquire)) return false;

    float* mel_ptr = (float*)b.mel_arr.dataPointer;
    float* am_ptr = (float*)b.am_arr.dataPointer;
    float* cm_ptr = (float*)b.cm_arr.dataPointer;
    (void)valid_mel;
    for (int t = 0; t < b.t_enc; ++t) {
      am_ptr[t] = (t < valid_enc) ? 0.0f : -1e4f;
      cm_ptr[t] = (t < valid_enc) ? 1.0f : 0.0f;
    }

    __block id<MLFeatureProvider> out_feat = nil;
    __block NSError* pred_err = nil;
    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    MLModel* mdl = b.model;
    MLMultiArray* m_arr = b.mel_arr;
    MLMultiArray* a_arr = b.am_arr;
    MLMultiArray* c_arr = b.cm_arr;

    eval_count.fetch_add(1, std::memory_order_relaxed);
    double t_pred0 = now_ms();
    dispatch_async(ane_queue, ^{
      @autoreleasepool {
        NSError* err = nil;
        NSDictionary* dict = @{
          @"mel": [MLFeatureValue featureValueWithMultiArray:m_arr],
          @"attn_mask": [MLFeatureValue featureValueWithMultiArray:a_arr],
          @"conv_mask": [MLFeatureValue featureValueWithMultiArray:c_arr],
        };
        MLDictionaryFeatureProvider* provider = [[MLDictionaryFeatureProvider alloc] initWithDictionary:dict error:&err];
        if (!provider) {
          pred_err = err;
          dispatch_semaphore_signal(sem);
          return;
        }
        out_feat = [mdl predictionFromFeatures:provider error:&err];
        pred_err = err;
        dispatch_semaphore_signal(sem);
      }
    });

    if (dispatch_semaphore_wait(sem, dispatch_time(DISPATCH_TIME_NOW, (int64_t)(2.0 * NSEC_PER_SEC))) != 0) {
      retired.store(true, std::memory_order_release);
      return false;
    }
    double pred_ms = now_ms() - t_pred0;
    if (!out_feat || pred_err) {
      retired.store(true, std::memory_order_release);
      return false;
    }

    MLMultiArray* enc_arr = [out_feat featureValueForName:@"enc_proj"].multiArrayValue;
    if (!enc_arr) {
      retired.store(true, std::memory_order_release);
      return false;
    }
    double t_copy0 = now_ms();
    const int H = decoder.hidden;
    out_enc.resize((size_t)valid_enc * H);
    if (enc_arr.dataType == MLMultiArrayDataTypeFloat16) {
      const uint16_t* src = (const uint16_t*)enc_arr.dataPointer;
      const NSInteger s_t = enc_arr.strides[1].integerValue;
      const NSInteger s_c = enc_arr.strides[2].integerValue;
      for (int t = 0; t < valid_enc; ++t) {
        for (int c = 0; c < H; ++c) {
          out_enc[(size_t)t * H + c] = fp16_to_f32(src[t * s_t + c * s_c]);
        }
      }
    } else {
      const float* src = (const float*)enc_arr.dataPointer;
      const NSInteger s_t = enc_arr.strides[1].integerValue;
      const NSInteger s_c = enc_arr.strides[2].integerValue;
      if (s_c == 1 && s_t == H) {
        std::memcpy(out_enc.data(), src, (size_t)valid_enc * H * sizeof(float));
      } else {
        for (int t = 0; t < valid_enc; ++t) {
          for (int c = 0; c < H; ++c) {
            out_enc[(size_t)t * H + c] = src[t * s_t + c * s_c];
          }
        }
      }
    }
    double copy_ms = now_ms() - t_copy0;
    if (out_predict_ms) *out_predict_ms = pred_ms;
    if (out_copy_ms) *out_copy_ms = copy_ms;
    (void)mel_ptr;
    double finished_ms = now_ms();
    if (is_real_req) {
      last_real_use_ms.store(finished_ms, std::memory_order_relaxed);
    } else {
      last_keep_ms.store(finished_ms, std::memory_order_relaxed);
    }
    return true;
  }

  void keep_loop() {
    auto st = keep_st;
    std::vector<float> dummy_enc;
    for (;;) {
      {
        std::unique_lock<std::mutex> lk(st->m);
        st->cv.wait_for(lk, std::chrono::milliseconds(1500), [&] { return st->stop; });
        if (st->stop) break;
      }
      if (retired.load(std::memory_order_acquire)) break;
      double now = now_ms();
      double last_real = last_real_use_ms.load(std::memory_order_relaxed);
      double last_keep = last_keep_ms.load(std::memory_order_relaxed);
      double real_idle = now - last_real;
      double any_idle = now - std::max(last_real, last_keep);
      if (real_idle > 180000.0 || any_idle < 1200.0) continue;
      if (!st->eval_mu.try_lock_for(std::chrono::milliseconds(50))) continue;
      std::unique_lock<std::timed_mutex> ev_lk(st->eval_mu, std::adopt_lock);
      if (retired.load(std::memory_order_acquire)) break;
      AneBucket& b0 = buckets[0];
      std::memset(b0.mel_arr.dataPointer, 0, (size_t)b0.t_mel * 80 * sizeof(float));
      eval_bucket(b0, 128, subsampled_len(128), dummy_enc, false);
    }
    st->done.store(true, std::memory_order_release);
  }
};

}  // namespace

extern "C" {

double nemo_ane_wired_mb(void) {
  vm_statistics64_data_t v{};
  mach_msg_type_number_t n = HOST_VM_INFO64_COUNT;
  if (host_statistics64(mach_host_self(), HOST_VM_INFO64, (host_info64_t)&v, &n) != KERN_SUCCESS) return 0.0;
  return double(v.wire_count) * double(vm_kernel_page_size) / 1e6;
}

int nemo_ane_retired(void* handle) {
  if (!handle) return 1;
  return static_cast<NemoAneContext*>(handle)->retired.load(std::memory_order_acquire) ? 1 : 0;
}

uint64_t nemo_ane_eval_count(void* handle) {
  if (!handle) return 0;
  return static_cast<NemoAneContext*>(handle)->eval_count.load(std::memory_order_relaxed);
}

int nemo_ane_last_timings(void* handle, NemoAneTimings* out) {
  if (!handle || !out) return -1;
  auto* ctx = static_cast<NemoAneContext*>(handle);
  std::lock_guard<std::mutex> lk(ctx->timings_mu);
  *out = ctx->last_timings;
  return 0;
}

void* nemo_ane_create(const char* model_dir, const char* gguf_path, char* err_buf, size_t err_len) {
  auto set_err = [&](const std::string& msg) {
    if (err_buf && err_len > 0) {
      std::snprintf(err_buf, err_len, "%s", msg.c_str());
    }
  };
  if (!model_dir || !gguf_path) {
    set_err("null model_dir or gguf_path");
    return nullptr;
  }
  try {
    auto ctx = std::make_unique<NemoAneContext>();
    GgufReader r(gguf_path);
    ctx->frontend.init(r);
    ctx->decoder.init(r);
    ctx->detokenizer.pieces = std::move(r.vocab);
    ctx->detokenizer.punctuation = std::move(r.punctuation);
    ctx->detokenizer.unk_id = r.unk_id;
    ctx->detokenizer.unk_surface = std::move(r.unk_surface);
    ctx->detokenizer.strip_leading_space = r.strip_leading_space;

    ctx->ane_queue = dispatch_queue_create("subtitle.nemo.ane", DISPATCH_QUEUE_SERIAL);
    const char* names[3] = {"fc_S256_w8a16.mlmodelc", "fc_M560_w8a16.mlmodelc", "fc_L1024_w8a16.mlmodelc"};
    const int tmels[3] = {256, 560, 1024};

    @autoreleasepool {
      MLModelConfiguration* cfg = [[MLModelConfiguration alloc] init];
      cfg.computeUnits = MLComputeUnitsCPUAndNeuralEngine;
      NSString* dir = [NSString stringWithUTF8String:model_dir];
      for (int i = 0; i < 3; ++i) {
        NSString* p = [dir stringByAppendingPathComponent:[NSString stringWithUTF8String:names[i]]];
        NSURL* url = [NSURL fileURLWithPath:p];
        NSError* err = nil;
        MLModel* mdl = [MLModel modelWithContentsOfURL:url configuration:cfg error:&err];
        if (!mdl) {
          throw std::runtime_error("Failed to load " + std::string(names[i]) + ": " +
                                   (err ? err.localizedDescription.UTF8String : "unknown"));
        }
        int tm = tmels[i];
        int te = NemoAneContext::subsampled_len(tm);
        ctx->buckets[i].t_mel = tm;
        ctx->buckets[i].t_enc = te;
        ctx->buckets[i].model = mdl;
        ctx->buckets[i].mel_arr = [[MLMultiArray alloc] initWithShape:@[@1, @1, @(tm), @80]
                                                             dataType:MLMultiArrayDataTypeFloat32
                                                                error:&err];
        ctx->buckets[i].am_arr = [[MLMultiArray alloc] initWithShape:@[@1, @1, @1, @(te)]
                                                            dataType:MLMultiArrayDataTypeFloat32
                                                               error:&err];
        ctx->buckets[i].cm_arr = [[MLMultiArray alloc] initWithShape:@[@1, @1, @(te)]
                                                            dataType:MLMultiArrayDataTypeFloat32
                                                               error:&err];
        if (!ctx->buckets[i].mel_arr || !ctx->buckets[i].am_arr || !ctx->buckets[i].cm_arr) {
          throw std::runtime_error("Failed to allocate MLMultiArray");
        }
      }
    }

    // Warmup bucket 0 so ANE is awake and compiled
    std::vector<float> dummy_enc;
    std::memset(ctx->buckets[0].mel_arr.dataPointer, 0, (size_t)ctx->buckets[0].t_mel * 80 * sizeof(float));
    if (!ctx->eval_bucket(ctx->buckets[0], 128, NemoAneContext::subsampled_len(128), dummy_enc, false)) {
      throw std::runtime_error("Initial ANE warmup failed");
    }
    double init_ms = now_ms();
    ctx->last_real_use_ms.store(init_ms, std::memory_order_relaxed);
    ctx->last_keep_ms.store(init_ms, std::memory_order_relaxed);
    // Disabled by default: M1 Pro ANE shows no wake penalty across 2s gaps, and keepalive wastes idle power.
    const char* ka_env = std::getenv("SUBTITLE_NEMO_ANE_KEEPALIVE");
    if (ka_env && std::strcmp(ka_env, "1") == 0) {
      ctx->keep_thread = std::thread([c = ctx.get()] { c->keep_loop(); });
    }
    return ctx.release();
  } catch (const std::exception& e) {
    set_err(e.what());
    return nullptr;
  }
}

void nemo_ane_destroy(void* handle) {
  if (!handle) return;
  auto* ctx = static_cast<NemoAneContext*>(handle);
  {
    std::lock_guard<std::mutex> lk(ctx->keep_st->m);
    ctx->keep_st->stop = true;
  }
  ctx->keep_st->cv.notify_all();
  if (ctx->keep_thread.joinable()) {
    ctx->keep_thread.join();
  }
  delete ctx;
}

int nemo_ane_detokenize(
    void* handle,
    const int32_t* ids,
    int32_t n_ids,
    char* out_text,
    size_t out_text_cap) {
  if (!handle || !out_text || out_text_cap == 0) return -1;
  auto* ctx = static_cast<NemoAneContext*>(handle);
  std::string txt = (ids && n_ids > 0) ? ctx->detokenizer.text(ids, n_ids) : "";
  std::snprintf(out_text, out_text_cap, "%s", txt.c_str());
  return 0;
}

int nemo_ane_transcribe(
    void* handle,
    const float* samples,
    int32_t n_samples,
    int32_t pad_samples,
    int32_t use_beam,
    nemo_ane_cancel_cb cancel_cb,
    void* user_data,
    char* out_text,
    size_t out_text_cap,
    NemoAneToken* out_tokens,
    int32_t max_tokens,
    int32_t* out_n_tokens,
    char* out_token_texts,
    size_t out_token_texts_cap) {
  (void)out_token_texts;
  (void)out_token_texts_cap;
  if (!handle || !samples || n_samples <= 0 || !out_text || out_text_cap == 0) return -1;
  auto* ctx = static_cast<NemoAneContext*>(handle);
  if (ctx->retired.load(std::memory_order_acquire)) return -2;
  if (cancel_cb && cancel_cb(user_data)) return 1;

  double t_total0 = now_ms();
  double t_fe0 = t_total0;
  std::vector<float> padded;
  const float* audio_ptr = samples;
  int32_t total_samples = n_samples;
  if (pad_samples > 0) {
    total_samples = n_samples + 2 * pad_samples;
    padded.assign((size_t)total_samples, 0.0f);
    std::memcpy(padded.data() + pad_samples, samples, (size_t)n_samples * sizeof(float));
    audio_ptr = padded.data();
  }

  std::vector<float> mel;
  int mel_frames = ctx->frontend.compute(audio_ptr, total_samples, mel);
  double frontend_ms = now_ms() - t_fe0;
  if (mel_frames < 2) {
    out_text[0] = '\0';
    if (out_n_tokens) *out_n_tokens = 0;
    return 0;
  }

  AneBucket* chosen = nullptr;
  for (int i = 0; i < 3; ++i) {
    if (ctx->buckets[i].t_mel >= mel_frames) {
      chosen = &ctx->buckets[i];
      break;
    }
  }
  if (!chosen) return 2;  // TOO_LONG -> caller falls back to speech.cpp

  if (cancel_cb && cancel_cb(user_data)) return 1;

  std::vector<float> enc_proj;
  int valid_enc = NemoAneContext::subsampled_len(mel_frames);
  double predict_ms = 0.0;
  double output_copy_ms = 0.0;
  {
    std::unique_lock<std::timed_mutex> ev_lk(ctx->keep_st->eval_mu, std::chrono::seconds(2));
    if (!ev_lk.owns_lock()) {
      ctx->retired.store(true, std::memory_order_release);
      return -2;
    }
    if (ctx->retired.load(std::memory_order_acquire)) return -2;
    std::memset(chosen->mel_arr.dataPointer, 0, (size_t)chosen->t_mel * 80 * sizeof(float));
    std::memcpy(chosen->mel_arr.dataPointer, mel.data(), (size_t)mel_frames * 80 * sizeof(float));
    if (!ctx->eval_bucket(*chosen, mel_frames, valid_enc, enc_proj, true, &predict_ms, &output_copy_ms)) {
      return -2;
    }
  }

  if (cancel_cb && cancel_cb(user_data)) return 1;

    std::string txt;
    auto detok_vec = [&](const std::vector<NemoAneToken>& v) {
      std::vector<int32_t> ids(v.size());
      for (size_t i = 0; i < v.size(); ++i) ids[i] = v[i].token_id;
      return ctx->detokenizer.text(ids.data(), (int32_t)ids.size());
    };

    double t_dec0 = now_ms();
    std::vector<NemoAneToken> toks;
    int rc = (use_beam == 1)
        ? ctx->decoder.decode_beam(enc_proj.data(), valid_enc, cancel_cb, user_data, toks)
        : ctx->decoder.decode_greedy(enc_proj.data(), valid_enc, cancel_cb, user_data, toks);
    if (rc == 1) return 1;

    txt = detok_vec(toks);
    if (use_beam == 2) {
      int g_chars = count_speech_codepoints(txt);
      double audio_sec = (double)n_samples / 16000.0;
      if (audio_sec >= 1.5 && (double)g_chars < 3.0 * audio_sec) {
        std::vector<NemoAneToken> beam_toks;
        int b_rc = ctx->decoder.decode_beam(enc_proj.data(), valid_enc, cancel_cb, user_data, beam_toks);
        if (b_rc == 1) return 1;
        std::string txt_b = detok_vec(beam_toks);
        if (count_speech_codepoints(txt_b) > g_chars) {
          toks = std::move(beam_toks);
          txt = std::move(txt_b);
        }
      }
    }

    if (out_n_tokens) *out_n_tokens = (int32_t)toks.size();
    if (out_tokens && max_tokens > 0) {
      int32_t copy_n = std::min<int32_t>(max_tokens, (int32_t)toks.size());
      std::memcpy(out_tokens, toks.data(), (size_t)copy_n * sizeof(NemoAneToken));
    }

    std::snprintf(out_text, out_text_cap, "%s", txt.c_str());
  double t_end = now_ms();
  {
    std::lock_guard<std::mutex> lk(ctx->timings_mu);
    ctx->last_timings = NemoAneTimings{
        frontend_ms,
        predict_ms,
        output_copy_ms,
        t_end - t_dec0,
        t_end - t_total0,
        chosen->t_mel,
        mel_frames,
        valid_enc,
    };
  }
  return 0;
}

}  // extern "C"
