"""Create a benchmark decoder with PengChengStarling's greedy language seed."""

import argparse
from pathlib import Path

import onnx
from onnx import TensorProto, helper


def prepare(snapshot, output, tag):
    tokens = {}
    for row in (snapshot / "tokens.txt").read_text().splitlines():
        token, _, index = row.rpartition(" ")
        tokens[token] = int(index)
    assert tokens["<blk>"] == 0 and tag in tokens
    model = onnx.load(next(snapshot.glob("decoder-*.onnx")))
    (input_info,) = model.graph.input
    name = input_info.name
    assert input_info.type.tensor_type.elem_type == TensorProto.INT64
    conditioned = "pengcheng_conditioned_input"
    for node in model.graph.node:
        for i, field in enumerate(node.input):
            if field == name:
                node.input[i] = conditioned
    model.graph.initializer.extend([
        helper.make_tensor("pengcheng_bos", TensorProto.INT64, [], [tokens["<blk>"]]),
        helper.make_tensor("pengcheng_language", TensorProto.INT64, [], [tokens[tag]]),
    ])
    # Greedy search seeds the decoder with a blank and never appends blank
    # to the context. The author uses the language token in that seed slot.
    model.graph.node.insert(0, helper.make_node("Equal", [name, "pengcheng_bos"], ["pengcheng_is_bos"]))
    model.graph.node.insert(
        1, helper.make_node("Where", ["pengcheng_is_bos", "pengcheng_language", name], [conditioned]),
    )
    onnx.checker.check_model(model)
    with output.open("xb") as target:
        target.write(model.SerializeToString())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--tag", default="<JA>", choices=["<JA>", "<EN>"])
    args = parser.parse_args()
    prepare(args.snapshot, args.output, args.tag)
