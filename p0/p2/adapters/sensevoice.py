"""Fixed SenseVoice CPU configuration; metadata never enters the source body."""
import re


def clean_result(raw, fields, allowed_tags=()):
    tags=re.findall(r"<\|([^<>|]+)\|>",raw)
    text=re.sub(r"<\|[^<>|]+\|>","",raw).strip()
    if "<|" in text or "|>" in text:
        raise ValueError("malformed SenseVoice control tag")
    known=set(allowed_tags) | {"withitn","woitn"} | {tag for value in fields.values() for tag in re.findall(r"<\|([^<>|]+)\|>",value or "")}
    return text,fields | dict(tags=tags,unknown_tags=[tag for tag in tags if tag not in known],raw_text=raw)


class SenseVoice:
    def __init__(self,snapshot):
        import sherpa_onnx
        self.allowed_tags=set(re.findall(r"<\|([^<>|]+)\|>",(snapshot/"tokens.txt").read_text()))
        self.recognizer=sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(snapshot/"model.int8.onnx"),tokens=str(snapshot/"tokens.txt"),
            num_threads=2,provider="cpu",language="ja",use_itn=True,decoding_method="greedy_search")
        self.settings=dict(provider="cpu",threads=2,language="ja",use_itn=True,decoding_method="greedy_search",
                           effective_config=str(self.recognizer.config))

    def decode(self,samples):
        stream=self.recognizer.create_stream();stream.accept_waveform(16000,samples)
        self.recognizer.decode_stream(stream);result=stream.result
        fields={key:getattr(result,key,"") for key in ("lang","emotion","event")}
        if not any(fields.values()):
            raise RuntimeError("SenseVoice returned no metadata; possible swallowed native inference failure")
        text,metadata=clean_result(result.text,fields,self.allowed_tags)
        metadata.update(raw_result=str(result),finish_reason="unknown_tag_filtered" if metadata["unknown_tags"] else "offline_decode_returned",
                        usable=not metadata["unknown_tags"])
        return text,metadata
