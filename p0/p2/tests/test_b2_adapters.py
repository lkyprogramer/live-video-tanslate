from types import SimpleNamespace

import pytest

from p0.p2.adapters.sensevoice import clean_result
from p0.p2.adapters.whisper_ja import collect_segments, generation_trace, validate_files
from p0.p2.freeze import validate_lock
from p0.p2 import b2


def test_control_tags_never_become_translation_text():
    text, metadata = clean_result("<|ja|><|NEUTRAL|><|Speech|>名前を読みます。<|unknown|>",
                                  dict(lang="<|ja|>", emotion="<|NEUTRAL|>", event="<|Speech|>"))
    assert text == "名前を読みます。"
    assert metadata["tags"] == ["ja", "NEUTRAL", "Speech", "unknown"]
    assert metadata["emotion"] == "<|NEUTRAL|>"
    assert metadata["unknown_tags"] == ["unknown"]
    assert clean_result("", {})[0] == ""
    text,metadata=clean_result("説明<|BGM|><|/BGM|>続く",{},allowed_tags={"BGM","/BGM"})
    assert text=="説明続く" and not metadata["unknown_tags"]
    with pytest.raises(ValueError, match="malformed"):
        clean_result("日文<|broken", {})


def test_whisper_does_not_fallback_to_generic_tokenizer(tmp_path):
    with pytest.raises(ValueError, match="tokenizer.json"):
        validate_files(tmp_path)


def test_lazy_generator_must_finish_before_returning_text():
    consumed=[]
    def segments():
        consumed.append(1)
        yield SimpleNamespace(text="名前", _asdict=lambda:dict(text="名前"))
        consumed.append(2)
        yield SimpleNamespace(text="を読みます", _asdict=lambda:dict(text="を読みます"))
    text, values=collect_segments(segments())
    assert text=="名前を読みます" and len(values)==2 and consumed==[1,2]
    def failing():
        yield SimpleNamespace(text="部分", _asdict=lambda:dict(text="部分"))
        raise RuntimeError("late decode failure")
    with pytest.raises(RuntimeError,match="late decode"):
        collect_segments(failing())


def test_native_budget_and_no_speech_filter_are_explicit():
    options=SimpleNamespace(max_new_tokens=256,no_speech_threshold=.6,log_prob_threshold=-1.0)
    result=SimpleNamespace(sequences_ids=[[5]*130],no_speech_prob=.9)
    trace=generation_trace(result,[8632,8640,8734,8738],options,lambda ids:"原始日文",-1.2,0,1.1)
    assert trace["native_decode_cap"]==130
    assert trace["length_boundary_unconfirmed"] and trace["no_speech_filtered"]
    assert trace["raw_text"]=="原始日文"
    result.sequences_ids=[[5]*128]
    assert not generation_trace(result,[8632,8640,8734,8738],options,lambda ids:"文",-.5,0,1)["length_boundary_unconfirmed"]
    result.sequences_ids=[[5]*129]
    assert not generation_trace(result,[8632,8640,8734,8738],options,lambda ids:"文",-.5,0,1)["length_boundary_unconfirmed"]


def test_release_cannot_borrow_runtime_commit_as_content_lock():
    with pytest.raises(ValueError,match="release content"):
        validate_lock(dict(models=[dict(id="sensevoice",source="release",revision="a"*40,files=[])]))


def test_changed_text_seed_stays_bound_to_original_span():
    row=dict(clip="sample",start_sample=0,end_sample=16000,text_ja="日文",mt_seed_index=8)
    assert b2.seed_index(row,"日文")==8
    assert b2.seed_index(row,"変更")>=100000
    assert b2.seed_index(row,"変更")==b2.seed_index(dict(row,mt_seed_index=77),"変更")


def test_failed_asr_blocks_mt_even_with_lingering_output(tmp_path):
    import json
    state=dict(steps={"B0-CURRENT-fixed-text":dict(status="passed",result=dict(parity={"11":True,"29":True})),
        "ASR-SENSEVOICE-I8-fixed-cuts":dict(status="failed")})
    (tmp_path/"state.json").write_text(json.dumps(state))
    output=tmp_path/"ASR-SENSEVOICE-I8";output.mkdir();(output/"reazon.jsonl").write_text("old partial")
    with pytest.raises(ValueError,match="ASR prerequisite"):
        b2.probe(tmp_path,"ASR-SENSEVOICE-I8","fixed-text")
