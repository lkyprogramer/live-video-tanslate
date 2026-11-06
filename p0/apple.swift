import AVFoundation
import Foundation
import Speech

@main
struct AppleReplay {
    static func emit(_ event: [String: Any]) throws {
        var line = try JSONSerialization.data(withJSONObject: event, options: [.sortedKeys])
        line.append(0x0A)
        try FileHandle.standardOutput.write(contentsOf: line)
    }

    static func main() async throws {
        let args = CommandLine.arguments
        let sentenceFinals = args.count == 3 && args[2] == "--sentence-finals"
        guard args.count == 2 || sentenceFinals else {
            throw NSError(domain: "Usage: apple-replay <pcm path | --install> [--sentence-finals]", code: 1)
        }
        let transcriber = SpeechTranscriber(
            locale: Locale(identifier: "ja-JP"), preset: .timeIndexedProgressiveTranscription
        )
        if args[1] == "--install" {
            if let request = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
                try await request.downloadAndInstall()
            }
            let status = await AssetInventory.status(forModules: [transcriber])
            try emit(["type": "assets", "status": String(describing: status)])
            guard status == .installed else {
                throw NSError(domain: "Japanese SpeechTranscriber asset unavailable", code: 2)
            }
            return
        }

        let rate: Int32 = 16000
        let pcm = try Data(contentsOf: URL(fileURLWithPath: args[1]))
        guard !pcm.isEmpty, pcm.count.isMultiple(of: 2),
              let format = await transcriber.availableCompatibleAudioFormats.first(where: {
                  $0.sampleRate == Double(rate) && $0.channelCount == 1 && $0.commonFormat == .pcmFormatInt16
              })
        else { throw NSError(domain: "Expected nonempty 16 kHz mono s16le PCM", code: 3) }

        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let (input, feed) = AsyncStream.makeStream(of: AnalyzerInput.self)
        let preparation = DispatchTime.now().uptimeNanoseconds
        try await analyzer.prepareToAnalyze(in: format)
        try await analyzer.start(inputSequence: input)

        let anchor = DispatchTime.now().uptimeNanoseconds
        let elapsed: @Sendable () -> Double = {
            Double(DispatchTime.now().uptimeNanoseconds - anchor) / 1_000_000_000
        }
        try emit([
            "type": "start", "prepare_s": Double(anchor - preparation) / 1_000_000_000,
            "sentence_finals": sentenceFinals,
        ])
        let receiver = Task {
            var finalizedThrough = 0.0
            var candidate: (start: Double, end: CMTime, text: String)?
            for try await result in transcriber.results {
                let arrived = elapsed()
                let start = result.range.start.seconds
                let end = result.range.end.seconds
                let text = String(result.text.characters)
                try emit([
                    "type": "asr", "t": arrived, "final": result.isFinal,
                    "start": start.isFinite ? start as Any : NSNull(),
                    "end": end.isFinite ? end as Any : NSNull(),
                    "ja": text,
                ])
                guard sentenceFinals else { continue }
                if result.isFinal {
                    finalizedThrough = max(finalizedThrough, end)
                    candidate = nil
                    continue
                }
                // Use the first result ending in punctuation as the approximate audio boundary.
                // Require the same prefix after more audio before requesting finalization.
                if let pending = candidate {
                    if pending.start != start || !text.hasPrefix(pending.text) {
                        candidate = nil
                    } else if end - pending.end.seconds >= 0.6 {
                        candidate = nil
                        finalizedThrough = pending.end.seconds
                        try emit(["type": "checkpoint", "through": finalizedThrough, "t": elapsed()])
                        try await analyzer.finalize(through: pending.end)
                    }
                }
                if candidate == nil, end >= max(start, finalizedThrough) + 1,
                   let last = text.last, "。！？".contains(last) {
                    candidate = (start, result.range.end, text)
                }
            }
        }

        var maxLate = 0.0
        for offset in stride(from: 0, to: pcm.count, by: 6400) {
            let end = min(offset + 6400, pcm.count)
            let frameCount = AVAudioFrameCount((end - offset) / 2)
            let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)!
            buffer.frameLength = frameCount
            pcm.copyBytes(to: UnsafeMutableRawBufferPointer(
                start: buffer.int16ChannelData![0], count: end - offset
            ), from: offset..<end)
            let due = Double(end) / Double(rate * 2)
            let delay = due - elapsed()
            if delay > 0 { try await Task.sleep(for: .seconds(delay)) }
            let timestamp = CMTime(value: Int64(offset / 2), timescale: rate)
            if case .terminated = feed.yield(AnalyzerInput(buffer: buffer, bufferStartTime: timestamp)) {
                throw NSError(domain: "SpeechAnalyzer stopped consuming audio", code: 4)
            }
            maxLate = max(maxLate, elapsed() - due)
        }
        let inputEnd = elapsed()
        feed.finish()
        try await analyzer.finalizeAndFinishThroughEndOfInput()
        try await receiver.value
        try emit([
            "type": "eof", "t": elapsed(), "input_end": inputEnd,
            "input_bytes": pcm.count, "max_feed_late_ms": maxLate * 1000,
        ])
    }
}
