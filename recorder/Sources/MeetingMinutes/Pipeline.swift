import Foundation

/// Turns a finished recording into minutes: transcribe, write with Claude,
/// save the PDF (and transcript), then delete the raw audio.
enum Pipeline {
    // MARK: Session file helpers

    static func readSession(_ dir: URL) -> [String: Any] {
        guard let d = try? Data(contentsOf: dir.appendingPathComponent("session.json")),
              let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return [:] }
        return o
    }

    static func updateSession(_ dir: URL, _ changes: [String: Any?]) {
        var s = readSession(dir)
        for (k, v) in changes { s[k] = v ?? NSNull() }
        if let d = try? JSONSerialization.data(withJSONObject: s, options: [.prettyPrinted, .sortedKeys]) {
            try? d.write(to: dir.appendingPathComponent("session.json"), options: .atomic)
        }
    }

    // MARK: Last week's minutes

    static var historyDir: URL { Settings.supportDir.appendingPathComponent("history") }

    static func saveHistory(date: Date, text: String) {
        try? FileManager.default.createDirectory(at: historyDir, withIntermediateDirectories: true)
        let o: [String: Any] = ["date": Dates.iso(date), "text": text]
        if let d = try? JSONSerialization.data(withJSONObject: o, options: .prettyPrinted) {
            try? d.write(to: historyDir.appendingPathComponent("\(Dates.iso(date)).json"))
        }
    }

    /// The most recent minutes dated before this meeting (the app ships with
    /// the last hand-checked minutes as a starting point).
    static func previous(before date: Date) -> (date: String, text: String)? {
        var all: [(String, String)] = []
        var files = (try? FileManager.default.contentsOfDirectory(at: historyDir, includingPropertiesForKeys: nil)) ?? []
        if let seed = Bundle.main.url(forResource: "last_minutes_seed", withExtension: "json") { files.append(seed) }
        for f in files where f.pathExtension == "json" {
            if let d = try? Data(contentsOf: f), let o = try? JSONSerialization.jsonObject(with: d) as? [String: String],
               let day = o["date"], let text = o["text"] { all.append((day, text)) }
        }
        let today = Dates.iso(date)
        return all.filter { $0.0 < today }.max { $0.0 < $1.0 }.map { (date: $0.0, text: $0.1) }
    }

    // MARK: Run

    /// Returns the saved PDF, or nil when the recording had no speech.
    @discardableResult
    static func run(_ dir: URL, settings st: Settings, force: Bool = false) async throws -> URL? {
        let logURL = dir.appendingPathComponent("pipeline.log")
        if !FileManager.default.fileExists(atPath: logURL.path) { FileManager.default.createFile(atPath: logURL.path, contents: nil) }
        let handle = try? FileHandle(forWritingTo: logURL)
        handle?.seekToEndOfFile()
        let say: (String) -> Void = { msg in
            let f = DateFormatter()
            f.dateFormat = "HH:mm:ss"
            handle?.write("[\(f.string(from: Date()))] \(msg)\n".data(using: .utf8)!)
            log("[\(dir.lastPathComponent)] \(msg)")
        }
        defer { try? handle?.close() }

        let s = readSession(dir)
        updateSession(dir, ["status": "processing", "attempts": ((s["attempts"] as? Int) ?? 0) + 1])
        do {
            let pdf = try await runSteps(dir, st: st, session: s, force: force, say: say)
            return pdf
        } catch {
            say("failed: \(error.localizedDescription)")
            updateSession(dir, ["status": "failed", "error": error.localizedDescription])
            throw error
        }
    }

    private static func runSteps(_ dir: URL, st: Settings, session s: [String: Any], force: Bool,
                                 say: @escaping (String) -> Void) async throws -> URL? {
        let t0 = (s["started_at"] as? Double) ?? Date().timeIntervalSince1970
        let started = Date(timeIntervalSince1970: t0)
        let date = started
        say("meeting date \(Dates.iso(date))")

        // 1. Transcribe (saved, so a redo does not need the audio)
        let work = dir.appendingPathComponent("work")
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        let cache = work.appendingPathComponent("transcript.json")
        var segs: [Segment]
        let haveAudio = !Transcriber.chunks(in: dir).isEmpty
        if FileManager.default.fileExists(atPath: cache.path) && (!force || !haveAudio),
           let d = try? Data(contentsOf: cache),
           let cached = try? JSONDecoder().decode([String: [Segment]].self, from: d)["segments"] {
            segs = cached
        } else {
            guard haveAudio else { throw NSError(domain: "MeetingMinutes", code: 8, userInfo: [NSLocalizedDescriptionKey: "no audio found"]) }
            segs = try await Transcriber.transcribeSession(dir, log: say)
            try JSONEncoder().encode(["segments": segs]).write(to: cache)
        }
        let after0 = Transcriber.afterStart(segs, zoomEndedAt: s["zoom_ended_at"] as? Double)
        let main = Transcriber.text(segs.filter { after0 == nil || $0.start < after0! }, t0: t0)
        let after = after0 == nil ? "" : Transcriber.text(segs.filter { $0.start >= after0! }, t0: t0)
        let words = (main + " " + after).split(separator: " ").count
        say("transcript: \(segs.count) pieces, \(words) words")
        if words < 40 {
            updateSession(dir, ["status": "empty"])
            notify("The recording had no speech, so no minutes were written.")
            return nil
        }

        // 2. Write
        let minutesCache = work.appendingPathComponent("minutes_v2.json")
        var items: [String: [Item]]
        if !force, let d = try? Data(contentsOf: minutesCache), let cached = try? JSONDecoder().decode([String: [Item]].self, from: d) {
            items = cached
            say("using the minutes already written")
        } else {
            let prev = previous(before: date)
            say("last week's minutes: \(prev?.date ?? "none")")
            let raw = try await Writer.write(st, prompt: Writer.prompt(st, date: date, lastWeek: prev, main: main, after: after), log: say)
            items = Writer.items(raw)
            if after.isEmpty { items["after_call"] = [] }
            try JSONEncoder().encode(items).write(to: minutesCache)
        }

        // 3. PDF and transcript
        let stem = ((st.output("filename") as? String) ?? "Meeting Minutes_{MM}_{DD}")
            .replacingOccurrences(of: "{MM}", with: String(format: "%02d", Calendar.current.component(.month, from: date)))
            .replacingOccurrences(of: "{DD}", with: String(format: "%02d", Calendar.current.component(.day, from: date)))
            .replacingOccurrences(of: "{YYYY}", with: "\(Calendar.current.component(.year, from: date))")
        let localPDF = dir.appendingPathComponent("\(stem).pdf")
        let final = items
        try await MainActor.run {
            try MinutesPDF.render(MinutesPDF.document(st, date: date, started: started, items: final), to: localPDF)
        }

        var folder = st.outputDir
        if (try? folder.checkResourceIsReachable()) != true {
            do { try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true) } catch {
                folder = URL(fileURLWithPath: NSHomeDirectory() + "/MeetingMinutes/output")
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                say("output folder missing, saving to \(folder.path)")
            }
        }
        let mine = s["output_pdf"] as? String
        var base = stem, n = 2
        while FileManager.default.fileExists(atPath: folder.appendingPathComponent("\(base).pdf").path),
              folder.appendingPathComponent("\(base).pdf").path != mine {
            base = "\(stem) (\(n))"
            n += 1
        }
        let target = folder.appendingPathComponent("\(base).pdf")
        try? FileManager.default.removeItem(at: target)
        try FileManager.default.copyItem(at: localPDF, to: target)
        say("saved \(target.path)")
        var txtTarget: URL? = nil
        if (st.output("save_transcript") as? Bool) ?? true {
            let header = "\(st.meeting("title")): transcript\n\(Dates.short(date)), \(Calendar.current.component(.year, from: date))\n"
                + "Made by speech recognition, so expect some misheard words.\n"
                + "'On Zoom' = Zoom call audio. 'In the room' = the room microphone.\n\n"
            let body = main + (after.isEmpty ? "" : "\n\n=== After the call ===\n\n" + after)
            let t = folder.appendingPathComponent("\(base) transcript.txt")
            try (header + body + "\n").write(to: t, atomically: true, encoding: .utf8)
            txtTarget = t
        }
        saveHistory(date: date, text: MinutesPDF.plainText(st, date: date, started: started, items: items))

        // 4. Clean up the raw audio
        if (st.output("delete_audio_after_success") as? Bool) ?? true {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent("audio"))
            say("deleted raw audio")
        }
        updateSession(dir, ["status": "done", "error": nil, "output_pdf": target.path, "output_txt": txtTarget?.path,
                            "meeting_date": Dates.iso(date)])
        notify("Minutes saved: \(target.lastPathComponent)")
        return target
    }

    /// Sessions that stopped but never finished (a crash, no internet).
    static func pendingSessions(_ st: Settings) -> [URL] {
        let dirs = (try? FileManager.default.contentsOfDirectory(at: st.sessionsDir, includingPropertiesForKeys: nil)) ?? []
        return dirs.filter { d in
            let s = readSession(d)
            let status = s["status"] as? String ?? ""
            let tries = s["attempts"] as? Int ?? 0
            let recording = FileManager.default.fileExists(atPath: d.appendingPathComponent("recording.lock").path)
            return !recording && (status == "stopped" || status == "processing" || (status == "failed" && tries < 3))
        }.sorted { $0.path < $1.path }
    }
}

func notify(_ text: String) {
    let safe = text.replacingOccurrences(of: "\"", with: "'")
    let p = Process()
    p.executableURL = URL(fileURLWithPath: "/usr/bin/osascript")
    p.arguments = ["-e", "display notification \"\(safe)\" with title \"Meeting Minutes\""]
    try? p.run()
}
