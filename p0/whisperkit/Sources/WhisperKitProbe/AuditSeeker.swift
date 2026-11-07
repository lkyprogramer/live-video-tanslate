import CoreML
import Foundation
import WhisperKit

// Preserve the fixed library's seek logic while exposing pre-cleanup decoder output.
final class AuditSeeker: SegmentSeeking {
    private let original = SegmentSeeker()
    var windows: [[String: Any]] = []

    func findSeekPointAndSegments(
        decodingResult: DecodingResult, options: DecodingOptions, allSegmentsCount: Int,
        currentSeek: Int, segmentSize: Int, sampleRate: Int, timeToken: Int,
        specialToken: Int, tokenizer: WhisperTokenizer
    ) -> (Int, [TranscriptionSegment]?) {
        let result = original.findSeekPointAndSegments(
            decodingResult: decodingResult, options: options, allSegmentsCount: allSegmentsCount,
            currentSeek: currentSeek, segmentSize: segmentSize, sampleRate: sampleRate,
            timeToken: timeToken, specialToken: specialToken, tokenizer: tokenizer
        )
        windows.append([
            "seek": currentSeek, "segment_size": segmentSize, "next_seek": result.0,
            "raw_tokens": decodingResult.tokens, "raw_text": decodingResult.text,
            "word_text": tokenizer.decode(tokens: decodingResult.tokens.filter { $0 < specialToken }),
            "segments": result.1?.map(\.text) ?? [], "segments_nil": result.1 == nil,
            "no_speech_prob": decodingResult.noSpeechProb,
            "decoder_loops": decodingResult.timings?.totalDecodingLoops ?? 0
        ])
        return result
    }

    func addWordTimestamps(
        segments: [TranscriptionSegment], alignmentWeights: MLMultiArray,
        tokenizer: WhisperTokenizer, seek: Int, segmentSize: Int,
        prependPunctuations: String, appendPunctuations: String,
        lastSpeechTimestamp: Float, options: DecodingOptions, timings: TranscriptionTimings
    ) throws -> [TranscriptionSegment]? {
        try original.addWordTimestamps(
            segments: segments, alignmentWeights: alignmentWeights, tokenizer: tokenizer,
            seek: seek, segmentSize: segmentSize, prependPunctuations: prependPunctuations,
            appendPunctuations: appendPunctuations, lastSpeechTimestamp: lastSpeechTimestamp,
            options: options, timings: timings
        )
    }
}
