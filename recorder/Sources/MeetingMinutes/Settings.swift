import Foundation

/// Settings live in ~/Library/Application Support/MeetingMinutes/settings.json.
/// The first launch copies the defaults that ship inside the app (with the
/// team's names, schedule and Claude key). Missing keys fall back to those
/// defaults, so an older settings file keeps working after an app update.
struct Settings {
    var raw: [String: Any]
    var configPath: URL
    var sessionsDir: URL
    var outputDir: URL

    var weekday: Int          // Calendar weekday, Sunday = 1
    var startHour: Int
    var startMinute: Int
    var silenceMinutes: Double
    var minMinutes: Double
    var maxHours: Double
    var silenceThresholdDb: Float
    var chunkSeconds: Double
    var zoomAutoStart: String // "schedule", "always" or "off"
    var zoomWindowMinutes: Double
    var autoStart: Bool       // false: only the Start Recording button starts a recording
    var stopOnSilence: Bool   // false: only Stop Recording (or the hard cap) stops it

    static var supportDir: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("MeetingMinutes")
    }

    static func bundledDefaults() -> [String: Any] {
        guard let url = Bundle.main.url(forResource: "default_settings", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return [:] }
        return obj
    }

    static func merged(_ base: [String: Any], _ over: [String: Any]) -> [String: Any] {
        var out = base
        for (k, v) in over {
            if let b = base[k] as? [String: Any], let o = v as? [String: Any] {
                out[k] = merged(b, o)
            } else {
                out[k] = v
            }
        }
        return out
    }

    static func load() throws -> Settings {
        let fm = FileManager.default
        try fm.createDirectory(at: supportDir, withIntermediateDirectories: true)
        let path = supportDir.appendingPathComponent("settings.json")
        let defaults = bundledDefaults()
        if !fm.fileExists(atPath: path.path) {
            var seed = defaults
            // The key stays in the app, not in the editable file.
            if var c = seed["gemini"] as? [String: Any] { c["api_key"] = ""; seed["gemini"] = c }
            let data = try JSONSerialization.data(withJSONObject: seed, options: [.prettyPrinted, .sortedKeys])
            try data.write(to: path)
        }
        let data = try Data(contentsOf: path)
        guard let user = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw NSError(domain: "MeetingMinutes", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "settings.json is not valid JSON"])
        }
        var raw = merged(defaults, user)
        // An empty key in the user's file means "use the key built into the app".
        if var c = raw["gemini"] as? [String: Any], ((c["api_key"] as? String) ?? "").isEmpty {
            c["api_key"] = (defaults["gemini"] as? [String: Any])?["api_key"] ?? ""
            raw["gemini"] = c
        }
        return Settings(raw: raw, configPath: path)
    }

    init(raw: [String: Any], configPath: URL) {
        self.raw = raw
        self.configPath = configPath
        let meeting = raw["meeting"] as? [String: Any] ?? [:]
        let rec = raw["recording"] as? [String: Any] ?? [:]
        let out = raw["output"] as? [String: Any] ?? [:]
        func num(_ d: [String: Any], _ k: String, _ def: Double) -> Double { (d[k] as? NSNumber)?.doubleValue ?? def }
        let days = ["sunday", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday"]
        weekday = (days.firstIndex(of: ((meeting["weekday"] as? String) ?? "thursday").lowercased()) ?? 4) + 1
        let parts = ((meeting["start"] as? String) ?? "14:00").split(separator: ":").compactMap { Int($0) }
        startHour = parts.first ?? 14
        startMinute = parts.count > 1 ? parts[1] : 0
        sessionsDir = URL(fileURLWithPath: NSHomeDirectory() + "/MeetingMinutes/sessions")
        let folder = ProcessInfo.processInfo.environment["MINUTES_OUTPUT"] ?? ((out["folder"] as? String) ?? "~/Downloads")
        outputDir = URL(fileURLWithPath: folder.expandingTilde)
        silenceMinutes = num(rec, "silence_minutes", 5)
        minMinutes = num(rec, "min_minutes", 15)
        maxHours = num(rec, "max_hours", 4)
        silenceThresholdDb = Float(num(rec, "silence_threshold_db", -45))
        chunkSeconds = num(rec, "chunk_seconds", 120)
        zoomAutoStart = (rec["zoom_auto_start"] as? String) ?? "schedule"
        zoomWindowMinutes = num(rec, "zoom_window_minutes", 60)
        autoStart = (rec["auto_start"] as? Bool) ?? false
        stopOnSilence = (rec["stop_on_silence"] as? Bool) ?? false
    }

    /// Fallback when the settings file is broken: the built-in defaults.
    static func fallback() -> Settings {
        Settings(raw: bundledDefaults(), configPath: supportDir.appendingPathComponent("settings.json"))
    }

    // MARK: Typed access for the minutes pipeline

    func section(_ k: String) -> [String: Any] { raw[k] as? [String: Any] ?? [:] }
    func meeting(_ k: String, _ def: String = "") -> String { (section("meeting")[k] as? String) ?? def }
    func output(_ k: String) -> Any? { section("output")[k] }
    func gemini(_ k: String, _ def: String = "") -> String { (section("gemini")[k] as? String) ?? def }

    var attendees: [(label: String, names: [String])] {
        (raw["attendees"] as? [[String: Any]] ?? []).map {
            (($0["label"] as? String) ?? "", ($0["names"] as? [String]) ?? [])
        }
    }
    var nextMeetingAgenda: [String] { raw["next_meeting_agenda"] as? [String] ?? [] }

    /// True if `date` is within the Zoom window around the scheduled meeting.
    func nearScheduledMeeting(_ date: Date = Date()) -> Bool {
        guard let start = scheduledStart(on: date), Calendar.current.component(.weekday, from: date) == weekday else {
            return false
        }
        return abs(date.timeIntervalSince(start)) <= zoomWindowMinutes * 60
    }

    func scheduledStart(on date: Date) -> Date? {
        Calendar.current.date(bySettingHour: startHour, minute: startMinute, second: 0, of: date)
    }
}

extension String {
    var expandingTilde: String { (self as NSString).expandingTildeInPath }
}
