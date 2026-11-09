import pytest
from p0.p2.b3_protocol import ChineseProtocol, StreamCommitter, validate_audio


def test_official_ast_protocol_rejects_missing_duplicate_and_truncation():
    parse=ChineseProtocol()
    assert parse.parse('これは説明です。\nChinese: 这是说明。','stop')['filtered_zh']=='这是说明。'
    for raw in ('只有中文。','日文\nChinese: 中文\nChinese: 重复','\nChinese: 中文','日文\nChinese: '):
        assert parse.parse(raw,'stop')['filtered_zh'] is None
    assert parse.parse('日文\nChinese: 中文','length')['filtered_zh'] is None
    assert parse.parse('日文\nChinese: 中文<turn|>','stop')['filtered_zh'] is None


def test_audio_shape_duration_and_feature_are_not_file_text():
    import numpy as np
    for value in (np.zeros((16,2),np.float32),np.zeros(480001,np.float32),np.array([float('nan')],np.float32)):
        with pytest.raises(ValueError):validate_audio(value)
    assert validate_audio(np.zeros(16000,np.float32))==16000


def test_commit_waits_release_and_decoder_not_ingested_and_no_backfill():
    c=StreamCommitter([dict(segment_id=1,start_sample=0,end_sample=4000,released_at_sample=8000)])
    assert c.advance(accepted=6400,frontier=6000,pieces=[(2000,'前半'),(5000,'後半')])==[]
    assert c.advance(accepted=8000,frontier=3000,pieces=[])==[]
    got=c.advance(accepted=8000,frontier=6400,pieces=[])
    assert len(got)==1 and got[0]['text_ja']=='前半' and got[0]['end_sample']==4000
    tail=c.advance(accepted=9600,frontier=9600,pieces=[],eof=True)
    assert len(tail)==1 and tail[0]['text_ja']=='後半' and tail[0]['start_sample']==4000
    assert c.advance(accepted=9600,frontier=9600,pieces=[],eof=True)==[]


def test_multiple_pending_endpoints_coalesce_only_unsubmitted():
    cuts=[dict(segment_id=i,start_sample=(i-1)*4000,end_sample=i*4000,released_at_sample=i*4000+2000) for i in (1,2)]
    c=StreamCommitter(cuts)
    got=c.advance(accepted=12000,frontier=12000,pieces=[(2000,'一'),(6000,'二')])
    assert len(got)==1 and got[0]['endpoint_ids']==[1,2] and got[0]['text_ja']=='一二'
    assert c.last_committed_piece==2


def test_future_pieces_rejected_and_empty_eof_never_zero_span():
    c=StreamCommitter([])
    with pytest.raises(ValueError,match='future'):c.advance(accepted=100,frontier=100,pieces=[(101,'未来')])
    assert StreamCommitter([]).advance(accepted=100,frontier=100,pieces=[],eof=True)==[]


def test_causal_native_reader_keeps_session_and_never_feeds_future(tmp_path,monkeypatch):
    import sys
    import json
    import numpy as np
    from types import SimpleNamespace
    from p0.p2.b3_worker import vox_session
    monkeypatch.setitem(sys.modules,'mlx.core',SimpleNamespace(synchronize=lambda:None,get_active_memory=lambda:0,get_peak_memory=lambda:0))
    class Detok:
        text='';pending=''
        def add_token(self,t):self.text+='あ';self.pending+='あ'
        def finalize(self):pass
        @property
        def last_segment(self):v=self.pending;self.pending='';return v
    class Session:
        def __init__(self):
            self._detokenizer=Detok();self.generated=[];self._audio_q=[];self.done=False;self._audio_closed=False
            self._n_left=32;self._n_delay=12;self._prompt_len=45;self.ready=False
        def feed(self,v):self._audio_q.append(v);self.ready=True
        def close(self):self._audio_closed=True
        def step(self,*,max_decode_tokens):
            assert 0<max_decode_tokens<=4
            self._audio_q.clear()
            if self._audio_closed:self.done=True;return []
            if not self.ready:return []
            self.ready=False;self.generated.append(1000);self._detokenizer.add_token(1000)
            return [self._detokenizer.last_segment]
    sessions=[]
    def create(**kwargs):s=Session();sessions.append(s);return s
    pcm=tmp_path/'a.pcm';pcm.write_bytes(np.arange(6400,dtype='<i2').tobytes())
    records,result=vox_session(SimpleNamespace(create_streaming_session=create,_tokenizer=SimpleNamespace(token_bytes=lambda token:'あ'.encode())),pcm,[], 'a',tmp_path)
    assert len(sessions)==1 and result['samples']==6400 and result['finish_reason']=='audio_exhausted_no_eos'
    assert result['text']=='ああ' and all(r['text_ja']=='' for r in records)
    events=[json.loads(x) for x in (tmp_path/'native-events.jsonl').read_text().splitlines()]
    feeds=[e for e in events if e['event']=='feed']
    assert [e['end_sample'] for e in feeds]==[3200,6400]
    assert all(e.get('decoder_frontier_sample',0)<=e.get('accepted_samples',6400) for e in events)
    import weakref
    reference=weakref.ref(sessions[0]);sessions.clear()
    assert reference() is None, 'observer must not retain a completed native session/KV'


def test_utf8_spanning_endpoint_is_held_then_owned_once_with_full_interval():
    c=StreamCommitter([dict(segment_id=1,start_sample=1000,end_sample=4000,released_at_sample=8000)])
    assert c.advance(accepted=8000,frontier=6000,pieces=[],pending_text_start=3840)==[]
    output=c.advance(accepted=8000,frontier=6400,pieces=[(3840,5120,'字')])
    assert output[0]['text_ja']=='字' and output[0]['end_sample']==5120
    assert c.last_committed_piece==1


def test_final_vad_flush_endpoint_waits_native_termination_and_owns_suffix():
    c=StreamCommitter([dict(segment_id=1,start_sample=0,end_sample=6400,released_at_sample=6400,flush=True)])
    assert c.advance(accepted=6400,frontier=6400,pieces=[(1280,2560,'前')])==[]
    assert c.advance(accepted=6400,frontier=6400,pieces=[(5120,6400,'尾')])==[]
    output=c.advance(accepted=6400,frontier=6400,pieces=[],eof=True)
    assert output[0]['text_ja']=='前尾' and output[0]['end_sample']==6400
