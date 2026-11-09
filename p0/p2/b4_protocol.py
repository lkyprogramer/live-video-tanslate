"""Frozen B4 single-variable output protocol and slot accounting."""
import re
import unicodedata


ZH_PROMPT='Translate the Japanese speech in the audio into Simplified Chinese. Output only the translation. Do not invent speech that is absent.'


class ChineseOnlyProtocol:
    def parse(self,raw,finish_reason):
        zh=raw.strip();normalized=unicodedata.normalize('NFKC',zh);status='valid'
        if finish_reason!='stop':status='unfinished_generation'
        elif not zh:status='empty_translation'
        elif '<' in raw or '>' in raw or '```' in raw:status='leaked_control_or_markup'
        elif re.search(r'^(?:Chinese|Japanese|Translation|中文(?:翻译)?|日文|译文|翻译)\s*[:：]',normalized,re.MULTILINE|re.IGNORECASE):status='unexpected_output_label'
        elif not re.search(r'[\u4e00-\u9fff]',normalized) or re.search(r'[\u3040-\u30ff]',normalized):status='not_valid_chinese'
        return dict(input_ja='',text_zh=zh,filtered_zh=zh if status=='valid' else None,protocol_status=status)


def reserve_slot(registry,experiment):
    slots=registry['policy']['b4_slots']
    row=next(r for r in registry['experiments'] if r['id']==experiment)
    if slots.count(experiment)>1:raise ValueError('duplicate B4 reservation')
    if experiment in slots:index=slots.index(experiment)
    else:
        if None not in slots:raise ValueError('B4 slots full')
        index=slots.index(None);slots[index]=experiment
    label=f'B4-{index+1}';row['formal_slot']=label
    return label


def stream_chinese_time(events,protocol):
    raw=''
    for event in events:
        raw+=event['text'];body=raw
        if protocol=='official':
            marker=re.search(r'^Chinese: ',raw,re.MULTILINE)
            if marker is None:continue
            body=raw[marker.end():]
        if re.search(r'[\u4e00-\u9fff]',body):return event['t_s']
    return None
