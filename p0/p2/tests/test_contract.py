from p0.p2.events import validate_causality
import pytest


def test_model_cannot_consume_audio_that_has_not_arrived():
    errors = validate_causality([
        {"event": "pcm_accepted", "session_id": "s", "epoch": 0, "t_ns": 1, "accepted_samples": 3200},
        {"event": "pcm_consumed", "session_id": "s", "epoch": 0, "t_ns": 2, "consumed_samples": 6400},
    ])
    assert any("future" in error for error in errors)


def test_unresolved_model_revision_prevents_preflight():
    from p0.p2.freeze import validate_lock
    with pytest.raises(ValueError, match="revision"):
        validate_lock({"models": [{"id": "new-model", "source": "hf", "revision": None,
                                    "files": []}]})


@pytest.mark.parametrize("tail", [511, 512, 513])
def test_packet_scheduler_preserves_real_short_tail_without_padding(tail):
    from p0.p2.runner import pcm_packets
    audio = bytes((3200 + tail) * 2)
    packets = list(pcm_packets(audio))
    assert [len(frame) // 2 for _, frame in packets] == [3200, tail]
    assert packets[-1][0] == 3200 + tail
    assert b"".join(frame for _, frame in packets) == audio


def test_cancelled_epoch_cannot_emit_late_output():
    errors = validate_causality([
        {"event": "pcm_accepted", "session_id": "s", "epoch": 0, "t_ns": 1, "accepted_samples": 3200},
        {"event": "cancel", "session_id": "s", "epoch": 0, "t_ns": 2},
        {"event": "caption_emit", "session_id": "s", "epoch": 0, "t_ns": 3,
         "start_sample": 0, "end_sample": 1000, "seq": 1},
    ])
    assert any("cancellation" in error for error in errors)


def test_artificial_silence_does_not_expand_the_output_range():
    errors = validate_causality([
        {"event": "pcm_accepted", "session_id": "s", "epoch": 0, "t_ns": 1, "accepted_samples": 3200},
        {"event": "caption_emit", "session_id": "s", "epoch": 0, "t_ns": 2,
         "start_sample": 0, "end_sample": 4000, "seq": 1},
    ])
    assert any("accepted audio" in error for error in errors)


def test_one_frame_subtitle_is_not_counted_as_readable():
    from p0.p2.scoring import readable_cues
    result = readable_cues(
        [{"session_id": "s", "seq": 1, "text_zh": "这是中文字幕"}],
        [{"session_id": "s", "seq": 1, "stage": "dom_visible", "t": 1000},
         {"session_id": "s", "seq": 1, "stage": "dom_removed", "t": 1017}])
    assert result[0]["readable"] is False


def test_missing_dom_removal_is_unknown_not_a_success():
    from p0.p2.scoring import readable_cues
    result = readable_cues(
        [{"session_id": "s", "seq": 1, "text_zh": "这是中文字幕"}],
        [{"session_id": "s", "seq": 1, "stage": "dom_visible", "t": 1000}])
    assert result[0]["readable"] is False
    assert result[0]["duration_ms"] is None


def test_downstream_operations_reject_changed_completed_outputs(tmp_path):
    import json
    from p0.p2.freeze import digest, frozen_fingerprint
    locks = tmp_path / "locks"
    locks.mkdir()
    source = tmp_path / "model.bin"
    source.write_bytes(b"model")
    (locks / "models.json").write_text(json.dumps({"models": [
        {"id": "fixture", "source": "local", "files": [{"path": str(source), "sha256": digest(source)}]}]}))
    (locks / "runtime.json").write_text('{"packages": {}}')
    (tmp_path / "run-manifest.json").write_text(json.dumps({
        "source_sha256": {}, "input_sha256": {}, "artifact_sha256": {}}))
    output = tmp_path / "translated.jsonl"
    output.write_text("original result")
    (tmp_path / "state.json").write_text(json.dumps({"steps": {"fixed-text": {
        "status": "passed", "outputs": {output.name: digest(output)}}}}))
    output.write_text("changed after the step completed")
    with pytest.raises(ValueError, match="completed output"):
        frozen_fingerprint(tmp_path)


def test_pcm_observer_preserves_bytes_and_does_not_log_start_token(tmp_path, monkeypatch):
    import asyncio
    import json
    from p0.p2.server import PCMTrace
    trace = tmp_path / "trace.jsonl"
    monkeypatch.setenv("SUBTITLE_BENCH_TRACE", str(trace))
    messages = iter([{"type": "websocket.receive", "text": '{"token":"private-test-value"}'},
                     {"type": "websocket.receive", "bytes": b"\x01\x02\x03\x04"}])
    forwarded = []
    async def receive():
        return next(messages)
    async def send(message):
        forwarded.append(message)
    async def app(scope, incoming, outgoing):
        assert (await incoming())["text"] == '{"token":"private-test-value"}'
        await outgoing({"type": "websocket.send", "text": '{"type":"ready","session_id":"s"}'})
        assert (await incoming())["bytes"] == b"\x01\x02\x03\x04"
    asyncio.run(PCMTrace(app)({"type": "websocket"}, receive, send))
    assert "private-test-value" not in trace.read_text()
    observed = json.loads(trace.read_text())
    assert observed["packet_samples"] == 2 and observed["end_sample"] == 2
    assert forwarded == [{"type": "websocket.send", "text": '{"type":"ready","session_id":"s"}'}]
