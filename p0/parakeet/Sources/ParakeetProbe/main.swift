import FluidAudio
import Foundation

struct Slice: Decodable {
    let clip: String
    let segment_id: Int
    let start_sample: Int
    let end_sample: Int
    let pcm_path: String
}

struct Result: Encodable {
    let clip: String
    let segment_id: Int
    let start_sample: Int
    let end_sample: Int
    let text_ja: String
    let decode_s: Double
}

struct ProbeError: Error {
    let message: String
}

let args = CommandLine.arguments
guard args.count == 4 else {
    throw ProbeError(message: "Usage: ParakeetProbe MODEL_DIR SLICES_JSONL OUTPUT_JSONL")
}
let modelPath = URL(fileURLWithPath: args[1])
let models = try AsrModels.loadLocal(from: modelPath, version: .tdtJa)
let manager = AsrManager(models: models)
do {
    var warmup = try TdtDecoderState(decoderLayers: 2)
    _ = try await manager.transcribe([Float](repeating: 0, count: 16_000), decoderState: &warmup)
}

let input = try String(contentsOfFile: args[2], encoding: .utf8)
let decoder = JSONDecoder()
let encoder = JSONEncoder()
let output = URL(fileURLWithPath: args[3])
try Data().write(to: output, options: .withoutOverwriting)
let handle = try FileHandle(forWritingTo: output)
defer { try? handle.close() }

var loadedPath = ""
var pcm = Data()
for line in input.split(whereSeparator: \.isNewline) {
    let cut = try decoder.decode(Slice.self, from: Data(line.utf8))
    if loadedPath != cut.pcm_path {
        pcm = try Data(contentsOf: URL(fileURLWithPath: cut.pcm_path))
        loadedPath = cut.pcm_path
    }
    guard cut.start_sample >= 0, cut.start_sample < cut.end_sample, cut.end_sample <= pcm.count / 2 else {
        throw ProbeError(message: "Invalid sample range for \(cut.clip)")
    }
    let samples = pcm.withUnsafeBytes { bytes in
        (cut.start_sample..<cut.end_sample).map { index in
            Float(Int16(littleEndian: bytes.loadUnaligned(fromByteOffset: index * 2, as: Int16.self))) / 32768
        }
    }
    var state = try TdtDecoderState(decoderLayers: 2)
    let start = ProcessInfo.processInfo.systemUptime
    let text = try await manager.transcribe(samples, decoderState: &state).text.trimmingCharacters(in: .whitespacesAndNewlines)
    let elapsed = ProcessInfo.processInfo.systemUptime - start
    let result = Result(clip: cut.clip, segment_id: cut.segment_id, start_sample: cut.start_sample,
                        end_sample: cut.end_sample, text_ja: text, decode_s: elapsed)
    try handle.write(contentsOf: encoder.encode(result) + Data([10]))
}
