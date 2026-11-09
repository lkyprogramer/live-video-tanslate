"""Frozen B3 boundary rules. Decoder intervals are estimates, never word timestamps."""
from hashlib import sha256
import re
import unicodedata


def validate_audio(samples):
    import numpy as np
    if samples is None:raise ValueError('audio required')
    if samples.ndim!=1 or samples.dtype!=np.float32 or not 0<len(samples)<=480000:
        raise ValueError('expected nonempty mono float32 audio <=30s at16kHz')
    if not np.isfinite(samples).all() or np.abs(samples).max()>1:
        raise ValueError('nonfinite or out of range waveform')
    return len(samples)


class ChineseProtocol:
    def parse(self, raw, finish_reason):
        markers=list(re.finditer(r'^Chinese: ',raw,re.MULTILINE))
        status='valid';ja='';zh=''
        if finish_reason!='stop':status='unfinished_generation'
        elif len(markers)!=1:status='missing_or_duplicate_marker'
        else:
            marker=markers[0];ja=raw[:marker.start()].strip();zh=raw[marker.end():].strip()
            if not ja or not zh:status='empty_bilingual_field'
            elif '<' in raw or '>' in raw:status='leaked_control_or_markup'
            elif not re.search(r'[\u4e00-\u9fff]',unicodedata.normalize('NFKC',zh)) or re.search(r'[\u3040-\u30ff]',unicodedata.normalize('NFKC',zh)):
                status='not_valid_chinese'
        return dict(input_ja=ja,text_zh=zh,filtered_zh=zh if status=='valid' else None,protocol_status=status)


class StreamCommitter:
    """Release only observed endpoints; consume each detokenized delta once."""
    def __init__(self,cuts):
        self.cuts=cuts;self.endpoint=0;self.pieces=[];self.last_committed_piece=0;self.last_end=0

    def advance(self,*,accepted,frontier,pieces,eof=False,pending_text_start=None):
        if frontier>accepted or any((p[-2] if len(p)==3 else p[0])>accepted for p in pieces):raise ValueError('future source interval')
        self.pieces.extend((p if len(p)==3 else (p[0],p[0],p[1])) for p in pieces)
        pending=[]
        while self.endpoint<len(self.cuts):
            cut=self.cuts[self.endpoint]
            if (cut.get('flush',False) and not eof) or cut['released_at_sample']>accepted or cut['end_sample']>frontier or (pending_text_start is not None and pending_text_start<cut['end_sample'] and not eof):break
            pending.append(cut);self.endpoint+=1
        if pending:
            start=min(self.last_end or pending[0]['start_sample'],pending[0]['start_sample'])
            end=pending[-1]['end_sample']
        elif eof and self.last_committed_piece<len(self.pieces) and self.last_end<accepted:
            start=self.last_end;end=accepted
        else:return []
        last=self.last_committed_piece
        while last<len(self.pieces) and (self.pieces[last][1]<=end or self.pieces[last][0]<end or eof):last+=1
        selected=self.pieces[self.last_committed_piece:last]
        text=''.join(t for _,_,t in selected)
        if selected:
            start=min(start,min(a for a,_,_ in selected));end=max(end,max(b for _,b,_ in selected))
        item=dict(start_sample=start,end_sample=end,text_ja=text,endpoint_ids=[c['segment_id'] for c in pending],
            coalesced=len(pending)>1,first_piece=self.last_committed_piece,last_piece=last,
            eof=eof,alignment='estimated_decoder_interval; not acoustic word timing')
        self.last_committed_piece=last;self.last_end=end
        return [item]


def source_seed(row,original):
    for old in original:
        if all(row[k]==old[k] for k in ('clip','start_sample','end_sample','text_ja')):return old['mt_seed_index']
    import json
    return int(sha256(json.dumps(tuple(row[k] for k in ('clip','start_sample','end_sample','text_ja')),ensure_ascii=False).encode()).hexdigest()[:7],16)+100000
