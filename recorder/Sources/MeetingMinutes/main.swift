import AppKit
import Foundation

// Usage:
//   MeetingMinutes                      run the menu bar app
//   MeetingMinutes --start [--trigger schedule]   start recording (used by launchd)
//   MeetingMinutes --stop               stop recording
// If the app is already running, --start and --stop are passed to it.

let args = CommandLine.arguments
let wantsStart = args.contains("--start")
let wantsStop = args.contains("--stop")
var trigger = "manual"
if let i = args.firstIndex(of: "--trigger"), i + 1 < args.count { trigger = args[i + 1] }

// Testing: MeetingMinutes --process <session folder> [--force]
// writes the minutes for one recording and exits.
if let i = args.firstIndex(of: "--process"), i + 1 < args.count {
    let dir = URL(fileURLWithPath: args[i + 1])
    let force = args.contains("--force")
    let app = NSApplication.shared
    Task { @MainActor in
        do {
            let st = try Settings.load()
            let out = try await Pipeline.run(dir, settings: st, force: force)
            print("done: \(out?.path ?? "no minutes")")
            exit(0)
        } catch {
            print("failed: \(error.localizedDescription)")
            exit(1)
        }
    }
    app.run()
}

let me = ProcessInfo.processInfo.processIdentifier
let bundleID = Bundle.main.bundleIdentifier ?? "com.meetingminutes.recorder"
let others = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
    .filter { $0.processIdentifier != me }

if !others.isEmpty {
    let dnc = DistributedNotificationCenter.default()
    if wantsStart {
        dnc.postNotificationName(startNotification, object: trigger, userInfo: nil, deliverImmediately: true)
        log("asked the running app to start (\(trigger))")
    } else if wantsStop {
        dnc.postNotificationName(stopNotification, object: nil, userInfo: nil, deliverImmediately: true)
        log("asked the running app to stop")
    } else {
        log("already running")
    }
    exit(0)
}
if wantsStop { exit(0) }

let app = NSApplication.shared
app.setActivationPolicy(.accessory) // menu bar only, no Dock icon
let delegate = AppDelegate()
if wantsStart { delegate.startOnLaunch = trigger }
app.delegate = delegate
log("Meeting Minutes started")
app.run()
