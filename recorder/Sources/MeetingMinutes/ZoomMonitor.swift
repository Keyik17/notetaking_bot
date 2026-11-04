import AppKit
import CoreGraphics
import Foundation

/// Tells when a Zoom meeting starts and ends, for the Zoom app and for
/// Zoom in a web browser. The Zoom app runs a helper process called CptHost
/// only while you are in a meeting, and the meeting window title is a
/// second check. In a browser, a tab on a Zoom web client meeting page
/// (zoom.us/wc/...) means you are in a meeting.
final class ZoomMonitor {
    var onChange: ((Bool) -> Void)?
    private(set) var inMeeting = false
    private var timer: Timer?
    private var misses = 0

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 3, repeats: true) { [weak self] _ in self?.poll() }
    }

    private func poll() {
        let now = ZoomMonitor.meetingActive()
        if now {
            misses = 0
            if !inMeeting {
                inMeeting = true
                log("Zoom meeting started")
                onChange?(true)
            }
        } else if inMeeting {
            misses += 1
            if misses >= 2 { // about 6 seconds, to ride out short glitches
                inMeeting = false
                log("Zoom meeting ended")
                onChange?(false)
            }
        }
    }

    static func meetingActive() -> Bool {
        if processRunning("CptHost") { return true }
        if browserMeetingOpen { return true }
        guard let list = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID)
                as? [[String: Any]] else { return false }
        for w in list {
            let owner = w[kCGWindowOwnerName as String] as? String ?? ""
            let name = w[kCGWindowName as String] as? String ?? ""
            if owner.lowercased().contains("zoom") &&
                (name.contains("Zoom Meeting") || name.contains("Zoom Webinar")) {
                return true
            }
        }
        return false
    }

    /// Browsers that can be asked for their tab addresses. Only browsers
    /// that are already running are asked, so none is ever launched.
    static let browsers: [(bundleID: String, name: String)] = [
        ("com.google.Chrome", "Google Chrome"),
        ("com.brave.Browser", "Brave Browser"),
        ("com.microsoft.edgemac", "Microsoft Edge"),
        ("company.thebrowser.Browser", "Arc"),
        ("com.apple.Safari", "Safari"),
    ]

    /// Browser tab checks run on their own queue, because a browser can be
    /// slow to answer (or macOS can be waiting on the permission prompt).
    /// The monitor uses the latest answer and never waits for one.
    private static let browserQueue = DispatchQueue(label: "zoom.browser")
    private static let lock = NSLock()
    private static var browserAnswer = false
    private static var checking = false

    static var browserMeetingOpen: Bool {
        lock.lock(); defer { lock.unlock() }
        if !checking {
            checking = true
            browserQueue.async {
                let r = checkBrowsers()
                lock.lock(); browserAnswer = r; checking = false; lock.unlock()
            }
        }
        return browserAnswer
    }

    private static func checkBrowsers() -> Bool {
        for b in browsers where !NSRunningApplication.runningApplications(withBundleIdentifier: b.bundleID).isEmpty {
            let src = "tell application \"\(b.name)\" to get URL of every tab of every window"
            guard let script = NSAppleScript(source: src) else { continue }
            var err: NSDictionary?
            let out = script.executeAndReturnError(&err)
            if let err = err {
                logOnce("cannot read \(b.name) tabs (allow it in System Settings, Privacy & Security, Automation): \(err[NSAppleScript.errorMessage] ?? err)")
                continue
            }
            if urls(in: out).contains(where: isZoomMeeting) { return true }
        }
        return false
    }

    /// Zoom web client meeting pages look like app.zoom.us/wc/123456789/join
    /// or zoom.us/wc/123456789/start. The page you see after leaving is not a meeting.
    static func isZoomMeeting(_ url: String) -> Bool {
        let u = url.lowercased()
        guard u.contains("zoom.us/wc/") else { return false }
        return !(u.contains("/leave") || u.contains("postattendee") || u.contains("/feedback"))
    }

    static func urls(in d: NSAppleEventDescriptor) -> [String] {
        if d.numberOfItems == 0 { return d.stringValue.map { [$0] } ?? [] }
        var out: [String] = []
        for i in 1...d.numberOfItems {
            if let item = d.atIndex(i) { out += urls(in: item) }
        }
        return out
    }

    private static var logged = Set<String>()
    static func logOnce(_ msg: String) {
        if logged.insert(msg).inserted { log(msg) }
    }

    static func processRunning(_ name: String) -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/pgrep")
        p.arguments = ["-x", name]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do {
            try p.run()
            p.waitUntilExit()
            return p.terminationStatus == 0
        } catch {
            return false
        }
    }
}
