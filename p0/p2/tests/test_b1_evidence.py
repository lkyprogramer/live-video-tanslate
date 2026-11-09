import json

import pytest

from p0.p2 import b1, b1_scoring


def test_completed_output_tampering_blocks_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(b1, "verify", lambda run: {"fingerprint": "frozen"})
    out = tmp_path / "candidate"
    out.mkdir()
    def operation():
        (out / "data").write_text("original")
        return {"rows": 62}
    assert b1.execute(tmp_path, "probe", operation, out) == {"rows": 62}
    assert b1.execute(tmp_path, "probe", lambda: pytest.fail("must not rerun"), out)["resumed_verified"]
    # Verification of saved receipts is exercised through the real verifier below.
    monkeypatch.undo()
    state = json.loads((tmp_path / "state.json").read_text())
    manifest = dict(source_sha256={},artifact_sha256={},assets=[],runtime={"engine": b1.runtime()},fingerprint="frozen")
    (tmp_path / "run-manifest.json").write_text(json.dumps(manifest))
    (tmp_path / "locks").mkdir()
    (tmp_path / "locks/models.json").write_text('{"models": []}')
    (out / "data").write_text("tampered")
    assert state["steps"]["probe"]["status"] == "passed"
    with pytest.raises(ValueError, match="completed output drift"):
        b1.verify(tmp_path)


def test_new_critical_error_on_old_failed_unit_blocks_otherwise_improved_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(b1_scoring,"verify",lambda _:None)
    (tmp_path / "inputs").mkdir();(tmp_path / "reviews").mkdir()
    units=[dict(clip="clip",group=str(i)) for i in range(34)]
    (tmp_path / "inputs/units.jsonl").write_text("\n".join(map(json.dumps,units)))
    old={f"m0-{seed}":dict(usable=[f"clip/{i}" for i in range(count)]) for seed,count in ((11,20),(29,18))}
    (tmp_path / "inputs/verdict.json").write_text(json.dumps(dict(outcomes=old)))
    (tmp_path / "reference-manifest.json").write_text(json.dumps(dict(protected_unit_ids=[f"clip/{i}" for i in range(20)])))
    states={name+"-fixed-text":dict(status="passed") for name in b1.IDS}
    states["PAD-900-fixed-cuts"]=dict(status="passed")
    (tmp_path / "state.json").write_text(json.dumps(dict(steps=states)))
    reviews={}
    for experiment in b1.IDS:
        reviews[experiment]={str(seed):[dict(unit=f"clip/{i}",status="correct" if i<23 else "incorrect",
            new_critical_error=i==33,reason="fixture") for i in range(34)] for seed in (11,29)}
    (tmp_path / "reviews/adjudication.json").write_text(json.dumps(reviews))
    (tmp_path / "reviews/anonymous-packet.json").write_text("{}")
    (tmp_path / "run-manifest.json").write_text("{}")
    result=b1_scoring.score(tmp_path)
    for candidate in result["candidates"].values():
        assert candidate["rounds"]["11"]["net"]==3
        assert not candidate["Q"] and not candidate["R_offline_eligible"]
        assert candidate["rounds"]["11"]["new_critical_errors"][0]["unit"]=="clip/33"
    # Exact control reproduction must preserve R's same-round quality prerequisite,
    # including the two units seed29 did not originally get right.
    for experiment in b1.IDS:
        for seed,count in (("11",20),("29",18)):
            for i,item in enumerate(reviews[experiment][seed]):
                item.update(status="correct" if i<count else "incorrect",new_critical_error=False)
    (tmp_path / "reviews/adjudication.json").write_text(json.dumps(reviews))
    result=b1_scoring.score(tmp_path)
    assert all(c["R_offline_eligible"] and not c["Q"] for c in result["candidates"].values())
    state={name+"-fixed-text":dict(status="passed") for name in b1.IDS}
    state["PAD-900-fixed-cuts"]=dict(status="passed")
    state["PAD-900-fixed-text"]=dict(status="failed",error="short control failed after core outputs")
    (tmp_path / "state.json").write_text(json.dumps(dict(steps=state)))
    del reviews["PAD-900"]
    (tmp_path / "reviews/adjudication.json").write_text(json.dumps(reviews))
    candidate=b1_scoring.score(tmp_path)["candidates"]["PAD-900"]
    assert candidate["status"]=="failed" and not candidate["R_offline_eligible"]
    state["PAD-900-fixed-text"]=dict(status="passed")
    state["PAD-900-fixed-cuts"]=dict(status="failed",error="silence diagnostic timeout")
    (tmp_path / "state.json").write_text(json.dumps(dict(steps=state)))
    assert not b1_scoring.score(tmp_path)["candidates"]["PAD-900"]["R_offline_eligible"]
