import Foundation
import Translation

struct ProbeError: Error {
    let message: String
}

let args = CommandLine.arguments
let source = Locale.Language(identifier: "ja")
let target = Locale.Language(identifier: "zh-Hans")
let status = await LanguageAvailability().status(from: source, to: target)
guard status == .installed else { throw ProbeError(message: "ja → zh-Hans is \(status)") }
guard args.count == 3 else { throw ProbeError(message: "Usage: apple-translation INPUT_JSONL OUTPUT_JSONL") }
let session = TranslationSession(installedSource: source, target: target)
_ = try await session.translate("日本語の音声を簡体字中国語に翻訳します。")

let input = try String(contentsOfFile: args[1], encoding: .utf8)
let output = URL(fileURLWithPath: args[2])
try Data().write(to: output, options: .withoutOverwriting)
let file = try FileHandle(forWritingTo: output)
defer { try? file.close() }
for line in input.split(whereSeparator: \.isNewline) {
    guard var row = try JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any],
          let ja = (row["text_ja"] ?? row["source_ja"]) as? String else {
        throw ProbeError(message: "Invalid input row")
    }
    let start = ProcessInfo.processInfo.systemUptime
    let zh = ja.isEmpty ? "" : try await session.translate(ja).targetText
    row["mt_s"] = ProcessInfo.processInfo.systemUptime - start
    row["input_ja"] = ja
    row["text_zh"] = zh
    try file.write(contentsOf: JSONSerialization.data(withJSONObject: row) + Data([10]))
}
