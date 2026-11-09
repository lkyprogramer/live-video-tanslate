import pytest

from p0.p2.b4_protocol import ChineseOnlyProtocol, reserve_slot, stream_chinese_time
from p0.p2.b3_protocol import ChineseProtocol


def test_chinese_only_has_no_fabricated_japanese_and_rejects_salvage():
    parser=ChineseOnlyProtocol()
    result=parser.parse('这是翻译。','stop')
    assert result['filtered_zh']=='这是翻译。' and result['input_ja']==''
    for raw in ('', 'Japanese: 説明\nChinese: 中文', 'Chinese: 中文', '中文：结果', '翻译: 结果', '```\n中文\n```', '中文<turn|>', 'これは日文です', 'only English'):
        assert parser.parse(raw,'stop')['filtered_zh'] is None
    assert parser.parse('这是部分译文','length')['filtered_zh'] is None
    assert parser.parse('这里的术语是Enforcing。','stop')['filtered_zh'] is not None
    assert ChineseProtocol().parse('説明\nChinese: 中文','stop')['filtered_zh']=='中文'


def test_slot_is_one_formal_configuration_even_after_new_attempt():
    registry=dict(policy=dict(b4_slots=[None,None,None]),experiments=[dict(id='AST-G4-ZH',formal_slot=None)])
    assert reserve_slot(registry,'AST-G4-ZH')=='B4-1'
    assert reserve_slot(registry,'AST-G4-ZH')=='B4-1'
    assert registry['policy']['b4_slots']==['AST-G4-ZH',None,None]
    registry['policy']['b4_slots']=['A','B','C']
    with pytest.raises(ValueError,match='full'):reserve_slot(registry,'AST-G4-ZH')


def test_readable_chinese_clock_does_not_count_japanese_or_lost_events():
    events=[dict(t_s=.1,text='日本語漢字'),dict(t_s=.2,text='\nChinese: '),dict(t_s=.3,text='中文')]
    assert stream_chinese_time(events,'official')==.3
    assert stream_chinese_time([dict(t_s=.1,text='Hello'),dict(t_s=.2,text='中文')],'zh_only')==.2
    assert stream_chinese_time([dict(t_s=.1,text='日本語漢字')],'official') is None
