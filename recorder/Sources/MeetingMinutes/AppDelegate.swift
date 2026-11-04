import AppKit
import AVFoundation
import CoreGraphics
import Foundation
import ServiceManagement

let startNotification = Notification.Name("com.meetingminutes.start")
let stopNotification = Notification.Name("com.meetingminutes.stop")

enum AppState { case idle, recording, processing }

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var statusItem: NSStatusItem!
    private let statusLine = NSMenuItem(title: "Idle", action: nil, keyEquivalent: "")
    private var startItem: NSMenuItem!
    private var stopItem: NSMenuItem!
    private var settings: Settings!
    private var state = AppState.idle
    private let zoom = ZoomMonitor()
    private var session: Session?
    private var micWriter: ChunkWriter?
    private var sysWriter: ChunkWriter?
    private var mic: MicCapture?
    private var system: SystemAudioCapture?
    private var caffeinate: Process?
    private var tick: Timer?
    private var pipelines = 0
    var startOnLaunch: String?

    func applicationDidFinishLaunching(_ note: Notification) {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        buildMenu()
        reloadSettings()
        updateIcon()

        let dnc = DistributedNotificationCenter.default()
        dnc.addObserver(forName: startNotification, object: nil, queue: .main) { [weak self] n in
            self?.startRecording(trigger: (n.object as? String) ?? "schedule")
        }
        dnc.addObserver(forName: stopNotification, object: nil, queue: .main) { [weak self] _ in
            self?.stopRecording(reason: "manual")
        }

        zoom.onChange = { [weak self] active in self?.zoomChanged(active) }
        zoom.start()
        requestPermissions()
        registerLoginItem()

        if let trigger = startOnLaunch {
            startRecording(trigger: trigger)
        }
        // Finish any session a crash left behind.
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
            guard let self = self else { return }
            for d in Pipeline.pendingSessions(self.settings) { self.runPipeline(d) }
        }
    }

    // MARK: Menu

    private func buildMenu() {
        let menu = NSMenu()
        statusLine.isEnabled = false
        menu.addItem(statusLine)
        menu.addItem(.separator())
        startItem = NSMenuItem(title: "Start Recording", action: #selector(startClicked), keyEquivalent: "r")
        stopItem = NSMenuItem(title: "Stop Recording", action: #selector(stopClicked), keyEquivalent: "s")
        for i in [startItem!, stopItem!] { i.target = self; menu.addItem(i) }
        menu.addItem(.separator())
        let items: [(String, Selector)] = [
            ("Open Minutes Folder", #selector(openOutput)),
            ("Open Recordings Folder", #selector(openSessions)),
            ("Edit Settings", #selector(openConfig)),
            ("Redo Minutes for Last Recording", #selector(redoLast)),
        ]
        for (t, s) in items {
            let i = NSMenuItem(title: t, action: s, keyEquivalent: "")
            i.target = self
            menu.addItem(i)
        }
        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Quit", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)
        statusItem.menu = menu
    }

    private func updateIcon() {
        guard let button = statusItem.button else { return }
        let name: String
        var config = NSImage.SymbolConfiguration(pointSize: 14, weight: .regular)
        switch state {
        case .recording:
            name = "record.circle.fill"
            config = config.applying(.init(paletteColors: [.white, .systemRed]))
        case .processing:
            name = "gearshape"
        case .idle:
            name = pipelines > 0 ? "gearshape" : "waveform"
        }
        let img = NSImage(systemSymbolName: name, accessibilityDescription: "Meeting Minutes")?
            .withSymbolConfiguration(config)
        img?.isTemplate = state != .recording
        button.image = img
        startItem.isEnabled = state != .recording
        stopItem.isEnabled = state == .recording
        updateStatusLine()
    }

    private func updateStatusLine() {
        switch state {
        case .recording:
            let secs = Int(Date().timeIntervalSince(session?.startedAt ?? Date()))
            let z = zoom.inMeeting ? "Zoom on" : (session?.get("zoom_ended_at") != nil ? "after the call" : "no Zoom")
            statusLine.title = String(format: "Recording %d:%02d:%02d (%@)", secs / 3600, secs / 60 % 60, secs % 60, z)
        case .processing, .idle:
            statusLine.title = pipelines > 0 ? "Writing minutes..." : "Idle"
        }
    }

    @objc private func startClicked() { startRecording(trigger: "manual") }
    @objc private func stopClicked() { stopRecording(reason: "manual") }
    @objc private func openOutput() { NSWorkspace.shared.open(settings.outputDir) }
    @objc private func openSessions() {
        try? FileManager.default.createDirectory(at: settings.sessionsDir, withIntermediateDirectories: true)
        NSWorkspace.shared.open(settings.sessionsDir)
    }
    @objc private func openConfig() {
        NSWorkspace.shared.open([settings.configPath], withApplicationAt: URL(fileURLWithPath: "/System/Applications/TextEdit.app"),
                                configuration: NSWorkspace.OpenConfiguration())
    }
    @objc private func redoLast() {
        guard let dirs = try? FileManager.default.contentsOfDirectory(at: settings.sessionsDir, includingPropertiesForKeys: nil),
              let last = dirs.filter({ $0.hasDirectoryPath }).sorted(by: { $0.path < $1.path }).last else { return }
        runPipeline(last, force: true)
    }
    @objc private func quit() {
        if state == .recording { stopRecording(reason: "quit") }
        DispatchQueue.main.asyncAfter(deadline: .now() + 1) { NSApp.terminate(nil) }
    }

    private func reloadSettings() {
        do {
            settings = try Settings.load()
        } catch {
            log("settings: \(error.localizedDescription)")
            notify("Could not read settings: \(error.localizedDescription)")
            if settings == nil { settings = Settings.fallback() }
        }
    }

    private func requestPermissions() {
        AVCaptureDevice.requestAccess(for: .audio) { ok in
            if !ok { log("microphone permission denied") }
        }
        if !CGPreflightScreenCaptureAccess() {
            log("asking for Screen Recording permission (needed for Zoom audio)")
            CGRequestScreenCaptureAccess()
        }
    }

    // MARK: Recording

    func startRecording(trigger: String) {
        guard state != .recording else {
            log("already recording, ignoring \(trigger) trigger")
            return
        }
        reloadSettings()
        if !settings.autoStart && (trigger == "schedule" || trigger == "zoom") {
            log("auto_start is off, ignoring \(trigger) trigger")
            return
        }
        let s: Session
        do {
            s = try Session(root: settings.sessionsDir, trigger: trigger)
        } catch {
            notify("Could not start recording: \(error.localizedDescription)")
            return
        }
        session = s
        log("recording started (\(trigger)) in \(s.dir.path)")
        if zoom.inMeeting { s.set("zoom_started_at", Date().timeIntervalSince1970) }

        let mw = ChunkWriter(track: "mic", sessionDir: s.dir, chunkSeconds: settings.chunkSeconds,
                             thresholdDb: settings.silenceThresholdDb)
        let sw = ChunkWriter(track: "system", sessionDir: s.dir, chunkSeconds: settings.chunkSeconds,
                             thresholdDb: settings.silenceThresholdDb)
        micWriter = mw
        sysWriter = sw
        let m = MicCapture(writer: mw)
        do {
            try m.start()
            mic = m
        } catch {
            log("microphone failed: \(error.localizedDescription)")
            notify("Microphone not available. Recording Zoom audio only.")
        }
        let sys = SystemAudioCapture(writer: sw)
        system = sys
        Task {
            do {
                try await sys.start()
            } catch {
                log("system audio failed: \(error.localizedDescription)")
                await MainActor.run {
                    notify("Zoom audio not recorded. Allow Screen Recording for MeetingMinutes in System Settings.")
                }
            }
        }

        // Keep the Mac awake while recording.
        let c = Process()
        c.executableURL = URL(fileURLWithPath: "/usr/bin/caffeinate")
        c.arguments = ["-i", "-m", "-s", "-w", "\(getpid())"]
        try? c.run()
        caffeinate = c

        state = .recording
        updateIcon()
        tick = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in self?.checkStop() }
        notify("Recording started. Remember to tell everyone the meeting is recorded.")
    }

    private func checkStop() {
        guard state == .recording, let s = session else { return }
        updateStatusLine()
        let now = Date()
        let elapsed = now.timeIntervalSince(s.startedAt)
        if elapsed >= settings.maxHours * 3600 {
            stopRecording(reason: "max_duration")
            return
        }
        // Silence only counts once Zoom is closed and the minimum time passed.
        guard settings.stopOnSilence, !zoom.inMeeting, elapsed >= settings.minMinutes * 60 else { return }
        let lastLoud = max(micWriter?.lastLoud ?? s.startedAt, sysWriter?.lastLoud ?? s.startedAt)
        if now.timeIntervalSince(lastLoud) >= settings.silenceMinutes * 60 {
            stopRecording(reason: "silence")
        }
    }

    private func zoomChanged(_ active: Bool) {
        if active {
            if state == .recording {
                if session?.get("zoom_started_at") == nil {
                    session?.set("zoom_started_at", Date().timeIntervalSince1970)
                }
                session?.set("zoom_ended_at", nil) // rejoined, so the call is not over yet
            } else {
                reloadSettings()
                let mode = settings.zoomAutoStart.lowercased()
                if settings.autoStart, mode == "always" || (mode == "schedule" && settings.nearScheduledMeeting()) {
                    startRecording(trigger: "zoom")
                    session?.set("zoom_started_at", Date().timeIntervalSince1970)
                }
            }
        } else if state == .recording {
            // Everything after this moment goes under "After the call".
            session?.set("zoom_ended_at", Date().timeIntervalSince1970)
        }
        updateStatusLine()
    }

    func stopRecording(reason: String) {
        guard state == .recording, let s = session else { return }
        log("stopping (\(reason))")
        tick?.invalidate()
        mic?.stop()
        let sys = system
        Task {
            await sys?.stop()
            await MainActor.run { self.finishStop(s, reason: reason) }
        }
    }

    private func finishStop(_ s: Session, reason: String) {
        micWriter?.close()
        sysWriter?.close()
        caffeinate?.terminate()
        caffeinate = nil
        s.finish(reason: reason)
        mic = nil
        system = nil
        session = nil
        state = .idle
        updateIcon()
        notify("Recording stopped (\(reason.replacingOccurrences(of: "_", with: " "))). Writing the minutes now.")
        runPipeline(s.dir)
    }

    // MARK: Pipeline

    /// Minutes are written one recording at a time, in order.
    private var queue: Task<Void, Never>?

    private func runPipeline(_ dir: URL, force: Bool = false) {
        reloadSettings()
        let st = settings!
        let previous = queue
        pipelines += 1
        updateIcon()
        queue = Task { @MainActor in
            await previous?.value
            do {
                try await Pipeline.run(dir, settings: st, force: force)
            } catch {
                notify("Minutes could not be written: \(error.localizedDescription). It will try again when the app restarts.")
            }
            self.pipelines -= 1
            self.updateIcon()
        }
    }

    /// Start with the Mac, so nobody has to set anything up.
    private func registerLoginItem() {
        let svc = SMAppService.mainApp
        guard svc.status != .enabled else { return }
        do {
            try svc.register()
            log("will start at login")
        } catch {
            log("could not add login item: \(error.localizedDescription)")
        }
    }
}
