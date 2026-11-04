import AppKit
import Foundation

enum Dates {
    static let months = ["January", "February", "March", "April", "May", "June", "July", "August",
                         "September", "October", "November", "December"]

    static func ordinal(_ n: Int) -> String {
        if (11...13).contains(n % 100) { return "\(n)th" }
        return "\(n)" + (["st", "nd", "rd"].indices.contains(n % 10 - 1) ? ["st", "nd", "rd"][n % 10 - 1] : "th")
    }

    /// October 29th
    static func short(_ d: Date) -> String {
        let c = Calendar.current.dateComponents([.month, .day], from: d)
        return "\(months[c.month! - 1]) \(ordinal(c.day!))"
    }

    /// OCTOBER 29th, 2026
    static func long(_ d: Date) -> String {
        let c = Calendar.current.dateComponents([.year, .month, .day], from: d)
        return "\(months[c.month! - 1].uppercased()) \(ordinal(c.day!)), \(c.year!)"
    }

    static func iso(_ d: Date) -> String {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd"
        return f.string(from: d)
    }

    /// The next meeting weekday strictly after `d`.
    static func nextMeeting(after d: Date, weekday: Int) -> Date {
        let cal = Calendar.current
        var x = cal.startOfDay(for: d)
        repeat { x = cal.date(byAdding: .day, value: 1, to: x)! } while cal.component(.weekday, from: x) != weekday
        return x
    }

    /// The usual label ("2 PM") for the regular meeting, else the real start
    /// time rounded to 15 minutes.
    static func timeLabel(_ st: Settings, started: Date) -> String {
        let cal = Calendar.current
        if cal.component(.weekday, from: started) == st.weekday, let sched = st.scheduledStart(on: started),
           abs(started.timeIntervalSince(sched)) <= 45 * 60 {
            return st.meeting("time_label", "2 PM")
        }
        let t = started.addingTimeInterval(7 * 60)
        let h = cal.component(.hour, from: t), m = cal.component(.minute, from: t) / 15 * 15
        let hour = h % 12 == 0 ? 12 : h % 12
        return m == 0 ? "\(hour) \(h < 12 ? "AM" : "PM")" : String(format: "%d:%02d %@", hour, m, h < 12 ? "AM" : "PM")
    }
}

/// Builds the minutes page by page in the layout of the team's template.
enum MinutesPDF {
    static let red = "#f75d5d", body = "#666666"

    static func joinNames(_ n: [String]) -> String {
        switch n.count {
        case 0: return ""
        case 1: return n[0]
        case 2: return "\(n[0]) and \(n[1])"
        default: return n.dropLast().joined(separator: ", ") + ", and " + n.last!
        }
    }

    static func color(_ hex: String) -> NSColor {
        let v = Int(hex.dropFirst(), radix: 16) ?? 0
        return NSColor(srgbRed: CGFloat(v >> 16 & 255) / 255, green: CGFloat(v >> 8 & 255) / 255,
                       blue: CGFloat(v & 255) / 255, alpha: 1)
    }

    static func font(_ size: CGFloat, bold: Bool = false) -> NSFont {
        let name = bold ? "HelveticaNeue-Bold" : "HelveticaNeue"
        return NSFont(name: name, size: size) ?? (bold ? .boldSystemFont(ofSize: size) : .systemFont(ofSize: size))
    }

    /// The whole document, styled like the team's template.
    static func document(_ st: Settings, date: Date, started: Date, items: [String: [Item]]) -> NSAttributedString {
        let doc = NSMutableAttributedString()
        func para(_ text: String, size: CGFloat, bold: Bool = false, hex: String = body,
                  before: CGFloat = 0, after: CGFloat = 4) {
            let ps = NSMutableParagraphStyle()
            ps.paragraphSpacingBefore = before
            ps.paragraphSpacing = after
            ps.lineHeightMultiple = 1.15
            doc.append(NSAttributedString(string: text + "\n", attributes: [
                .font: font(size, bold: bold), .foregroundColor: color(hex), .paragraphStyle: ps]))
        }
        func list(_ list: [Item]) {
            for (n, it) in list.enumerated() {
                let ps = NSMutableParagraphStyle()
                ps.firstLineHeadIndent = 14
                ps.headIndent = 34
                ps.tabStops = [NSTextTab(textAlignment: .left, location: 34)]
                ps.paragraphSpacing = 3
                ps.lineHeightMultiple = 1.15
                let base: [NSAttributedString.Key: Any] = [.font: font(11), .foregroundColor: color(body), .paragraphStyle: ps]
                let line = NSMutableAttributedString(string: "\(n + 1).\t", attributes: base)
                if !it.bold.isEmpty {
                    var b = base
                    b[.font] = font(11, bold: true)
                    line.append(NSAttributedString(string: it.bold + " ", attributes: b))
                }
                line.append(NSAttributedString(string: it.text + "\n", attributes: base))
                doc.append(line)
                for (k, sub) in it.sub.enumerated() {
                    let sp = NSMutableParagraphStyle()
                    sp.firstLineHeadIndent = 40
                    sp.headIndent = 58
                    sp.tabStops = [NSTextTab(textAlignment: .left, location: 58)]
                    sp.paragraphSpacing = 2
                    sp.lineHeightMultiple = 1.15
                    let letter = String(UnicodeScalar(UInt8(97 + k % 26)))
                    doc.append(NSAttributedString(string: "\(letter).\t\(sub)\n", attributes: [
                        .font: font(11), .foregroundColor: color(body), .paragraphStyle: sp]))
                }
            }
        }
        para(st.meeting("title", "Meeting Minutes"), size: 30, bold: true, hex: red, after: 6)
        para("\(Dates.long(date)) / \(Dates.timeLabel(st, started: started)) / \(st.meeting("location"))",
             size: 11.5, hex: "#000000", after: 10)
        para("Attendees", size: 16, bold: true, hex: red, before: 8, after: 4)
        for g in st.attendees { para("\(g.label): \(joinNames(g.names))", size: 11) }
        para("Agenda", size: 16, bold: true, hex: red, before: 10, after: 2)
        let next = Dates.nextMeeting(after: date, weekday: st.weekday)
        for s in SECTIONS {
            var l = items[s.key] ?? []
            if s.key == "next_steps" {
                l.append(Item(text: st.meeting("next_meeting_line").replacingOccurrences(of: "{date}", with: Dates.short(next))))
            }
            if s.key == "after_call" {
                para("Next Meeting Agenda", size: 16, bold: true, hex: red, before: 12, after: 4)
                list(st.nextMeetingAgenda.map { Item(text: $0) })
                if l.isEmpty { l = [Item(text: "No discussion was recorded after the call.")] }
            }
            if l.isEmpty { l = [Item(text: "None this week.")] }
            if s.h1 {
                para(s.heading, size: 16, bold: true, hex: red, before: 12, after: 4)
            } else {
                para(s.heading, size: 12, bold: true, hex: "#000000", before: 10, after: 4)
            }
            list(l)
        }
        return doc
    }

    /// Plain text version (headings and bullets) for next week's prompt.
    static func plainText(_ st: Settings, date: Date, started: Date, items: [String: [Item]]) -> String {
        var t = "## \(st.meeting("title"))\n\(Dates.long(date)) / \(Dates.timeLabel(st, started: started)) / \(st.meeting("location"))\n"
        for s in SECTIONS {
            t += "\n## \(s.heading)\n"
            for it in items[s.key] ?? [] {
                t += "- " + (it.bold.isEmpty ? "" : it.bold + " ") + it.text + "\n"
                for x in it.sub { t += "  - \(x)\n" }
            }
        }
        return t
    }

    /// Renders the document to a paginated US Letter PDF. Must run on the main thread.
    @MainActor
    static func render(_ text: NSAttributedString, to url: URL) throws {
        let info = NSPrintInfo()
        info.paperSize = NSSize(width: 612, height: 792)
        info.topMargin = 60; info.bottomMargin = 60; info.leftMargin = 66; info.rightMargin = 66
        info.horizontalPagination = .fit
        info.verticalPagination = .automatic
        info.isHorizontallyCentered = false
        info.isVerticallyCentered = false
        info.jobDisposition = .save
        info.dictionary()[NSPrintInfo.AttributeKey.jobSavingURL] = url
        let width = info.paperSize.width - info.leftMargin - info.rightMargin
        let view = NSTextView(frame: NSRect(x: 0, y: 0, width: width, height: 100))
        view.textStorage?.setAttributedString(text)
        view.isVerticallyResizable = true
        view.textContainer?.widthTracksTextView = true
        view.layoutManager?.ensureLayout(for: view.textContainer!)
        view.sizeToFit()
        try? FileManager.default.removeItem(at: url)
        let op = NSPrintOperation(view: view, printInfo: info)
        op.showsPrintPanel = false
        op.showsProgressPanel = false
        guard op.run(), FileManager.default.fileExists(atPath: url.path) else {
            throw NSError(domain: "MeetingMinutes", code: 7, userInfo: [NSLocalizedDescriptionKey: "could not save the PDF"])
        }
    }
}
