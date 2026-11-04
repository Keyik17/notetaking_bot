import Foundation

/// One recording: a folder with session.json, chunks.jsonl and audio/.
/// The pipeline reads the same files.
final class Session {
    let dir: URL
    private var info: [String: Any]

    init(root: URL, trigger: String) throws {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd_HHmmss"
        dir = root.appendingPathComponent(f.string(from: Date()))
        try FileManager.default.createDirectory(at: dir.appendingPathComponent("audio"),
                                                withIntermediateDirectories: true)
        info = ["started_at": Date().timeIntervalSince1970, "trigger": trigger, "status": "recording"]
        try "\(getpid())".write(to: dir.appendingPathComponent("recording.lock"), atomically: true, encoding: .utf8)
        save()
    }

    func set(_ key: String, _ value: Any?) {
        info[key] = value ?? NSNull()
        save()
    }

    func get(_ key: String) -> Any? {
        let v = info[key]
        return v is NSNull ? nil : v
    }

    var startedAt: Date {
        Date(timeIntervalSince1970: (info["started_at"] as? Double) ?? Date().timeIntervalSince1970)
    }

    func finish(reason: String) {
        info["ended_at"] = Date().timeIntervalSince1970
        info["stop_reason"] = reason
        info["status"] = "stopped"
        save()
        try? FileManager.default.removeItem(at: dir.appendingPathComponent("recording.lock"))
    }

    private func save() {
        guard let data = try? JSONSerialization.data(withJSONObject: info, options: [.prettyPrinted, .sortedKeys]) else {
            return
        }
        let url = dir.appendingPathComponent("session.json")
        let tmp = dir.appendingPathComponent("session.json.tmp")
        do {
            try data.write(to: tmp)
            _ = try FileManager.default.replaceItemAt(url, withItemAt: tmp)
        } catch {
            try? data.write(to: url)
        }
    }
}
