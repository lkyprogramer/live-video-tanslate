"""Compare WhisperKit Core ML and ReazonSpeech through the same v1 replay."""

import argparse
import asyncio
from contextlib import suppress
import hashlib
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys

import numpy as np
import sherpa_onnx
import uvicorn

from review import P0
from subtitle_service import replay
from subtitle_service.app import FRAME_BYTES, create_app
from subtitle_service.backend import BackendError
from subtitle_service.cli import token_path
from subtitle_service.config import load_settings, load_token
from subtitle_service.local_pipeline import HyTranslator, LocalBackend, RATE, ReazonRecognizer, VAD_SHA, silero_config


class WhisperKitRecognizer(ReazonRecognizer):
    # Reuse the exact VAD, sample ranges and trace events from the control.
    def __init__(self, command, vad_path, pause_ms):
        self.sherpa = sherpa_onnx
        self.vad_config = silero_config(vad_path, pause_ms)
        self.worker = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        try:
            if self.receive(100) != {"ready": True}:
                raise RuntimeError("WhisperKit did not report readiness")
            audio = (P0 / "fixtures/first-photo.pcm").read_bytes()[:3 * RATE * 2]
            self.decode(np.frombuffer(audio, dtype="<i2").astype(np.float32) / 32768)
            self.new_vad()
        except Exception:
            self.close()
            raise

    def receive(self, timeout):
        if not select.select([self.worker.stdout], [], [], timeout)[0]:
            raise TimeoutError("WhisperKit response timed out")
        line = self.worker.stdout.readline()
        if not line:
            raise RuntimeError("WhisperKit exited without a response")
        return json.loads(line)

    def decode(self, samples):
        pcm = np.asarray(samples, dtype="<f4").tobytes()
        try:
            self.worker.stdin.write(struct.pack("<I", len(samples)) + pcm)
            self.worker.stdin.flush()
            return self.receive(20)["text"]
        except Exception:
            self.close()
            raise

    def close(self):
        if not self.worker.stdin.closed:
            with suppress(BrokenPipeError):
                self.worker.stdin.close()
            try:
                self.worker.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.worker.kill()
                self.worker.wait()
            self.worker.stdout.close()


class WhisperKitBackend(LocalBackend):
    def __init__(self, cache, profile, command):
        super().__init__(cache, profile)
        self.command = command
        self.missing = [path for path in self.missing if path.parent != self.asr_path]

    async def warmup(self):
        if self.missing:
            raise BackendError("profile_unavailable", "not_ready")
        async with self.lock:
            if self.translator is not None:
                return
            if hashlib.sha256(self.vad_path.read_bytes()).hexdigest() != VAD_SHA:
                raise ValueError("Silero VAD checksum mismatch")
            loop = asyncio.get_running_loop()
            if self.recognizer is None:
                self.recognizer = await loop.run_in_executor(
                    self.asr_pool, WhisperKitRecognizer, self.command, self.vad_path, self.profile.asr.vad_pause_ms,
                )
            self.translator = await loop.run_in_executor(self.mt_pool, HyTranslator, self.mt_path)

    async def aclose(self):
        await super().aclose()
        if self.recognizer is not None:
            await asyncio.to_thread(self.recognizer.close)


def serve(config_path):
    config = load_settings(config_path)
    token = load_token(token_path(), create=True)
    profile = config.profiles[config.default_profile_id]
    command = [os.environ[key] for key in ("P0_WHISPERKIT_BIN", "P0_WHISPERKIT_MODEL", "P0_WHISPERKIT_TOKENIZER")]
    backend = WhisperKitBackend(Path(config.cache_root), profile, command)
    app = create_app(config, token, {config.default_profile_id: backend})
    uvicorn.run(app, host="127.0.0.1", port=config.server.port, workers=1, proxy_headers=False,
                access_log=False, ws="websockets-sansio", ws_max_size=FRAME_BYTES * 2)


if __name__ == "__main__":
    if sys.argv[1:2] == ["--serve"]:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--serve", action="store_true")
        parser.add_argument("--config", type=Path, required=True)
        serve(parser.parse_args().config)
    else:
        replay.main(server_command=[sys.executable, str(Path(__file__).resolve()), "--serve"])
