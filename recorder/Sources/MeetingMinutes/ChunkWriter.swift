import AVFoundation
import Foundation

/// Writes one audio track to disk as a series of short files, so a crash
/// loses at most one chunk. Each new chunk is logged in chunks.jsonl with
/// its wall clock start time. Gaps in the incoming audio are filled with
/// silence so the timeline stays true.
final class ChunkWriter {
    let track: String
    private let sessionDir: URL
    private let chunkSeconds: Double
    private let queue: DispatchQueue
    private var file: AVAudioFile?
    private var index = 0
    private var framesInChunk: AVAudioFramePosition = 0
    private var totalFrames: AVAudioFramePosition = 0
    private var sampleRate: Double = 0
    private var startEpoch: Double = 0
    private var firstHostSeconds: Double = -1
    private let thresholdDb: Float

    /// Last time this track was louder than the silence threshold.
    private(set) var lastLoud = Date()
    private(set) var levelDb: Float = -120

    init(track: String, sessionDir: URL, chunkSeconds: Double, thresholdDb: Float) {
        self.track = track
        self.sessionDir = sessionDir
        self.chunkSeconds = chunkSeconds
        self.thresholdDb = thresholdDb
        self.queue = DispatchQueue(label: "writer.\(track)")
    }

    /// `buffer` must be mono Float32. `hostSeconds` is the capture time of
    /// its first sample on the host clock.
    func write(_ buffer: AVAudioPCMBuffer, hostSeconds: Double) {
        queue.async { self.writeOnQueue(buffer, hostSeconds: hostSeconds) }
    }

    private func writeOnQueue(_ buffer: AVAudioPCMBuffer, hostSeconds: Double) {
        let n = AVAudioFramePosition(buffer.frameLength)
        if n == 0 { return }
        if sampleRate == 0 {
            sampleRate = buffer.format.sampleRate
            startEpoch = Date().timeIntervalSince1970
            firstHostSeconds = hostSeconds
        }
        // Level for silence detection.
        if let ch = buffer.floatChannelData?[0] {
            var sum: Float = 0
            for i in 0..<Int(buffer.frameLength) { sum += ch[i] * ch[i] }
            let rms = sqrt(sum / Float(buffer.frameLength))
            levelDb = 20 * log10(max(rms, 1e-6))
            if levelDb > thresholdDb { lastLoud = Date() }
        }
        // Fill gaps (for example Zoom audio stops while nobody talks).
        if hostSeconds > 0 && firstHostSeconds > 0 {
            let expected = AVAudioFramePosition((hostSeconds - firstHostSeconds) * sampleRate)
            let gap = expected - totalFrames
            if gap > AVAudioFramePosition(0.25 * sampleRate) && gap < AVAudioFramePosition(3600 * sampleRate) {
                writeSilence(frames: gap, format: buffer.format)
            }
        }
        append(buffer)
    }

    private func writeSilence(frames: AVAudioFramePosition, format: AVAudioFormat) {
        var left = frames
        let block: AVAudioFrameCount = 48000
        while left > 0 {
            let count = AVAudioFrameCount(min(AVAudioFramePosition(block), left))
            guard let b = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: count) else { return }
            b.frameLength = count
            if let ch = b.floatChannelData?[0] { ch.update(repeating: 0, count: Int(count)) }
            append(b)
            left -= AVAudioFramePosition(count)
        }
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        if file == nil || Double(framesInChunk) >= chunkSeconds * sampleRate {
            rotate(format: buffer.format)
        }
        do {
            try file?.write(from: buffer)
            framesInChunk += AVAudioFramePosition(buffer.frameLength)
            totalFrames += AVAudioFramePosition(buffer.frameLength)
        } catch {
            log("write failed on \(track): \(error)")
        }
    }

    private func rotate(format: AVAudioFormat) {
        file = nil // closes and finishes the previous chunk
        index += 1
        framesInChunk = 0
        let name = String(format: "audio/%@_%04d.caf", track, index)
        let url = sessionDir.appendingPathComponent(name)
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: format.sampleRate,
            AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
        ]
        do {
            file = try AVAudioFile(forWriting: url, settings: settings,
                                   commonFormat: .pcmFormatFloat32, interleaved: false)
        } catch {
            log("could not create \(name): \(error)")
            file = nil
            return
        }
        let start = startEpoch + Double(totalFrames) / sampleRate
        let line = "{\"track\": \"\(track)\", \"file\": \"\(name)\", \"start\": \(start), \"sample_rate\": \(format.sampleRate)}\n"
        appendLine(line, to: sessionDir.appendingPathComponent("chunks.jsonl"))
    }

    /// Finish the current chunk. Waits for queued audio to be written.
    func close() {
        queue.sync { self.file = nil }
    }
}

func appendLine(_ line: String, to url: URL) {
    let data = Data(line.utf8)
    if let h = try? FileHandle(forWritingTo: url) {
        h.seekToEndOfFile()
        h.write(data)
        try? h.synchronize()
        try? h.close()
    } else {
        try? data.write(to: url)
    }
}

/// Mix any Float32 buffer down to a new mono Float32 buffer.
func makeMono(_ input: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
    let frames = Int(input.frameLength)
    let channels = Int(input.format.channelCount)
    guard frames > 0, channels > 0,
          input.format.commonFormat == .pcmFormatFloat32,
          let src = input.floatChannelData,
          let fmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: input.format.sampleRate,
                                  channels: 1, interleaved: false),
          let out = AVAudioPCMBuffer(pcmFormat: fmt, frameCapacity: AVAudioFrameCount(frames)),
          let dst = out.floatChannelData?[0] else { return nil }
    out.frameLength = AVAudioFrameCount(frames)
    let scale = 1 / Float(channels)
    if input.format.isInterleaved {
        let p = src[0]
        for i in 0..<frames {
            var s: Float = 0
            for c in 0..<channels { s += p[i * channels + c] }
            dst[i] = s * scale
        }
    } else {
        for i in 0..<frames {
            var s: Float = 0
            for c in 0..<channels { s += src[c][i] }
            dst[i] = s * scale
        }
    }
    return out
}

/// Prints and appends to ~/MeetingMinutes/logs/app.log.
func log(_ msg: String) {
    let f = DateFormatter()
    f.dateFormat = "yyyy-MM-dd HH:mm:ss"
    let line = "[\(f.string(from: Date()))] \(msg)\n"
    print(line, terminator: "")
    fflush(stdout)
    let dir = URL(fileURLWithPath: NSHomeDirectory() + "/MeetingMinutes/logs")
    let url = dir.appendingPathComponent("app.log")
    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    if !FileManager.default.fileExists(atPath: url.path) { FileManager.default.createFile(atPath: url.path, contents: nil) }
    if let h = try? FileHandle(forWritingTo: url) {
        h.seekToEndOfFile()
        h.write(line.data(using: .utf8)!)
        try? h.close()
    }
}
