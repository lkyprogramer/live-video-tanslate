"""Author CT2 Japanese checkpoint, explicit tokenizer, lazy decode and cap evidence."""
import json


def validate_files(snapshot):
    for name in ("tokenizer.json","vocabulary.json","config.json","model.bin","preprocessor_config.json"):
        if not (snapshot/name).is_file():
            raise ValueError("Missing dedicated Whisper file: "+name)
    from tokenizers import Tokenizer
    tokenizer=Tokenizer.from_file(str(snapshot/"tokenizer.json"))
    vocab=json.loads((snapshot/"vocabulary.json").read_text())
    if len(vocab)!=10240 or tokenizer.get_vocab_size()!=10240:
        raise ValueError("Unexpected custom vocabulary size")
    if any(vocab[index]!=token for token,index in tokenizer.get_vocab().items()):
        raise ValueError("CT2 and tokenizer vocabulary mismatch")
    expected={"<|endoftext|>":8631,"<|startoftranscript|>":8632,"<|ja|>":8640,
              "<|transcribe|>":8734,"<|notimestamps|>":8738}
    if any(tokenizer.token_to_id(token)!=index for token,index in expected.items()):
        raise ValueError("Unexpected Japanese checkpoint special tokens")
    return dict(vocabulary_size=len(vocab),special_ids=expected,timestamp_begin=8739)


def collect_segments(generator):
    values=[segment._asdict() for segment in generator]
    return "".join(s["text"] for s in values).strip(),values


def generation_trace(result,prompt,options,decode,avg_logprob,temperature,compression_ratio):
    ids=list(result.sequences_ids[0]);total=len(prompt)+options.max_new_tokens
    # CT2v4.8.5 appends before testing the final step and removes only a real EOS.
    cap=min(total//2,total-(len(prompt)-1))
    boundary=len(ids)>=cap
    skipped=(result.no_speech_prob>options.no_speech_threshold and avg_logprob<=options.log_prob_threshold)
    return dict(token_ids=ids,prompt_token_ids=list(prompt),native_total_max_length=total,native_decode_cap=cap,
        generated_tokens=len(ids),raw_text=decode(ids),no_speech_prob=result.no_speech_prob,
        termination_basis="fixed_CT2_v4.8.5_source_inference_EOS_excluded",
        no_speech_filtered=skipped,avg_logprob=avg_logprob,temperature=temperature,compression_ratio=compression_ratio,
        length_boundary_unconfirmed=boundary,finish_reason="length_boundary_unconfirmed" if boundary else "native_returned_eos_not_exposed")


class WhisperJapanese:
    def __init__(self,snapshot):
        import ctranslate2
        from faster_whisper import WhisperModel
        from packaging.version import Version
        if Version(ctranslate2.__version__)<Version("4.7.1"):
            raise ValueError("Dedicated vocabulary requires CT2>=4.7.1")
        supported=ctranslate2.get_supported_compute_types("cpu")
        if "int8" not in supported:
            raise ValueError("CPU int8 unsupported; no precision fallback")
        vocabulary=validate_files(snapshot)
        class TracedWhisper(WhisperModel):
            def generate_with_fallback(self,encoder_output,prompt,tokenizer,options):
                result=super().generate_with_fallback(encoder_output,prompt,tokenizer,options)
                self.decode_trace.append(generation_trace(result[0],prompt,options,tokenizer.decode,*result[1:]))
                return result
        self.model=TracedWhisper(str(snapshot),device="cpu",compute_type="int8",cpu_threads=2,num_workers=1,local_files_only=True)
        actual=self.model.model.compute_type
        if actual not in ("int8","int8_float32"):
            raise ValueError("Unexpected actual compute type: "+actual)
        self.settings=dict(device="cpu",requested_compute_type="int8",actual_compute_type=actual,
            supported_compute_types=sorted(supported),cpu_threads=2,num_workers=1,**vocabulary,
            language="ja",task="transcribe",beam_size=5,temperature=0,condition_on_previous_text=False,
            vad_filter=False,without_timestamps=True,max_new_tokens=256,suppress_tokens=[-1],suppress_blank=True,
            no_speech_threshold=.6,log_prob_threshold=-1.0,compression_ratio_threshold=2.4)

    def decode(self,samples):
        self.model.decode_trace=[]
        options={key:self.settings[key] for key in ("language","task","beam_size","temperature","condition_on_previous_text",
            "vad_filter","without_timestamps","max_new_tokens","suppress_tokens","suppress_blank","no_speech_threshold",
            "log_prob_threshold","compression_ratio_threshold")}
        generator,info=self.model.transcribe(samples,**options)
        text,segments=collect_segments(generator)
        trace=self.model.decode_trace;truncated=any(t["length_boundary_unconfirmed"] for t in trace)
        return ("" if truncated else text),dict(raw_text=text,segments=segments,native_calls=trace,
            language=info.language,language_probability=info.language_probability,usable=not truncated,
            finish_reason="length_boundary_unconfirmed" if truncated else "generator_completed_eos_not_exposed",
            empty_reason="length_boundary" if truncated else "no_speech_filter" if not text and any(t["no_speech_filtered"] for t in trace)
                else "empty_decoded_text" if not text else None)
