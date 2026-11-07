import CoreML
import Foundation
import WhisperKit

private func readExactly(_ size: Int) throws -> Data? {
    var result = Data()
    while result.count < size {
        guard let chunk = try FileHandle.standardInput.read(upToCount: size - result.count), !chunk.isEmpty else {
            if result.isEmpty {
                return nil
            }
            throw CocoaError(.fileReadCorruptFile)
        }
        result.append(chunk)
    }
    return result
}

private func send(_ payload: [String: Any]) throws {
    var data = try JSONSerialization.data(withJSONObject: payload)
    data.append(0x0a)
    try FileHandle.standardOutput.write(contentsOf: data)
}

@main
struct Probe {
    static func main() async throws {
        let arguments = CommandLine.arguments
        guard arguments.count == 3 else {
            throw CocoaError(.fileReadInvalidFileName)
        }
        let encoderDecoder: MLComputeUnits = ProcessInfo.processInfo.environment["P0_WHISPERKIT_COMPUTE"] == "gpu"
            ? .cpuAndGPU : .cpuAndNeuralEngine
        let audit = ProcessInfo.processInfo.environment["P0_WHISPERKIT_AUDIT"] == "1" ? AuditSeeker() : nil
        let kit = try await WhisperKit(WhisperKitConfig(
            model: "small",
            modelFolder: arguments[1],
            tokenizerFolder: URL(fileURLWithPath: arguments[2], isDirectory: true),
            computeOptions: ModelComputeOptions(
                melCompute: .cpuAndGPU,
                audioEncoderCompute: encoderDecoder,
                textDecoderCompute: encoderDecoder
            ),
            segmentSeeker: audit,
            verbose: false,
            load: true,
            download: false
        ))
        var options = DecodingOptions(
            task: .transcribe,
            language: "ja",
            temperatureFallbackCount: 0,
            skipSpecialTokens: true
        )
        if let value = ProcessInfo.processInfo.environment["P0_WHISPERKIT_WINDOW_CLIP"], let seconds = Float(value) {
            options.windowClipTime = seconds
        }
        try send(["ready": true])
        // Each request is a little-endian sample count followed by float32 PCM.
        while let header = try readExactly(4) {
            let count = Int(header.withUnsafeBytes { $0.loadUnaligned(as: UInt32.self).littleEndian })
            guard (1...1_920_000).contains(count), let pcm = try readExactly(count * 4) else {
                throw CocoaError(.fileReadCorruptFile)
            }
            let samples = pcm.withUnsafeBytes { raw in
                (0..<count).map { index in
                    let bits = raw.loadUnaligned(fromByteOffset: index * 4, as: UInt32.self).littleEndian
                    return Float(bitPattern: bits)
                }
            }
            audit?.windows.removeAll()
            let results = try await kit.transcribe(audioArray: samples, decodeOptions: options)
            var payload: [String: Any] = ["text": results.map(\.text).joined().trimmingCharacters(in: .whitespacesAndNewlines)]
            if let audit {
                payload["audit"] = [
                    "samples": count, "window_clip_time": options.windowClipTime,
                    "windows": audit.windows,
                    "encoder_calls": results.map { $0.timings.totalEncodingRuns },
                    "decoder_loops": results.map { $0.timings.totalDecodingLoops },
                    "result_texts": results.map(\.text)
                ]
            }
            try send(payload)
        }
    }
}
