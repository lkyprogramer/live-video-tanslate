"""Experiment-only service launcher with reproducible MT seeds and clock samples.

Production entry points/configuration are unchanged. Requires P2_B0_RUN and a seed.
"""
import hmac
import json
import os
from pathlib import Path
import time
from hashlib import sha256


class PCMTrace:
    """Observe incoming PCM bytes without changing the ASGI stream or logging tokens."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "websocket":
            return await self.app(scope, receive, send)
        sid, received, index = None, 0, 0

        async def observed_receive():
            nonlocal received, index
            message = await receive()
            if message.get("bytes") is not None:
                frame = message["bytes"]
                received += len(frame)//2
                index += 1
                with open(os.environ["SUBTITLE_BENCH_TRACE"], "a") as target:
                    target.write(json.dumps(dict(t=time.monotonic(), stage="wire_pcm_received", session_id=sid,
                        end_sample=received, frame_index=index, packet_samples=len(frame)//2,
                        pcm_sha256=sha256(frame).hexdigest()))+"\n")
            return message

        async def observed_send(message):
            nonlocal sid
            if message.get("text"):
                event = json.loads(message["text"])
                if event.get("type") == "ready":
                    sid = event["session_id"]
            await send(message)

        await self.app(scope, observed_receive, observed_send)


def main():
    import mlx.core as mx
    from fastapi import Request
    from fastapi.responses import JSONResponse
    import subtitle_service.cli as cli
    from subtitle_service.local_pipeline import LocalSession, ReazonRecognizer
    from .freeze import rows
    run = Path(os.environ["P2_B0_RUN"])
    seed = int(os.environ["P2_B0_SEED"])
    if seed not in (11, 29):
        raise ValueError("B0 requires paired seed 11 or 29")
    frozen = rows(run / "baseline/reazon.jsonl")
    indexes = {}
    for row in frozen:
        key = (row["start_sample"], row["end_sample"], row["text_ja"])
        if key in indexes:
            raise ValueError("ambiguous B0 seed mapping")
        indexes[key] = row["mt_seed_index"]
    original_translate = LocalSession.translate

    def seeded_translate(self, segment):
        key = (segment.start_sample, segment.end_sample, segment.text_ja)
        index = indexes.get(key)
        exact = index is not None
        if index is None:
            from hashlib import sha256
            index = int(sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()[:7], 16) + 100000
        mx.random.seed(seed + index)
        if self.record:
            self.record("mt_seed", segment_id=segment.segment_id, seed=seed + index,
                        baseline_seed_index=index, exact_frozen_input=exact)
        return original_translate(self, segment)

    LocalSession.translate = seeded_translate
    original_accept = LocalSession.accept_pcm
    original_segments = ReazonRecognizer.segments

    async def profiled_accept(self, frame):
        self._p2_job = getattr(self, "_p2_job", 0) + 1
        self.record("asr_enqueue", job_id=self._p2_job, eof=frame is None)
        return await original_accept(self, frame)

    def profiled_segments(self, vad, frame, segment_ids, record):
        connection = record.__self__
        connection._p2_job = getattr(connection, "_p2_job", 0) + 1
        job = connection._p2_job
        record("asr_job_start", job_id=job)
        try:
            return original_segments(self, vad, frame, segment_ids, record)
        finally:
            record("asr_job_end", job_id=job)

    LocalSession.accept_pcm = profiled_accept
    ReazonRecognizer.segments = profiled_segments
    original_create = cli.create_app

    def create(config, token, backends):
        app = original_create(config, token, backends)
        app.add_middleware(PCMTrace)

        async def clock(request: Request):
            if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + token):
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return {"monotonic_ns": time.monotonic_ns()}

        # Request is imported locally; FastAPI resolves the annotation in this function's globals.
        clock.__annotations__["request"] = Request
        app.add_api_route("/p2/clock", clock, methods=["GET"])
        return app

    cli.create_app = create
    cli.serve()


if __name__ == "__main__":
    main()
