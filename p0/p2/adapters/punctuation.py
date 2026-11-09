"""Character-only ONNX punctuation with explicit, reversible insertion offsets."""

MARKS = ("", "、", "。", "？")


def remove_insertions(text, insertions):
    offsets = {item["output_offset"]: item["mark"] for item in insertions}
    for offset, mark in offsets.items():
        if text[offset] != mark:
            raise ValueError("punctuation insertion proof mismatch")
    return "".join(c for i, c in enumerate(text) if i not in offsets)


def restore_with_labels(text, predict):
    output, insertions, chunks = [], [], []
    emitted = 0
    while emitted < len(text):
        start = max(0, emitted - 16)
        end = min(len(text), start + 510)
        labels = list(predict(text[start:end]))
        if len(labels) != end-start or any(int(k) not in range(4) for k in labels):
            raise ValueError("invalid punctuation labels")
        chunks.append([start, end])
        for index in range(emitted, end):
            output.append(text[index])
            mark = MARKS[int(labels[index-start])]
            if mark:
                insertions.append(dict(after_source_offset=index, output_offset=len(output), mark=mark))
                output.append(mark)
        emitted = end
    restored = "".join(output)
    if remove_insertions(restored, insertions) != text:
        raise ValueError("punctuation changed original characters")
    return restored, dict(insertions=insertions, chunks=chunks)


class Punctuation:
    def __init__(self, snapshot):
        import onnxruntime as ort

        self.vocab = {word: i for i, word in enumerate((snapshot / "vocab.txt").read_text().split("\n"))}
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(snapshot / "punct.int8.onnx"), sess_options=options,
                                           providers=["CPUExecutionProvider"])

    def restore_punctuation(self, text_ja):
        import numpy as np

        def predict(chunk):
            v = self.vocab
            ids = np.array([[v["[CLS]"], *(v.get(c, v["[UNK]"]) for c in chunk), v["[SEP]"]]], dtype=np.int64)
            logits = self.session.run(None, dict(input_ids=ids, attention_mask=np.ones_like(ids)))[0]
            return logits[0, 1:-1].argmax(-1)

        return restore_with_labels(text_ja, predict)
