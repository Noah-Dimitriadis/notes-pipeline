import Foundation
import Speech
import AVFoundation
import CoreMedia

struct Seg: Codable { let start: Double; let end: Double; let text: String }

func err(_ s: String) { FileHandle.standardError.write((s + "\n").data(using: .utf8)!) }

let args = CommandLine.arguments
guard args.count >= 2 else { err("usage: stt <audio> [out.json]"); exit(2) }
let url = URL(fileURLWithPath: args[1])
let outPath: String? = args.count >= 3 ? args[2] : nil

guard SpeechTranscriber.isAvailable else { err("SpeechTranscriber unavailable"); exit(1) }

let want = Locale(identifier: "en-US")
let locale = await SpeechTranscriber.supportedLocale(equivalentTo: want) ?? want
let installed = await SpeechTranscriber.installedLocales
err("locale: \(locale.identifier)  installed: \(installed.map(\.identifier))")

let transcriber = SpeechTranscriber(
    locale: locale,
    transcriptionOptions: [],
    reportingOptions: [],
    attributeOptions: [.audioTimeRange]
)

let status = await AssetInventory.status(forModules: [transcriber])
err("asset status: \(status)")
if let req = try await AssetInventory.assetInstallationRequest(supporting: [transcriber]) {
    err("downloading model...")
    try await req.downloadAndInstall()
    err("model installed")
}

let audioFile = try AVAudioFile(forReading: url)
let duration = Double(audioFile.length) / audioFile.fileFormat.sampleRate
err(String(format: "duration: %.2fs", duration))

let analyzer = SpeechAnalyzer(modules: [transcriber])

let collector = Task { () -> [Seg] in
    var out: [Seg] = []
    for try await r in transcriber.results {
        out.append(Seg(start: CMTimeGetSeconds(r.range.start),
                       end: CMTimeGetSeconds(r.range.end),
                       text: String(r.text.characters)))
    }
    return out
}

let t0 = Date()
_ = try await analyzer.analyzeSequence(from: audioFile)
try await analyzer.finalizeAndFinishThroughEndOfInput()
let segs = try await collector.value
let elapsed = Date().timeIntervalSince(t0)

err(String(format: "elapsed: %.2fs   rtf: %.2fx   segments: %d", elapsed, duration / elapsed, segs.count))

let enc = JSONEncoder()
enc.outputFormatting = [.prettyPrinted, .withoutEscapingSlashes]
let data = try enc.encode(segs)
if let outPath { try data.write(to: URL(fileURLWithPath: outPath)); err("wrote \(outPath)") }
else { FileHandle.standardOutput.write(data) }
