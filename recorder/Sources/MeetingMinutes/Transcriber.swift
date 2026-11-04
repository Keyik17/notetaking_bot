import AVFoundation
import Foundation
import Speech

/// One piece of transcript. `start`/`end` are wall clock seconds (epoch).
/// Same shape the old pipeline saved in work/transcript.json, so older
/// sessions can be redone.
struct Segment: Codable {
    var track: String   // "system" (Zoom) or "mic" (room)
    var start: Double
    var end: Double
    var text: String
}

enum Transcriber {
    static let labels = ["system": "On Zoom", "mic": "In the room"]

    /// Reads chunks.jsonl: [(track, file, startEpoch)].
    static func chunks(in dir: URL) -> [(track: String, file: URL, start: Double)] {
        guard let text = try? String(contentsOf: dir.appendingPathComponent("chunks.jsonl"), encoding: .utf8) else {
            return []
        }
        var out: [(String, URL, Double)] = []
        for line in text.split(separator: "\n") {
            guard let data = line.data(using: .utf8),
                  let c = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
                  let track = c["track"] as? String, let file = c["file"] as? String,
                  let start = (c["start"] as? NSNumber)?.doubleValue else { continue } // half written line after a crash
            let url = file.hasPrefix("/") ? URL(fileURLWithPath: file) : dir.appendingPathComponent(file)
            if let size = (try? FileManager.default.attributesOfItem(atPath: url.path)[.size] as? NSNumber)?.intValue,
               size > 4096 {
                out.append((track, url, start))
            }
        }
        return out.sorted { $0.2 < $1.2 }
    }

    static func locale() async -> Locale {
        await SpeechTranscriber.supportedLocale(equivalentTo: Locale(identifier: "en-US")) ?? Locale(identifier: "en-US")
    }

    /// Downloads the speech model once if macOS does not have it yet.
    static func prepare(log: (String) -> Void) async throws {
        let t = SpeechTranscriber(locale: await locale(), preset: .timeIndexedTranscriptionWithAlternatives)
        if let req = try await AssetInventory.assetInstallationRequest(supporting: [t]) {
            log("downloading the speech model (one time)")
            try await req.downloadAndInstall()
        }
    }

    /// Transcribes one audio file; times are relative to the file start.
    static func transcribe(file url: URL) async throws -> [(Double, Double, String)] {
        let transcriber = SpeechTranscriber(locale: await locale(), preset: .timeIndexedTranscriptionWithAlternatives)
        let analyzer = SpeechAnalyzer(modules: [transcriber])
        let audio = try AVAudioFile(forReading: url)
        let collect = Task {
            var out: [(Double, Double, String)] = []
            for try await r in transcriber.results {
                let text = String(r.text.characters).trimmingCharacters(in: .whitespacesAndNewlines)
                if !text.isEmpty { out.append((r.range.start.seconds, r.range.end.seconds, text)) }
            }
            return out
        }
        if let last = try await analyzer.analyzeSequence(from: audio) {
            try await analyzer.finalizeAndFinish(through: last)
        } else {
            await analyzer.cancelAndFinishNow()
        }
        return try await collect.value
    }

    /// All tracks of a session, time sorted, echo removed.
    static func transcribeSession(_ dir: URL, log: (String) -> Void) async throws -> [Segment] {
        let all = chunks(in: dir)
        if all.isEmpty { return [] }
        try await prepare(log: log)
        var segs: [Segment] = []
        for (i, c) in all.enumerated() {
            if i % 10 == 0 { log("transcribing \(i + 1) of \(all.count) audio pieces") }
            do {
                for (a, b, t) in try await transcribe(file: c.file) {
                    segs.append(Segment(track: c.track, start: c.start + a, end: c.start + b, text: t))
                }
            } catch {
                log("skipped \(c.file.lastPathComponent): \(error.localizedDescription)")
            }
        }
        segs.sort { $0.start < $1.start }
        return dropEcho(segs)
    }

    // MARK: Cleanup and text

    static func words(_ s: String) -> [String] {
        s.lowercased().components(separatedBy: CharacterSet.alphanumerics.inverted).filter { !$0.isEmpty }
    }

    /// The room mic also hears the Zoom speakers. Drop room lines that
    /// repeat what the Zoom track said at the same moment.
    static func dropEcho(_ segs: [Segment]) -> [Segment] {
        let zoom = segs.filter { $0.track == "system" }
        if zoom.isEmpty { return segs }
        return segs.filter { s in
            guard s.track == "mic" else { return true }
            let w = words(s.text)
            if w.isEmpty { return false }
            let near = Set(zoom.filter { $0.end >= s.start - 3 && $0.start <= s.end + 3 }.flatMap { words($0.text) })
            if near.isEmpty { return true }
            let overlap = Double(w.filter { near.contains($0) }.count) / Double(w.count)
            return overlap < 0.7
        }
    }

    static func fmt(_ sec: Double) -> String {
        let s = max(0, Int(sec))
        return String(format: "%02d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
    }

    /// Readable lines; consecutive pieces from the same track are joined.
    static func text(_ segs: [Segment], t0: Double) -> String {
        var lines: [Segment] = []
        for s in segs {
            if var cur = lines.last, cur.track == s.track, s.start - cur.end < 1.5, cur.text.count < 600 {
                cur.text += " " + s.text
                cur.end = s.end
                lines[lines.count - 1] = cur
            } else {
                lines.append(s)
            }
        }
        return lines.map { "[\(fmt($0.start - t0))] \(labels[$0.track] ?? $0.track): \($0.text)" }.joined(separator: "\n")
    }

    /// When the Zoom call ended: the recorder's mark, or else the last Zoom
    /// speech if the room kept talking for 2+ minutes after it.
    static func afterStart(_ segs: [Segment], zoomEndedAt: Double?) -> Double? {
        if let z = zoomEndedAt { return z }
        guard let last = segs.filter({ $0.track == "system" }).map(\.end).max() else { return nil }
        let after = segs.filter { $0.track == "mic" && $0.start > last + 20 }
        if let a = after.first, let b = after.last, b.end - a.start >= 120 { return last + 20 }
        return nil
    }
}
