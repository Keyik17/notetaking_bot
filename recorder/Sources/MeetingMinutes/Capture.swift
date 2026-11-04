import AVFoundation
import CoreMedia
import Foundation
import ScreenCaptureKit

/// Everything the Mac plays (so everyone on Zoom), through ScreenCaptureKit.
/// Audio only; the video frames it must deliver are tiny and ignored.
final class SystemAudioCapture: NSObject, SCStreamOutput, SCStreamDelegate {
    private var stream: SCStream?
    private let writer: ChunkWriter
    private let audioQueue = DispatchQueue(label: "capture.system.audio")
    private let videoQueue = DispatchQueue(label: "capture.system.video")
    private var wantRunning = false

    init(writer: ChunkWriter) {
        self.writer = writer
    }

    func start() async throws {
        wantRunning = true
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let display = content.displays.first else {
            throw NSError(domain: "MeetingMinutes", code: 2, userInfo: [NSLocalizedDescriptionKey: "No display found"])
        }
        let filter = SCContentFilter(display: display, excludingApplications: [], exceptingWindows: [])
        let cfg = SCStreamConfiguration()
        cfg.capturesAudio = true
        cfg.sampleRate = 48000
        cfg.channelCount = 1
        cfg.excludesCurrentProcessAudio = true
        cfg.width = 2
        cfg.height = 2
        cfg.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        cfg.queueDepth = 3
        let s = SCStream(filter: filter, configuration: cfg, delegate: self)
        try s.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
        try s.addStreamOutput(self, type: .screen, sampleHandlerQueue: videoQueue)
        try await s.startCapture()
        stream = s
        log("system audio capture started")
    }

    func stop() async {
        wantRunning = false
        if let s = stream {
            try? await s.stopCapture()
        }
        stream = nil
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sb: CMSampleBuffer, of type: SCStreamOutputType) {
        guard type == .audio, sb.isValid,
              let fd = CMSampleBufferGetFormatDescription(sb),
              let asbdPtr = CMAudioFormatDescriptionGetStreamBasicDescription(fd) else { return }
        var asbd = asbdPtr.pointee
        guard let fmt = AVAudioFormat(streamDescription: &asbd) else { return }
        let frames = AVAudioFrameCount(CMSampleBufferGetNumSamples(sb))
        guard frames > 0, let pcm = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: frames) else { return }
        pcm.frameLength = frames
        let status = CMSampleBufferCopyPCMDataIntoAudioBufferList(
            sb, at: 0, frameCount: Int32(frames), into: pcm.mutableAudioBufferList)
        guard status == noErr, let mono = makeMono(pcm) else { return }
        writer.write(mono, hostSeconds: CMSampleBufferGetPresentationTimeStamp(sb).seconds)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        log("system audio capture stopped: \(error.localizedDescription)")
        self.stream = nil
        guard wantRunning else { return }
        // For example a display change. Try again.
        DispatchQueue.global().asyncAfter(deadline: .now() + 3) {
            Task { try? await self.start() }
        }
    }
}

/// The Mac's microphone (everyone in the room).
final class MicCapture {
    private var engine = AVAudioEngine()
    private let writer: ChunkWriter
    private var observer: NSObjectProtocol?
    private var running = false

    init(writer: ChunkWriter) {
        self.writer = writer
    }

    func start() throws {
        running = true
        engine = AVAudioEngine()
        let input = engine.inputNode
        let fmt = input.outputFormat(forBus: 0)
        guard fmt.sampleRate > 0, fmt.channelCount > 0 else {
            throw NSError(domain: "MeetingMinutes", code: 3, userInfo: [NSLocalizedDescriptionKey: "No microphone"])
        }
        input.installTap(onBus: 0, bufferSize: 4096, format: fmt) { [weak self] buf, time in
            guard let self = self, let mono = makeMono(buf) else { return }
            let host = time.isHostTimeValid ? AVAudioTime.seconds(forHostTime: time.hostTime) : 0
            self.writer.write(mono, hostSeconds: host)
        }
        engine.prepare()
        try engine.start()
        // Restart when the input device changes (headset plugged in, etc).
        observer = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main) { [weak self] _ in
            guard let self = self, self.running else { return }
            log("microphone changed, restarting")
            self.engine.inputNode.removeTap(onBus: 0)
            self.engine.stop()
            try? self.start()
        }
        log("microphone capture started (\(Int(fmt.sampleRate)) Hz, \(fmt.channelCount) ch)")
    }

    func stop() {
        running = false
        if let o = observer { NotificationCenter.default.removeObserver(o) }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
    }
}
