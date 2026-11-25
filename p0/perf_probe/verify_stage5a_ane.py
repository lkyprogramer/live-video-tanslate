"""Stage 5A-1 probe: ANE static-bucket FastConformer Encoder (CoreML FP16 & W8A16) + Keepalive."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import math
import re
import shutil
import statistics
import struct
import subprocess
import sys
import time
from pathlib import Path

import coremltools as ct
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "p0/tier2_asr"))
from metrics_utils import compute_cer, percentile, score_d0_cer  # noqa: E402

GGUF_PATH = REPO_ROOT / ".model-cache/speech-cpp/reazonspeech-nemo-619M-v2-Q8_0.gguf"
SLICES_NPZ = Path("/tmp/stage5a-slices.npz")
META_JSON = Path("/tmp/stage5a-meta.json")
MODEL_DIR = Path("/tmp/stage5a-ane-models")
ARCHIVE_PATH = REPO_ROOT / "docs/experiments/stage5a-2026-11-24/stage5a_ane_probe.json"

RATE = 16000
PAD_MS = 350
PAD_SAMPLES = int(round(PAD_MS * RATE / 1000.0))  # 5600


# ---------------------------------------------------------------------------
# System wired memory helper (mirrors kapitsa/src/ane/ffn_split.cpp:wired_mb)
# ---------------------------------------------------------------------------
class _VmStat64(ctypes.Structure):
    _fields_ = [
        ("free_count", ctypes.c_uint32),
        ("active_count", ctypes.c_uint32),
        ("inactive_count", ctypes.c_uint32),
        ("wire_count", ctypes.c_uint32),
        ("zero_fill_count", ctypes.c_uint64),
        ("reactivations", ctypes.c_uint64),
        ("pageins", ctypes.c_uint64),
        ("pageouts", ctypes.c_uint64),
        ("faults", ctypes.c_uint64),
        ("cow_faults", ctypes.c_uint64),
        ("lookups", ctypes.c_uint64),
        ("hits", ctypes.c_uint64),
        ("purges", ctypes.c_uint64),
        ("purgeable_count", ctypes.c_uint32),
        ("speculative_count", ctypes.c_uint32),
        ("decompressions", ctypes.c_uint64),
        ("compressions", ctypes.c_uint64),
        ("swapins", ctypes.c_uint64),
        ("swapouts", ctypes.c_uint64),
        ("compressor_page_count", ctypes.c_uint32),
        ("throttled_count", ctypes.c_uint32),
        ("external_page_count", ctypes.c_uint32),
        ("internal_page_count", ctypes.c_uint32),
        ("total_uncompressed_pages_in_compressor", ctypes.c_uint64),
    ]


_libc = ctypes.CDLL(ctypes.util.find_library("c"))
_libc.mach_host_self.restype = ctypes.c_uint32
_libc.host_statistics64.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.POINTER(_VmStat64), ctypes.POINTER(ctypes.c_uint32)]
_libc.host_statistics64.restype = ctypes.c_int


def wired_mb() -> float:
    vm = _VmStat64()
    count = ctypes.c_uint32(ctypes.sizeof(_VmStat64) // 4)
    if _libc.host_statistics64(_libc.mach_host_self(), 4, ctypes.byref(vm), ctypes.byref(count)) != 0:
        return 0.0
    page_size = ctypes.c_size_t.in_dll(_libc, "vm_kernel_page_size").value
    return float(vm.wire_count) * float(page_size) / 1e6


# ---------------------------------------------------------------------------
# GGUF Q8_0 / F32 Loader
# ---------------------------------------------------------------------------
def load_gguf(path: Path) -> tuple[dict, dict[str, np.ndarray]]:
    with path.open("rb") as f:
        magic = f.read(4)
        assert magic == b"GGUF", f"Bad magic: {magic}"
        _ver, ntensors, nkv = struct.unpack("<IQQ", f.read(20))

        def rstr() -> str:
            n = struct.unpack("<Q", f.read(8))[0]
            return f.read(n).decode("utf-8", "replace")

        def rval(t: int):
            if t in (0, 1, 7):
                return struct.unpack("<B", f.read(1))[0]
            if t in (2, 3):
                return struct.unpack("<H", f.read(2))[0]
            if t == 4:
                return struct.unpack("<I", f.read(4))[0]
            if t == 5:
                return struct.unpack("<i", f.read(4))[0]
            if t == 6:
                return struct.unpack("<f", f.read(4))[0]
            if t == 8:
                return rstr()
            if t == 9:
                et, cnt = struct.unpack("<IQ", f.read(12))
                return [rval(et) for _ in range(cnt)]
            if t == 10:
                return struct.unpack("<Q", f.read(8))[0]
            if t == 11:
                return struct.unpack("<q", f.read(8))[0]
            if t == 12:
                return struct.unpack("<d", f.read(8))[0]
            raise ValueError(f"Unsupported GGUF value type {t}")

        meta = {}
        for _ in range(nkv):
            k = rstr()
            t = struct.unpack("<I", f.read(4))[0]
            meta[k] = rval(t)

        tinfos = []
        for _ in range(ntensors):
            name = rstr()
            ndim = struct.unpack("<I", f.read(4))[0]
            dims = struct.unpack(f"<{ndim}Q", f.read(8 * ndim))
            ttype = struct.unpack("<I", f.read(4))[0]
            off = struct.unpack("<Q", f.read(8))[0]
            tinfos.append((name, dims, ttype, off))

        pos = f.tell()
        align = int(meta.get("general.alignment", 32))
        data_start = ((pos + align - 1) // align) * align

        tensors: dict[str, np.ndarray] = {}
        for name, dims, ttype, off in tinfos:
            f.seek(data_start + off)
            # GGML stores shape reversed: dims[0] is innermost
            shape = tuple(reversed(dims))
            ne = int(np.prod(shape))
            if ttype == 0:  # F32
                arr = np.frombuffer(f.read(ne * 4), dtype="<f4").reshape(shape).copy()
            elif ttype == 8:  # Q8_0: 32 elems per 34-byte block (f16 scale + 32 int8)
                assert ne % 32 == 0
                nblk = ne // 32
                raw = f.read(nblk * 34)
                dt = np.dtype([("d", "<f2"), ("qs", "i1", (32,))])
                blks = np.frombuffer(raw, dtype=dt, count=nblk)
                arr = (blks["d"].astype(np.float32)[:, None] * blks["qs"].astype(np.float32)).reshape(shape).copy()
            else:
                raise ValueError(f"Unsupported tensor type {ttype} for {name}")
            tensors[name] = arr
    return meta, tensors


# ---------------------------------------------------------------------------
# Frontend & Detokenizer (exact speech.cpp parity)
# ---------------------------------------------------------------------------
class NemoFrontend:
    def __init__(self, meta: dict, tensors: dict[str, np.ndarray]) -> None:
        self.n_fft = int(meta["fastconformer.frontend.n_fft"])
        self.hop = int(meta["fastconformer.frontend.hop_length"])
        self.n_mels = int(meta["fastconformer.frontend.n_mels"])
        self.preemph = float(meta["fastconformer.frontend.preemphasis"])
        self.log_guard = float(meta["fastconformer.frontend.log_guard"])
        self.std_guard = float(meta["fastconformer.frontend.std_guard"])
        w = tensors["frontend.window"].astype(np.float64)
        self.window = np.zeros(self.n_fft, dtype=np.float64)
        left = (self.n_fft - len(w)) // 2
        self.window[left : left + len(w)] = w
        self.fb = tensors["frontend.filterbank"].astype(np.float64)  # (80, 257)

    def features(self, samples: np.ndarray) -> np.ndarray:
        """Return normalized mel features of shape [frames, 80] (float32)."""
        x = np.asarray(samples, dtype=np.float32)
        frames = len(x) // self.hop
        pre = np.empty_like(x)
        pre[0] = x[0]
        pre[1:] = x[1:] - self.preemph * x[:-1]
        half = self.n_fft // 2
        padded = np.pad(pre.astype(np.float64), (half, half + self.n_fft), mode="constant")
        idx = np.arange(frames)[:, None] * self.hop + np.arange(self.n_fft)[None, :]
        win_frames = padded[idx] * self.window[None, :]
        spec = np.fft.rfft(win_frames, n=self.n_fft, axis=-1)
        power = spec.real * spec.real + spec.imag * spec.imag  # (frames, 257)
        mel = np.log(power @ self.fb.T + self.log_guard)
        mean = mel.mean(axis=0, keepdims=True)
        std = np.sqrt(((mel - mean) ** 2).sum(axis=0, keepdims=True) / float(frames - 1)) + self.std_guard
        return ((mel - mean) / std).astype(np.float32)


class Detokenizer:
    def __init__(self, meta: dict) -> None:
        self.pieces: list[str] = list(meta["fastconformer.tokenizer.tokens"])
        self.punc: list[str] = list(meta["fastconformer.tokenizer.punctuation"])
        self.unk_id = int(meta["fastconformer.tokenizer.unknown_id"])
        self.unk_surf = str(meta["fastconformer.tokenizer.unknown_surface"])
        self.strip_lead = bool(meta["fastconformer.tokenizer.strip_leading_space"])

    def text(self, ids: list[int]) -> str:
        out = []
        empty = True
        for tid in ids:
            if tid == self.unk_id:
                p = self.unk_surf
            else:
                p = self.pieces[tid]
                if self.strip_lead and empty and p.startswith("▁"):
                    p = p[1:]
                p = p.replace("▁", " ")
            if p:
                empty = False
            out.append(p)
        s = "".join(out)
        if self.punc:
            pat = r"\s+(?=[" + "".join(re.escape(c) for c in self.punc) + r"])"
            s = re.sub(pat, "", s)
        return s


# ---------------------------------------------------------------------------
# PyTorch FastConformer Encoder (static-shape ANE-friendly option)
# ---------------------------------------------------------------------------
def _tanh_silu(x: torch.Tensor) -> torch.Tensor:
    # Avoid M1 ANE sigmoid accuracy drop (kapitsa/src/ane/ffn_split.cpp)
    return 0.5 * x * (1.0 + torch.tanh(0.5 * x))


def _act_silu(x: torch.Tensor, use_tanh: bool) -> torch.Tensor:
    return _tanh_silu(x) if use_tanh else F.silu(x)


def _act_sigmoid(x: torch.Tensor, use_tanh: bool) -> torch.Tensor:
    return 0.5 * (1.0 + torch.tanh(0.5 * x)) if use_tanh else torch.sigmoid(x)


def ane_layer_norm(x: torch.Tensor, ln: nn.LayerNorm) -> torch.Tensor:
    # Scale down by 1/16 before mean/var so sum((x/16)^2) over 1024 channels stays << FP16_MAX (65504)
    xs = x * 0.0625
    mean = xs.mean(dim=-1, keepdim=True)
    d = xs - mean
    d_s = d * (1.0 / math.sqrt(x.shape[-1]))
    var = (d_s * d_s).sum(dim=-1, keepdim=True)
    eps_eff = max(ln.eps * (0.0625 * 0.0625), 1e-5)
    return d * torch.rsqrt(var + eps_eff) * ln.weight + ln.bias


def _build_rel_pe(span: int, d_model: int, pos_base: float) -> torch.Tensor:
    """Create [2*span+1, d_model] sinusoidal PE from +span down to -span."""
    positions = torch.arange(span, -span - 1, -1, dtype=torch.float32)[:, None]
    step = -math.log(pos_base) / d_model
    i = torch.arange(0, d_model, 2, dtype=torch.float32)[None, :]
    angles = positions * torch.exp(i * step)
    pe = torch.empty((2 * span + 1, d_model), dtype=torch.float32)
    pe[:, 0::2] = torch.sin(angles)
    pe[:, 1::2] = torch.cos(angles)
    return pe


class FastConformerBlock(nn.Module):
    def __init__(
        self,
        idx: int,
        tensors: dict[str, np.ndarray],
        d_model: int,
        heads: int,
        conv_kernel: int,
        eps: float,
        ff_factor: float,
        context: int,
        pe_table: torch.Tensor,
        use_tanh: bool,
    ) -> None:
        super().__init__()
        self.d_model = d_model
        self.heads = heads
        self.dk = d_model // heads
        self.ff_factor = ff_factor
        self.context = context
        self.use_tanh = use_tanh
        p = f"blk.{idx}."

        def mk_ln(name: str) -> nn.LayerNorm:
            ln = nn.LayerNorm(d_model, eps=eps)
            ln.weight.data.copy_(torch.from_numpy(tensors[name + ".weight"]))
            ln.bias.data.copy_(torch.from_numpy(tensors[name + ".bias"]))
            return ln

        def mk_lin(name: str) -> nn.Linear:
            w = tensors[name + ".weight"]
            b = tensors[name + ".bias"]
            lin = nn.Linear(w.shape[1], w.shape[0], bias=True)
            lin.weight.data.copy_(torch.from_numpy(w))
            lin.bias.data.copy_(torch.from_numpy(b))
            return lin

        self.ff1_norm = mk_ln(p + "ff1_norm")
        self.ff1_up = mk_lin(p + "ff1_up")
        self.ff1_down = mk_lin(p + "ff1_down")

        self.attn_norm = mk_ln(p + "attn_norm")
        self.attn_q = mk_lin(p + "attn_q")
        self.attn_k = mk_lin(p + "attn_k")
        self.attn_v = mk_lin(p + "attn_v")
        self.attn_out = mk_lin(p + "attn_out")
        self.pos_bias_u = nn.Parameter(torch.from_numpy(tensors[p + "attn_pos_bias_u"].copy()))  # (heads, dk)
        self.pos_bias_v = nn.Parameter(torch.from_numpy(tensors[p + "attn_pos_bias_v"].copy()))  # (heads, dk)
        # Pre-project PE [2*context+1, d_model] -> [heads, 2*context+1, dk]
        pos_w = torch.from_numpy(tensors[p + "attn_pos.weight"])  # (d_model, d_model)
        proj_pe = F.linear(pe_table, pos_w)  # (2*context+1, d_model)
        proj_pe = proj_pe.view(-1, heads, self.dk).permute(1, 0, 2).contiguous()  # (heads, 2*context+1, dk)
        self.register_buffer("proj_pe", proj_pe)

        self.conv_norm = mk_ln(p + "conv_norm")
        self.conv_pw1_a = mk_lin(p + "conv_pw1_a")
        self.conv_pw1_gate = mk_lin(p + "conv_pw1_gate")
        self.conv_dw = nn.Conv1d(d_model, d_model, kernel_size=conv_kernel, padding=(conv_kernel - 1) // 2, groups=d_model)
        self.conv_dw.weight.data.copy_(torch.from_numpy(tensors[p + "conv_dw.weight"]).unsqueeze(1))  # (1024, 1, 9)
        self.conv_dw.bias.data.copy_(torch.from_numpy(tensors[p + "conv_dw.bias"]))
        self.conv_pw2 = mk_lin(p + "conv_pw2")

        self.ff2_norm = mk_ln(p + "ff2_norm")
        self.ff2_up = mk_lin(p + "ff2_up")
        self.ff2_down = mk_lin(p + "ff2_down")
        self.out_norm = mk_ln(p + "out_norm")

    def _local_attn(self, x: torch.Tensor, attn_mask: torch.Tensor | None) -> torch.Tensor:
        # x: [1, T, D], attn_mask: [1, 1, 1, T] (0 for valid, -1e4 for padded)
        T = x.shape[1]
        h = ane_layer_norm(x, self.attn_norm) if self.use_tanh else self.attn_norm(x)
        q = self.attn_q(h).view(1, T, self.heads, self.dk).permute(0, 2, 1, 3)  # [1, H, T, dk]
        k = self.attn_k(h).view(1, T, self.heads, self.dk).permute(0, 2, 1, 3)
        v = self.attn_v(h).view(1, T, self.heads, self.dk).permute(0, 2, 1, 3)
        scale = 1.0 / math.sqrt(self.dk)

        if T <= self.context:
            # Single window: w = T, blocks = 1, globals = 1 (frame 0)
            qu = (q + self.pos_bias_u[None, :, None, :]) * scale
            qv = (q + self.pos_bias_v[None, :, None, :]) * scale
            q_scaled = q * scale
            ac = torch.matmul(qu, k.transpose(-1, -2))  # [1, H, T, T]
            # Slice proj_pe around center: indices [context - T + 1 .. context + T - 1] (length 2*T - 1)
            c0 = self.context - (T - 1)
            c1 = self.context + T
            pe_slice = self.proj_pe[:, c0:c1, :]  # [H, 2*T-1, dk]
            bd_raw = torch.matmul(qv, pe_slice.unsqueeze(0).transpose(-1, -2))  # [1, H, T, 2*T-1]
            bd_pad = F.pad(bd_raw, (1, 0))
            bd = bd_pad.reshape(1, self.heads, 2 * T, T)[:, :, 1:, :].reshape(1, self.heads, T, 2 * T - 1)[:, :, :, :T]
            local_scores = ac + bd  # [1, H, T, T]

            # Prepend global token 0 score: q * k[:, :, :1, :]^T
            kg = k[:, :, :1, :]  # [1, H, 1, dk]
            vg = v[:, :, :1, :]  # [1, H, 1, dk]
            g_score = torch.matmul(q_scaled, kg.transpose(-1, -2))  # [1, H, T, 1]
            scores = torch.cat([g_score, local_scores], dim=-1)  # [1, H, T, 1+T]
            if attn_mask is not None:
                # mask col 0 (global token 0 is always valid), mask local keys [1:]
                g_mask = torch.zeros((1, 1, 1, 1), dtype=scores.dtype, device=scores.device)
                full_mask = torch.cat([g_mask, attn_mask], dim=-1)
                scores = scores + full_mask
            weights = F.softmax(scores, dim=-1)
            out_local = torch.matmul(weights[:, :, :, :1], vg) + torch.matmul(weights[:, :, :, 1:], v)

            # Global token 0's own output: q_0 against all keys (without pos bias/PE)
            g0_scores = torch.matmul(q_scaled[:, :, :1, :], k.transpose(-1, -2))  # [1, H, 1, T]
            if attn_mask is not None:
                g0_scores = g0_scores + attn_mask
            g0_w = F.softmax(g0_scores, dim=-1)
            out_g0 = torch.matmul(g0_w, v)  # [1, H, 1, dk]
            out = torch.cat([out_g0, out_local[:, :, 1:, :]], dim=2)  # [1, H, T, dk]
        else:
            # Multi-block local attention (w = context = 128)
            w = self.context
            blocks = (T + w - 1) // w
            padded = blocks * w
            pad_t = padded - T
            qu = q + self.pos_bias_u[None, :, None, :]
            qv = q + self.pos_bias_v[None, :, None, :]
            if pad_t > 0:
                qu = F.pad(qu, (0, 0, 0, pad_t))
                qv = F.pad(qv, (0, 0, 0, pad_t))
                qn = F.pad(q, (0, 0, 0, pad_t))
            else:
                qn = q
            qu_b = qu.view(1, self.heads, blocks, w, self.dk)
            qv_b = qv.view(1, self.heads, blocks, w, self.dk)
            qn_b = qn.view(1, self.heads, blocks, w, self.dk)

            kp = F.pad(k, (0, 0, w, pad_t + w))  # [1, H, padded + 2w, dk]
            vp = F.pad(v, (0, 0, w, pad_t + w))
            kw = torch.cat(
                [kp[:, :, b * w : (b + 3) * w, :].unsqueeze(2) for b in range(blocks)],
                dim=2,
            )  # [1, H, blocks, 3w, dk]
            vw = torch.cat(
                [vp[:, :, b * w : (b + 3) * w, :].unsqueeze(2) for b in range(blocks)],
                dim=2,
            )
            ac = torch.matmul(qu_b, kw.transpose(-1, -2))  # [1, H, blocks, w, 3w]
            bd_raw = torch.matmul(qv_b, self.proj_pe.unsqueeze(0).unsqueeze(2).transpose(-1, -2))  # [1, H, blocks, w, 2w+1]
            # encoder.cpp:263: bd = pad(bd_raw, left=w-1) -> [blocks, w, 3w] -> stride view: element (r, c) is bd_raw[r, c - r + ...]?
            # Let's check exact indexing: encoder.cpp:263 pads (3w - (2w+1)) = w-1 on the RIGHT (ne0 in ggml)!
            # Wait: ggml_pad(ctx, t, lp0, lp1, ...) pads ON THE RIGHT!
            # And view with stride (3w - 1): element (r, c) is flat r*(3w-1) + c = r*(3w) + (c - r)!
            # Since proj_pe has [w, w-1, ..., 0, ..., -w], index 0 is +w, index w is 0, index 2w is -w!
            # At query r in block, window col c is (k-1)w + c, query pos is (k-1)w + w + r, so (q - key) = w + r - c!
            # In proj_pe, offset (w + r - c) is at index w - (w + r - c) = c - r!
            # So element (r, c) needs bd_raw[r, c - r] (for r <= c <= r + 2w)!
            # With right pad w-1 (row width 3w), flat r*(3w-1) + c = r*3w + (c - r), which IS bd_raw[r, c - r]!
            # Let's compute this cleanly via F.pad + unfold or slice:
            bd_pad = F.pad(bd_raw, (0, w - 1))  # [1, H, blocks, w, 3w]
            flat = bd_pad.reshape(1, self.heads, blocks, w * 3 * w)
            # We want indices r * (3*w - 1) + c for r in [0..w-1], c in [0..3w-1]
            # Wait: r=0 -> [0..3w-1]; r=1 -> [3w-1 .. 6w-2], etc. Max idx = (w-1)*(3w-1) + 3w - 1 = w*3w - w < w*3w!
            # Even simpler for static CoreML: gather or shifted index!
            idx_r = torch.arange(w, device=x.device)[:, None]
            idx_c = torch.arange(3 * w, device=x.device)[None, :]
            bd_idx = (idx_r * (3 * w - 1) + idx_c).reshape(-1)
            bd = flat[:, :, :, bd_idx].reshape(1, self.heads, blocks, w, 3 * w)
            local_scores = ac + bd  # [1, H, blocks, w, 3w]

            kg = k[:, :, :1, :].unsqueeze(2)  # [1, H, 1, 1, dk]
            vg = v[:, :, :1, :].unsqueeze(2)  # [1, H, 1, 1, dk]
            g_score = torch.matmul(qn_b, kg.transpose(-1, -2))  # [1, H, blocks, w, 1]
            scores = torch.cat([g_score, local_scores], dim=-1) * scale  # [1, H, blocks, w, 1+3w]

            # Build static local band mask [blocks, w, 1+3w]
            band_mask = torch.full((blocks, w, 1 + 3 * w), -1e4, dtype=scores.dtype, device=x.device)
            band_mask[:, :, 0] = 0.0
            for bk in range(blocks):
                for r in range(w):
                    for c in range(r, r + 2 * w + 1):
                        key_pos = (bk - 1) * w + c
                        if 0 <= key_pos < T:
                            band_mask[bk, r, 1 + c] = 0.0
            scores = scores + band_mask[None, None, :, :, :]
            if attn_mask is not None:
                # Also mask out padded tail frames in keys (attn_mask: [1, 1, 1, T])
                m_tail = F.pad(attn_mask.squeeze(0).squeeze(0).squeeze(0), (w, pad_t + w), value=-1e4)  # [padded+2w]
                mw = torch.stack([m_tail[bk * w : (bk + 3) * w] for bk in range(blocks)], dim=0)  # [blocks, 3w]
                mw_full = torch.cat([torch.zeros((blocks, 1), dtype=scores.dtype, device=x.device), mw], dim=-1)
                scores = scores + mw_full[None, None, :, None, :]

            weights = F.softmax(scores, dim=-1)
            out_local = torch.matmul(weights[:, :, :, :, :1], vg) + torch.matmul(weights[:, :, :, :, 1:], vw)
            out_local = out_local.reshape(1, self.heads, padded, self.dk)[:, :, :T, :]

            g0_scores = torch.matmul(q[:, :, :1, :], k.transpose(-1, -2)) * scale
            if attn_mask is not None:
                g0_scores = g0_scores + attn_mask
            g0_w = F.softmax(g0_scores, dim=-1)
            out_g0 = torch.matmul(g0_w, v)
            out = torch.cat([out_g0, out_local[:, :, 1:, :]], dim=2)

        out_c = out.permute(0, 1, 3, 2).reshape(1, self.d_model, 1, T)
        y = F.conv2d(out_c, self.attn_out.weight[:, :, None, None], self.attn_out.bias).squeeze(2).transpose(1, 2)
        return x + y

    def forward(self, x: torch.Tensor, attn_mask: torch.Tensor | None, conv_mask: torch.Tensor | None) -> torch.Tensor:
        ln_fn = (lambda t, mod: ane_layer_norm(t, mod)) if self.use_tanh else (lambda t, mod: mod(t))
        # ff1
        h = self.ff1_down(_act_silu(self.ff1_up(ln_fn(x, self.ff1_norm)), self.use_tanh))
        x = x + self.ff_factor * h
        # attn
        x = self._local_attn(x, attn_mask)
        # conv
        cn = ln_fn(x, self.conv_norm)
        cg = self.conv_pw1_a(cn) * _act_sigmoid(self.conv_pw1_gate(cn), self.use_tanh)
        cg_t = cg.transpose(1, 2)  # [1, D, T]
        if conv_mask is not None:
            cg_t = cg_t * conv_mask
        cdw = self.conv_dw(cg_t).transpose(1, 2)
        x = x + self.conv_pw2(_act_silu(cdw, self.use_tanh))
        # ff2
        h2 = self.ff2_down(_act_silu(self.ff2_up(ln_fn(x, self.ff2_norm)), self.use_tanh))
        x = x + self.ff_factor * h2
        return ln_fn(x, self.out_norm)


class FastConformerEncoder(nn.Module):
    def __init__(self, meta: dict, tensors: dict[str, np.ndarray], use_tanh: bool = True) -> None:
        super().__init__()
        d_model = int(meta["fastconformer.encoder.d_model"])
        n_layers = int(meta["fastconformer.encoder.num_layers"])
        heads = int(meta["fastconformer.encoder.num_heads"])
        conv_kernel = int(meta["fastconformer.encoder.conv_kernel"])
        eps = float(meta["fastconformer.encoder.norm_eps"])
        pos_base = float(meta["fastconformer.encoder.pos_base"])
        self.xscale = float(meta["fastconformer.encoder.xscale"])
        ff_factor = float(meta["fastconformer.encoder.ff_factor"])
        context = int(meta["fastconformer.encoder.attention_context"])

        # Subsampling: conv0 -> relu -> dw1 -> pw1 -> relu -> dw2 -> pw2 -> relu -> sub.out
        self.sub_conv0 = nn.Conv2d(1, 256, kernel_size=3, stride=2, padding=1)
        self.sub_conv0.weight.data.copy_(torch.from_numpy(tensors["sub.conv.0.weight"]))
        self.sub_conv0.bias.data.copy_(torch.from_numpy(tensors["sub.conv.0.bias"]))

        self.sub_dw1 = nn.Conv2d(256, 256, kernel_size=3, stride=2, padding=1, groups=256)
        self.sub_dw1.weight.data.copy_(torch.from_numpy(tensors["sub.conv.1.dw.weight"]))
        self.sub_dw1.bias.data.copy_(torch.from_numpy(tensors["sub.conv.1.dw.bias"]))
        self.sub_pw1 = nn.Conv2d(256, 256, kernel_size=1)
        self.sub_pw1.weight.data.copy_(torch.from_numpy(tensors["sub.conv.1.pw.weight"])[:, :, None, None])
        self.sub_pw1.bias.data.copy_(torch.from_numpy(tensors["sub.conv.1.pw.bias"]))

        self.sub_dw2 = nn.Conv2d(256, 256, kernel_size=3, stride=2, padding=1, groups=256)
        self.sub_dw2.weight.data.copy_(torch.from_numpy(tensors["sub.conv.2.dw.weight"]))
        self.sub_dw2.bias.data.copy_(torch.from_numpy(tensors["sub.conv.2.dw.bias"]))
        self.sub_pw2 = nn.Conv2d(256, 256, kernel_size=1)
        self.sub_pw2.weight.data.copy_(torch.from_numpy(tensors["sub.conv.2.pw.weight"])[:, :, None, None])
        self.sub_pw2.bias.data.copy_(torch.from_numpy(tensors["sub.conv.2.pw.bias"]))

        w_out = tensors["sub.out.weight"]
        self.sub_out = nn.Linear(w_out.shape[1], w_out.shape[0])
        self.sub_out.weight.data.copy_(torch.from_numpy(w_out))
        self.sub_out.bias.data.copy_(torch.from_numpy(tensors["sub.out.bias"]))

        pe_table = _build_rel_pe(context, d_model, pos_base)
        self.blocks = nn.ModuleList(
            [
                FastConformerBlock(i, tensors, d_model, heads, conv_kernel, eps, ff_factor, context, pe_table, use_tanh)
                for i in range(n_layers)
            ]
        )

        # Fold joint.enc at tail so CoreML outputs [1, T_enc, 640] directly
        w_enc = tensors["joint.enc.weight"]
        self.joint_enc = nn.Linear(w_enc.shape[1], w_enc.shape[0])
        self.joint_enc.weight.data.copy_(torch.from_numpy(w_enc))
        self.joint_enc.bias.data.copy_(torch.from_numpy(tensors["joint.enc.bias"]))

    def forward(self, mel: torch.Tensor, attn_mask: torch.Tensor, conv_mask: torch.Tensor) -> torch.Tensor:
        # mel: [1, 1, T_mel, 80]
        x = F.relu(self.sub_conv0(mel))
        x = F.relu(self.sub_pw1(self.sub_dw1(x)))
        x = F.relu(self.sub_pw2(self.sub_dw2(x)))  # [1, 256, T_enc, 10]
        b, c, t, f = x.shape
        x = x.permute(0, 2, 1, 3).contiguous().view(b, t, c * f)  # [1, T_enc, 2560]
        x = self.sub_out(x) * self.xscale
        for blk in self.blocks:
            x = blk(x, attn_mask, conv_mask)
        return self.joint_enc(x)


# ---------------------------------------------------------------------------
# Fast CPU NumPy RNNT Decoder (greedy + ALSD beam=4)
# ---------------------------------------------------------------------------
class CpuRnntDecoder:
    def __init__(self, meta: dict, tensors: dict[str, np.ndarray]) -> None:
        self.blank = int(meta["fastconformer.decoder.blank_id"])
        self.beam = int(meta["fastconformer.decoder.rnnt.beam_size"])
        self.score_norm = bool(meta["fastconformer.decoder.rnnt.score_norm"])
        self.max_ratio = float(meta["fastconformer.decoder.rnnt.max_target_ratio"])
        self.layers = int(meta["fastconformer.decoder.prediction_layers"])

        self.embed = tensors["pred.embed.weight"]  # (3001, 640)
        self.lstm_ih = [tensors[f"pred.lstm.{l}.ih.weight"] for l in range(self.layers)]  # (2560, 640)
        self.lstm_hh = [tensors[f"pred.lstm.{l}.hh.weight"] for l in range(self.layers)]  # (2560, 640)
        self.lstm_b = [tensors[f"pred.lstm.{l}.bias"] for l in range(self.layers)]  # (2560,)
        self.hidden = self.embed.shape[1]

        self.joint_pred_w = tensors["joint.pred.weight"]  # (640, 640)
        self.joint_pred_b = tensors["joint.pred.bias"]  # (640,)
        self.joint_out_w = tensors["joint.out.weight"]  # (3001, 640)
        self.joint_out_b = tensors["joint.out.bias"]  # (3001,)

    def _pred_step(self, label: int, state: tuple[np.ndarray, np.ndarray] | None) -> tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]:
        if state is None:
            h = np.zeros((self.layers, self.hidden), dtype=np.float32)
            c = np.zeros((self.layers, self.hidden), dtype=np.float32)
        else:
            h, c = state[0].copy(), state[1].copy()
        x = self.embed[label]
        H = self.hidden
        for l in range(self.layers):
            gates = self.lstm_ih[l] @ x + self.lstm_hh[l] @ h[l] + self.lstm_b[l]
            i_g = 1.0 / (1.0 + np.exp(-gates[0:H]))
            f_g = 1.0 / (1.0 + np.exp(-gates[H : 2 * H]))
            g_g = np.tanh(gates[2 * H : 3 * H])
            o_g = 1.0 / (1.0 + np.exp(-gates[3 * H : 4 * H]))
            c[l] = f_g * c[l] + i_g * g_g
            h[l] = o_g * np.tanh(c[l])
            x = h[l]
        pred_proj = self.joint_pred_w @ x + self.joint_pred_b
        return pred_proj, (h, c)

    def _joint_logits(self, enc_vec: np.ndarray, pred_proj: np.ndarray) -> np.ndarray:
        h = np.maximum(enc_vec + pred_proj, 0.0)
        return self.joint_out_w @ h + self.joint_out_b

    def decode_greedy(self, enc_proj: np.ndarray, max_symbols: int = 10) -> list[int]:
        T = enc_proj.shape[0]
        state = None
        label = self.blank
        pred_proj, next_state = self._pred_step(label, state)
        ids: list[int] = []
        for t in range(T):
            f = enc_proj[t]
            syms = 0
            while syms < max_symbols:
                logits = self._joint_logits(f, pred_proj)
                tok = int(np.argmax(logits))
                if tok == self.blank:
                    break
                ids.append(tok)
                syms += 1
                pred_proj, next_state = self._pred_step(tok, next_state)
        return ids

    def decode_beam(self, enc_proj: np.ndarray) -> list[int]:
        T = enc_proj.shape[0]
        beam = min(self.beam, self.blank)
        max_labels = int(self.max_ratio * T)
        cache: dict[tuple[int, ...], tuple[np.ndarray, tuple[np.ndarray, np.ndarray]]] = {}
        beam_hyps: list[tuple[tuple[int, ...], float, tuple[np.ndarray, np.ndarray] | None]] = [((self.blank,), 0.0, None)]
        finished: tuple[tuple[int, ...], float] | None = None

        def key_fn(labels: tuple[int, ...], score: float) -> float:
            return score / len(labels) if self.score_norm else score

        for i in range(T + max_labels):
            active = []
            for labels, score, prev_state in beam_hyps:
                t = i - (len(labels) - 1)
                if t <= T - 1:
                    active.append((labels, score, prev_state, t))
            if not active:
                break

            expanded: list[tuple[tuple[int, ...], float, tuple[np.ndarray, np.ndarray]]] = []
            finishing: list[tuple[tuple[int, ...], float]] = []
            for labels, score, prev_state, t in active:
                cached = cache.get(labels)
                if cached is None:
                    cached = self._pred_step(labels[-1], prev_state)
                    cache[labels] = cached
                pred_proj, cur_state = cached
                logits = self._joint_logits(enc_proj[t], pred_proj)
                top = float(np.max(logits))
                lse = top + math.log(float(np.sum(np.exp(logits - top))))
                logp = logits - lse

                b_score = score + float(logp[self.blank])
                expanded.append((labels, b_score, prev_state))
                if t == T - 1:
                    finishing.append((labels, b_score))

                tok_scores = logp[: self.blank]
                top_idx = np.argpartition(tok_scores, -beam)[-beam:]
                top_idx = top_idx[np.argsort(-tok_scores[top_idx], kind="mergesort")]
                for tok in top_idx:
                    tok_i = int(tok)
                    expanded.append((labels + (tok_i,), score + float(logp[tok_i]), cur_state))

            expanded.sort(key=lambda x: x[1], reverse=True)
            expanded = expanded[:beam]
            merged: dict[tuple[int, ...], tuple[float, tuple[np.ndarray, np.ndarray]]] = {}
            order: list[tuple[int, ...]] = []
            for labels, sc, st in expanded:
                if labels in merged:
                    old_sc, old_st = merged[labels]
                    merged[labels] = (float(np.logaddexp(old_sc, sc)), old_st)
                else:
                    merged[labels] = (sc, st)
                    order.append(labels)
            beam_hyps = [(lbl, merged[lbl][0], merged[lbl][1]) for lbl in order]
            cache = {lbl: cache[lbl] for lbl in order if lbl in cache}
            for lbl, sc in finishing:
                if finished is None or key_fn(lbl, sc) > key_fn(finished[0], finished[1]):
                    finished = (lbl, sc)

        best_labels = finished[0] if finished is not None else beam_hyps[0][0]
        return list(best_labels[1:])


# ---------------------------------------------------------------------------
# Helpers for static buckets & CoreML export
# ---------------------------------------------------------------------------
def subsampled_len(frames: int) -> int:
    for _ in range(3):
        frames = (frames - 1) // 2 + 1
    return frames


def pad_and_make_masks(mel: np.ndarray, target_mel_len: int) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
    t_valid_mel = mel.shape[0]
    t_valid_enc = subsampled_len(t_valid_mel)
    t_bucket_enc = subsampled_len(target_mel_len)
    mel_pad = np.zeros((1, 1, target_mel_len, 80), dtype=np.float32)
    mel_pad[0, 0, :t_valid_mel, :] = mel
    attn_mask = np.full((1, 1, 1, t_bucket_enc), -1e4, dtype=np.float32)
    attn_mask[0, 0, 0, :t_valid_enc] = 0.0
    conv_mask = np.zeros((1, 1, t_bucket_enc), dtype=np.float32)
    conv_mask[0, 0, :t_valid_enc] = 1.0
    return mel_pad, attn_mask, conv_mask, t_valid_enc


def export_bucket(
    pt_model: FastConformerEncoder,
    t_mel: int,
    out_dir: Path,
    tag: str,
    *,
    w8a16_only: bool = False,
) -> tuple[Path, Path]:
    t_enc = subsampled_len(t_mel)
    ex_mel = torch.zeros((1, 1, t_mel, 80), dtype=torch.float32)
    ex_am = torch.zeros((1, 1, 1, t_enc), dtype=torch.float32)
    ex_cm = torch.ones((1, 1, t_enc), dtype=torch.float32)

    with torch.no_grad():
        traced = torch.jit.trace(pt_model, (ex_mel, ex_am, ex_cm))

    fp16_pkg = out_dir / f"fc_{tag}_fp16.mlpackage"
    if fp16_pkg.exists():
        shutil.rmtree(fp16_pkg)
    ml_fp16 = ct.convert(
        traced,
        inputs=[
            ct.TensorType(name="mel", shape=(1, 1, t_mel, 80), dtype=np.float32),
            ct.TensorType(name="attn_mask", shape=(1, 1, 1, t_enc), dtype=np.float32),
            ct.TensorType(name="conv_mask", shape=(1, 1, t_enc), dtype=np.float32),
        ],
        outputs=[ct.TensorType(name="enc_proj")],
        convert_to="mlprogram",
        compute_precision=ct.precision.FLOAT16,
        minimum_deployment_target=ct.target.macOS15,
    )
    ml_fp16.save(str(fp16_pkg))

    w8_pkg = out_dir / f"fc_{tag}_w8a16.mlpackage"
    if w8_pkg.exists():
        shutil.rmtree(w8_pkg)
    op_cfg = ct.optimize.coreml.OpLinearQuantizerConfig(mode="linear_symmetric", dtype="int8", granularity="per_channel")
    q_cfg = ct.optimize.coreml.OptimizationConfig(global_config=op_cfg)
    ml_w8 = ct.optimize.coreml.linear_quantize_weights(ml_fp16, config=q_cfg)
    ml_w8.save(str(w8_pkg))

    compile_pkgs = (w8_pkg,) if w8a16_only else (fp16_pkg, w8_pkg)
    for pkg in compile_pkgs:
        subprocess.run(
            ["xcrun", "coremlcompiler", "compile", str(pkg), str(out_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    if w8a16_only:
        shutil.rmtree(fp16_pkg, ignore_errors=True)
        shutil.rmtree(w8_pkg, ignore_errors=True)
    return fp16_pkg, w8_pkg


def load_mlmodel(pkg_path: Path, compute_units: ct.ComputeUnit):
    return ct.models.MLModel(str(pkg_path), compute_units=compute_units)


def run_ane_bucket(model, mel_pad: np.ndarray, am: np.ndarray, cm: np.ndarray) -> np.ndarray:
    out = model.predict({"mel": mel_pad, "attn_mask": am, "conv_mask": cm})
    return np.asarray(out["enc_proj"], dtype=np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-export", action="store_true")
    parser.add_argument("--export-prod-only", action="store_true")
    parser.add_argument("--out-dir", type=Path, default=REPO_ROOT / ".model-cache/nemo-ane")
    args = parser.parse_args()

    meta, tensors = load_gguf(GGUF_PATH)
    if args.export_prod_only:
        out_dir = args.out_dir.resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        pt_tanh = FastConformerEncoder(meta, tensors, use_tanh=True).eval()
        prod_buckets = [("S256", 256), ("M560", 560), ("L1024", 1024)]
        print(f"=== Exporting Production W8A16 CoreML Buckets to {out_dir} ===")
        for bname, t_mel in prod_buckets:
            t0 = time.perf_counter()
            export_bucket(pt_tanh, t_mel, out_dir, bname, w8a16_only=True)
            print(f"  Compiled fc_{bname}_w8a16.mlmodelc (T_mel={t_mel}, T_enc={subsampled_len(t_mel)}) in {time.perf_counter()-t0:.1f}s")
        return

    frontend = NemoFrontend(meta, tensors)
    detok = Detokenizer(meta)
    dec = CpuRnntDecoder(meta, tensors)

    npz = np.load(SLICES_NPZ)
    meta_json = json.loads(META_JSON.read_text(encoding="utf-8"))
    slices_meta = meta_json["slices"]
    d0_units = meta_json["d0_units"]
    d1_ref_text = meta_json["d1_ref_text"]
    sanity_refs = meta_json["sanity_refs"]
    silence_audio = npz["silence_audio"]

    # 1. PyTorch Reference Parity Gate on first 5 slices (exact silu vs tanh-silu)
    print("=== [1/4] PyTorch Reference Parity Gate (5 sanity slices) ===")
    pt_exact = FastConformerEncoder(meta, tensors, use_tanh=False).eval()
    pt_tanh = FastConformerEncoder(meta, tensors, use_tanh=True).eval()

    sanity_results = []
    max_tanh_diff = 0.0
    for i in range(5):
        audio = npz[f"audio_{i}"]
        pad = np.zeros(PAD_SAMPLES, dtype=np.float32)
        padded = np.concatenate([pad, audio, pad])
        mel = frontend.features(padded)
        t_enc = subsampled_len(mel.shape[0])
        m_in = torch.from_numpy(mel[None, None, :, :])
        am = torch.zeros((1, 1, 1, t_enc), dtype=torch.float32)
        cm = torch.ones((1, 1, t_enc), dtype=torch.float32)
        with torch.no_grad():
            out_exact = pt_exact(m_in, am, cm).squeeze(0).numpy()
            out_tanh = pt_tanh(m_in, am, cm).squeeze(0).numpy()
        diff = float(np.max(np.abs(out_exact - out_tanh)))
        max_tanh_diff = max(max_tanh_diff, diff)
        txt_exact = detok.text(dec.decode_beam(out_exact))
        txt_tanh = detok.text(dec.decode_beam(out_tanh))
        cer_vs_ref = float(compute_cer(txt_tanh, sanity_refs[i], strip_speakers=False)["cer"])
        sanity_results.append({
            "idx": i,
            "mel_frames": int(mel.shape[0]),
            "enc_frames": int(t_enc),
            "speech_cpp_ref": sanity_refs[i],
            "pt_exact": txt_exact,
            "pt_tanh": txt_tanh,
            "cer_vs_speech_cpp": cer_vs_ref,
            "max_abs_tanh_diff": round(diff, 5),
        })
        print(f"  Slice[{i}] cer_vs_speech_cpp={cer_vs_ref:.4f} max_tanh_diff={diff:.4f}")
        print(f"    ref : {sanity_refs[i]}")
        print(f"    tanh: {txt_tanh}")

    mean_sanity_cer = statistics.mean(r["cer_vs_speech_cpp"] for r in sanity_results)
    print(f"  Mean parity CER vs speech.cpp: {mean_sanity_cer:.4f} (max_tanh_diff={max_tanh_diff:.4f})")

    # Choose 3 static single-window buckets (T_enc <= 128 == attention_context):
    # S256 (<=2.56s), M512 (<=5.12s, matches prod 4.5s+pad), L1024 (<=10.24s, covers 87/91 frozen slices)
    BUCKETS = [("S256", 256), ("M512", 512), ("L1024", 1024)]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    if not args.skip_export:
        print("\n=== [2/4] Exporting FP16 & W8A16 Static-Bucket CoreML Models ===")
        for bname, t_mel in BUCKETS:
            t0 = time.perf_counter()
            export_bucket(pt_tanh, t_mel, MODEL_DIR, bname)
            print(f"  Exported & compiled {bname} (T_mel={t_mel}, T_enc={subsampled_len(t_mel)}) in {time.perf_counter()-t0:.1f}s")

    # 3. Compute Unit Sweep + Wired Memory + Keepalive on Bucket M512
    print("\n=== [3/4] ComputeUnit Sweep, Wired Memory & Sleep/Keepalive Probe ===")
    # Pick a slice that fits inside M512 (256 < mel <= 512)
    m512_idx, mel2 = None, None
    for idx in range(len(slices_meta)):
        pad_i = np.zeros(PAD_SAMPLES, dtype=np.float32)
        m_i = frontend.features(np.concatenate([pad_i, npz[f"audio_{idx}"], pad_i]))
        if 256 < m_i.shape[0] <= 512:
            m512_idx, mel2 = idx, m_i
            break
    assert mel2 is not None
    m_pad, am_pad, cm_pad, t_enc2 = pad_and_make_masks(mel2, 512)
    with torch.no_grad():
        ref_m512_txt = detok.text(dec.decode_beam(
            pt_tanh(
                torch.from_numpy(mel2[None, None, :, :]),
                torch.zeros((1, 1, 1, t_enc2), dtype=torch.float32),
                torch.ones((1, 1, t_enc2), dtype=torch.float32),
            ).squeeze(0).numpy()
        ))
    print(f"  Using slice[{m512_idx}] (mel={mel2.shape[0]}) for M512 ComputeUnit sweep")

    cu_bench = {}
    for prec in ("fp16", "w8a16"):
        pkg = MODEL_DIR / f"fc_M512_{prec}.mlpackage"
        cu_bench[prec] = {}
        for cu_name, cu in [
            ("CPU_ONLY", ct.ComputeUnit.CPU_ONLY),
            ("CPU_AND_GPU", ct.ComputeUnit.CPU_AND_GPU),
            ("CPU_AND_NE", ct.ComputeUnit.CPU_AND_NE),
            ("ALL", ct.ComputeUnit.ALL),
        ]:
            w0 = wired_mb()
            mdl = load_mlmodel(pkg, cu)
            w_load = wired_mb()
            _ = run_ane_bucket(mdl, m_pad, am_pad, cm_pad)
            w_eval = wired_mb()
            times = []
            for _ in range(10):
                t0 = time.perf_counter()
                out_m = run_ane_bucket(mdl, m_pad, am_pad, cm_pad)
                times.append((time.perf_counter() - t0) * 1000.0)
            p50 = round(statistics.median(times), 2)
            p95 = round(percentile(times, 0.95), 2)
            txt = detok.text(dec.decode_beam(out_m[0, :t_enc2, :]))
            cer_s2 = round(float(compute_cer(txt, ref_m512_txt, strip_speakers=False)["cer"]), 4)
            cu_bench[prec][cu_name] = {
                "p50_ms": p50,
                "p95_ms": p95,
                "wired_load_delta_mb": round(w_load - w0, 1),
                "wired_eval_delta_mb": round(w_eval - w_load, 1),
                "slice2_cer": cer_s2,
            }
            print(f"  {prec:5s} {cu_name:11s}: p50={p50:6.2f}ms p95={p95:6.2f}ms wired_delta={w_eval-w0:+6.1f}MB cer_s2={cer_s2:.4f}")
            del mdl

    # Choose best precision for ANE based on latency & slice2_cer
    best_prec = "w8a16" if cu_bench["w8a16"]["CPU_AND_NE"]["slice2_cer"] <= 0.05 and cu_bench["w8a16"]["CPU_AND_NE"]["p50_ms"] <= cu_bench["fp16"]["CPU_AND_NE"]["p50_ms"] else "fp16"
    print(f"  Selected ANE precision: {best_prec}")

    # Load all 4 buckets on CPU_AND_NE
    w_before_all = wired_mb()
    ane_models = {}
    bucket_warm_ms = {}
    for bname, t_mel in BUCKETS:
        mdl = load_mlmodel(MODEL_DIR / f"fc_{bname}_{best_prec}.mlpackage", ct.ComputeUnit.CPU_AND_NE)
        ane_models[t_mel] = mdl
        dm, dam, dcm, _ = pad_and_make_masks(np.zeros((min(t_mel, 128), 80), dtype=np.float32), t_mel)
        _ = run_ane_bucket(mdl, dm, dam, dcm)
        ts = []
        for _ in range(10):
            t0 = time.perf_counter()
            _ = run_ane_bucket(mdl, dm, dam, dcm)
            ts.append((time.perf_counter() - t0) * 1000.0)
        bucket_warm_ms[bname] = {"p50_ms": round(statistics.median(ts), 2), "p95_ms": round(percentile(ts, 0.95), 2)}
        print(f"  Warm bucket {bname:6s}: p50={bucket_warm_ms[bname]['p50_ms']:.2f}ms")
    w_after_all = wired_mb()

    # Sleep 2.0s vs Keepalive (Bucket S256 ping at 1.0s)
    dm_s, dam_s, dcm_s, _ = pad_and_make_masks(np.zeros((128, 80), dtype=np.float32), 256)
    cold_wake_ms = []
    for _ in range(5):
        time.sleep(2.0)
        t0 = time.perf_counter()
        _ = run_ane_bucket(ane_models[512], m_pad, am_pad, cm_pad)
        cold_wake_ms.append(round((time.perf_counter() - t0) * 1000.0, 2))
    keepalive_ms = []
    for _ in range(5):
        time.sleep(1.0)
        _ = run_ane_bucket(ane_models[256], dm_s, dam_s, dcm_s)
        time.sleep(1.0)
        t0 = time.perf_counter()
        _ = run_ane_bucket(ane_models[512], m_pad, am_pad, cm_pad)
        keepalive_ms.append(round((time.perf_counter() - t0) * 1000.0, 2))
    print(f"  2s Sleep cold-wake M512: {cold_wake_ms} (med={statistics.median(cold_wake_ms):.2f}ms)")
    print(f"  2s Sleep + Keepalive   : {keepalive_ms} (med={statistics.median(keepalive_ms):.2f}ms)")

    # 4. Full 91-slice + silence benchmark (both w8a16 and fp16 on CPU_AND_NE)
    print("\n=== [4/4] Full 91 Frozen Slices + Silence Benchmark (w8a16 & fp16) ===")
    bucket_lens = [t_mel for _, t_mel in BUCKETS]

    def run_full_91(models_map: dict[int, object]) -> dict:
        rows_beam, rows_greedy = [], []
        fe_times, enc_times, dec_beam_times, dec_greedy_times = [], [], [], []
        tot_beam_times, tot_greedy_times = [], []
        ane_enc_times = []
        cpu_fallback_slices = 0

        for i, sm in enumerate(slices_meta):
            audio = npz[f"audio_{i}"]
            pad = np.zeros(PAD_SAMPLES, dtype=np.float32)
            padded = np.concatenate([pad, audio, pad])
            t_f0 = time.perf_counter()
            mel = frontend.features(padded)
            t_fe = (time.perf_counter() - t_f0) * 1000.0

            t_bucket = next((b for b in bucket_lens if b >= mel.shape[0]), None)
            if t_bucket is not None:
                mp, am, cm, t_valid_enc = pad_and_make_masks(mel, t_bucket)
                t_e0 = time.perf_counter()
                enc_out = run_ane_bucket(models_map[t_bucket], mp, am, cm)[0, :t_valid_enc, :]
                t_enc = (time.perf_counter() - t_e0) * 1000.0
                ane_enc_times.append(t_enc)
            else:
                cpu_fallback_slices += 1
                t_valid_enc = subsampled_len(mel.shape[0])
                t_e0 = time.perf_counter()
                with torch.no_grad():
                    enc_out = pt_tanh(
                        torch.from_numpy(mel[None, None, :, :]),
                        torch.zeros((1, 1, 1, t_valid_enc), dtype=torch.float32),
                        torch.ones((1, 1, t_valid_enc), dtype=torch.float32),
                    ).squeeze(0).numpy()
                t_enc = (time.perf_counter() - t_e0) * 1000.0

            t_db0 = time.perf_counter()
            ids_b = dec.decode_beam(enc_out)
            txt_b = detok.text(ids_b)
            t_db = (time.perf_counter() - t_db0) * 1000.0

            t_dg0 = time.perf_counter()
            ids_g = dec.decode_greedy(enc_out)
            txt_g = detok.text(ids_g)
            t_dg = (time.perf_counter() - t_dg0) * 1000.0

            fe_times.append(t_fe)
            enc_times.append(t_enc)
            dec_beam_times.append(t_db)
            dec_greedy_times.append(t_dg)
            tot_beam_times.append(t_fe + t_enc + t_db)
            tot_greedy_times.append(t_fe + t_enc + t_dg)

            rows_beam.append(dict(sm, text_ja=txt_b))
            rows_greedy.append(dict(sm, text_ja=txt_g))

        d0_cer_beam = score_d0_cer(rows_beam, d0_units)
        d1_cer_beam = compute_cer("".join(r["text_ja"] for r in rows_beam if r["clip"] == "d1-ep01-45s"), d1_ref_text, strip_speakers=True)
        d0_cer_greedy = score_d0_cer(rows_greedy, d0_units)
        d1_cer_greedy = compute_cer("".join(r["text_ja"] for r in rows_greedy if r["clip"] == "d1-ep01-45s"), d1_ref_text, strip_speakers=True)

        sil_9s = silence_audio[: 9 * RATE]
        sil_pad = np.concatenate([np.zeros(PAD_SAMPLES, dtype=np.float32), sil_9s, np.zeros(PAD_SAMPLES, dtype=np.float32)])
        sil_mel = frontend.features(sil_pad)
        sil_b = next(b for b in bucket_lens if b >= sil_mel.shape[0])
        smp, sam, scm, st_enc = pad_and_make_masks(sil_mel, sil_b)
        senc = run_ane_bucket(models_map[sil_b], smp, sam, scm)[0, :st_enc, :]
        sil_beam_txt = detok.text(dec.decode_beam(senc))
        sil_greedy_txt = detok.text(dec.decode_greedy(senc))

        return {
            "ane_slices_count": len(ane_enc_times),
            "cpu_fallback_slices_count": cpu_fallback_slices,
            "frontend_ms": {"p50": round(statistics.median(fe_times), 2), "p95": round(percentile(fe_times, 0.95), 2)},
            "encoder_ane_only_ms": {"p50": round(statistics.median(ane_enc_times), 2), "p95": round(percentile(ane_enc_times, 0.95), 2)},
            "encoder_all_ms": {"p50": round(statistics.median(enc_times), 2), "p95": round(percentile(enc_times, 0.95), 2)},
            "decoder_beam_ms": {"p50": round(statistics.median(dec_beam_times), 2), "p95": round(percentile(dec_beam_times, 0.95), 2)},
            "decoder_greedy_ms": {"p50": round(statistics.median(dec_greedy_times), 2), "p95": round(percentile(dec_greedy_times, 0.95), 2)},
            "total_beam_ms": {"p50": round(statistics.median(tot_beam_times), 2), "p95": round(percentile(tot_beam_times, 0.95), 2)},
            "total_greedy_ms": {"p50": round(statistics.median(tot_greedy_times), 2), "p95": round(percentile(tot_greedy_times, 0.95), 2)},
            "beam": {"d0_cer": d0_cer_beam, "d1_cer": d1_cer_beam, "silence": sil_beam_txt},
            "greedy": {"d0_cer": d0_cer_greedy, "d1_cer": d1_cer_greedy, "silence": sil_greedy_txt},
        }

    res_best = run_full_91(ane_models)
    other_prec = "fp16" if best_prec == "w8a16" else "w8a16"
    other_models = {
        t_mel: load_mlmodel(MODEL_DIR / f"fc_{bname}_{other_prec}.mlpackage", ct.ComputeUnit.CPU_AND_NE)
        for bname, t_mel in BUCKETS
    }
    res_other = run_full_91(other_models)

    summary = {
        "sanity_5_slices": sanity_results,
        "mean_sanity_cer": round(mean_sanity_cer, 4),
        "compute_unit_bench_m512": cu_bench,
        "selected_precision": best_prec,
        "bucket_warm_ane_ms": bucket_warm_ms,
        "wired_all_3_buckets_delta_mb": round(w_after_all - w_before_all, 1),
        "sleep_2s_cold_wake_ms": cold_wake_ms,
        "sleep_2s_keepalive_ms": keepalive_ms,
        "cold_wake_median_ms": round(statistics.median(cold_wake_ms), 2),
        "keepalive_median_ms": round(statistics.median(keepalive_ms), 2),
        f"frozen_91_slices_{best_prec}": res_best,
        f"frozen_91_slices_{other_prec}": res_other,
        "baseline_stage3_mtl0_q8": {"total_p50_ms": 137.8, "d0_cer": 0.1523, "d1_cer": 0.2934},
    }

    ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ARCHIVE_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "selected_precision": best_prec,
        "bucket_warm_ane_ms": bucket_warm_ms,
        "cold_wake_median_ms": summary["cold_wake_median_ms"],
        "keepalive_median_ms": summary["keepalive_median_ms"],
        f"frozen_91_{best_prec}": res_best,
        f"frozen_91_{other_prec}": res_other,
    }, ensure_ascii=False, indent=2))
    print(f"\nSaved {ARCHIVE_PATH}")


if __name__ == "__main__":
    main()
