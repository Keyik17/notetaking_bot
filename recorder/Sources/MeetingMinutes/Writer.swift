import Foundation

/// One section of the minutes: JSON key, heading, heading style and shape.
struct SectionDef {
    enum Kind { case bullets, goals, questions, actions }
    let key: String
    let heading: String
    let h1: Bool
    let kind: Kind
}

let SECTIONS: [SectionDef] = [
    .init(key: "accomplishments", heading: "Accomplishments from our last meeting", h1: false, kind: .bullets),
    .init(key: "sprint_goals", heading: "Sprint goal status", h1: false, kind: .goals),
    .init(key: "roadmap", heading: "Project Roadmap", h1: false, kind: .bullets),
    .init(key: "risks", heading: "Risk", h1: false, kind: .bullets),
    .init(key: "issues", heading: "Issues", h1: false, kind: .bullets),
    .init(key: "questions", heading: "Questions", h1: false, kind: .questions),
    .init(key: "goals_next_week", heading: "Goals for next week", h1: false, kind: .bullets),
    .init(key: "next_steps", heading: "Next Steps", h1: false, kind: .bullets),
    .init(key: "notes", heading: "Notes", h1: true, kind: .bullets),
    .init(key: "action_items", heading: "Action Items", h1: true, kind: .actions),
    .init(key: "after_call", heading: "After the call", h1: true, kind: .bullets),
]

/// A written item, whatever the section's shape, ready for the PDF.
struct Item: Codable {
    var text: String
    var bold: String = ""
    var sub: [String] = []
}

enum Writer {
    static let system = """
    You write official meeting minutes for a university senior design team and its project sponsors. \
    You are accurate, clean and to the point. You only write what the transcript supports, in your own \
    words, never as quotes. You reply only with JSON that matches the given schema.
    """

    static let guide = """
    SECTIONS (JSON key: what goes in it)

    accomplishments: Work the team finished since the last meeting, as reported in the meeting. Check each of last
      week's "Goals for next week" and include it only if the transcript shows it was done. Short past tense
      phrases, for example "Converted the Technical Support Directory map to ArcGIS". No work in progress, no plans,
      nothing that was only discussed. Leave "sub" empty.
    sprint_goals: Each current sprint goal that was talked about, with "status" (Done, In progress, Not started or
      Blocked) and a one sentence "detail" on where it stands.
    roadmap: The project timeline: where the team is, upcoming sprints and releases, and any roadmap changes that
      were requested or agreed, with details in "sub".
    risks: Each risk (something that could go wrong later) as a short name in "text", with its status, mitigation
      plan and any update in "sub".
    issues: Current problems or blockers (already happening). If none were reported, one bullet saying so, plus any
      feedback about what should count as an issue.
    questions: Each real question about the project, the course or meeting logistics that someone asked, and how it
      was answered. "question" is a short topic title (2 to 8 words, not a quote, a name in parentheses if it is
      clear who asked). "answers" holds 1 to 4 short sentences: the answer, decision or follow up. Merge follow up
      questions on the same topic. Skip small talk, audio checks, greetings and anything unrelated to the project.
    goals_next_week: What the team plans to do before the next meeting. Short phrases starting with a verb.
    next_steps: Upcoming steps and dates for the team and the sponsors (for example "Eric will share the API
      documentation on Friday, October 30th"). One step per bullet, at most 8. Do NOT add the date of the next
      sponsor meeting; it is added automatically.
    notes: 3 to 9 short facts or pieces of feedback someone who missed the meeting must know, including feedback
      from faculty after the sponsors left the call. Full sentences.
    action_items: Who will do what. "owner" is a person's name when the transcript makes it clear, otherwise
      "Team" or "Sponsors". "task" includes the deadline if one was said.
    after_call: What the team decided or planned in the room after the Zoom call ended: decisions, who will do
      what, concerns. 3 to 8 bullets in your own words. Ignore chat about classes, grades, other students, personal
      plans and anything not about the project. Empty list if nothing about the project was said.

    RULES
    - Use only facts from the transcript. Never invent names, dates or decisions.
    - The transcript comes from speech recognition, so names and words are often misheard. Fix them using the
      PEOPLE list, the KNOWN MISHEARINGS and common sense, but do not guess beyond that.
    - Leave out private or personal details: email addresses, phone numbers, grades, gossip and personal plans.
    - Be concise: each bullet under about 30 words, no filler such as "The team discussed".
    - Write dates like "Thursday, November 5th". Never use em dashes or middle dots.
    - Every key must be present. Use an empty list when nothing fits. Use [] for "sub" when there are no details.
    """

    // MARK: Schema

    static func obj(_ props: [String: Any]) -> [String: Any] {
        ["type": "object", "properties": props, "required": Array(props.keys).sorted(), "additionalProperties": false]
    }

    static func schema() -> [String: Any] {
        let str: [String: Any] = ["type": "string"]
        let strs: [String: Any] = ["type": "array", "items": str]
        var props: [String: Any] = [:]
        for s in SECTIONS {
            let item: [String: Any]
            switch s.kind {
            case .bullets: item = obj(["text": str, "sub": strs])
            case .goals: item = obj(["goal": str, "status": str, "detail": str])
            case .questions: item = obj(["question": str, "answers": strs])
            case .actions: item = obj(["owner": str, "task": str])
            }
            props[s.key] = ["type": "array", "items": item]
        }
        return obj(["sections": obj(props)])
    }

    // MARK: Prompt

    static func prompt(_ st: Settings, date: Date, lastWeek: (date: String, text: String)?, main: String, after: String) -> String {
        let people = st.attendees.map { "\($0.label): \($0.names.joined(separator: ", "))" }.joined(separator: "\n")
        let f = DateFormatter()
        f.dateFormat = "EEEE"
        return """
        Write the minutes for the \(st.meeting("title", "meeting")) held on \(f.string(from: date)), \
        \(Dates.short(date)), \(Calendar.current.component(.year, from: date)).

        PEOPLE (correct spellings):
        \(people)

        KNOWN MISHEARINGS: \(st.meeting("misheard_names", "(none)"))

        HOW TO READ THE TRANSCRIPT: lines marked 'On Zoom' are people on the Zoom call (usually sponsors and \
        faculty), lines marked 'In the room' are people in the room (usually the student team). The labels do not \
        say which person spoke. The start of the recording can include chat before the meeting begins.

        \(guide)
        LAST WEEK'S MINUTES (\(lastWeek?.date ?? "none")), for the style and to check last week's goals:
        <last_week>
        \(lastWeek?.text ?? "(none available)")
        </last_week>

        TRANSCRIPT DURING THE ZOOM CALL:
        <transcript>
        \(main)
        </transcript>

        TRANSCRIPT IN THE ROOM AFTER THE ZOOM CALL ENDED:
        <after_call>
        \(after.isEmpty ? "(nothing recorded)" : after)
        </after_call>

        Now write every section as JSON.
        """
    }

    // MARK: Calling Gemini

    /// Returns the raw "sections" object, written by Google's Gemini on the
    /// free tier with the key built into the app.
    static func write(_ st: Settings, prompt: String, log: @escaping (String) -> Void) async throws -> [String: Any] {
        let key = st.gemini("api_key")
        guard !key.isEmpty else {
            throw NSError(domain: "MeetingMinutes", code: 2,
                          userInfo: [NSLocalizedDescriptionKey: "no Gemini API key in the app or in settings.json"])
        }
        let models = [st.gemini("model", "gemini-flash-latest"), st.gemini("backup_model", "gemini-flash-lite-latest")]
        var errors: [String] = []
        for model in models where !model.isEmpty {
            do { return try await viaGemini(model: model, key: key, prompt: prompt, log: log) } catch {
                errors.append("\(model): \(error.localizedDescription)")
                log(errors.last!)
            }
        }
        throw NSError(domain: "MeetingMinutes", code: 2, userInfo: [NSLocalizedDescriptionKey: errors.joined(separator: "; ")])
    }

    static func viaGemini(model: String, key: String, prompt: String, log: (String) -> Void) async throws -> [String: Any] {
        log("writing the minutes with Gemini (\(model))")
        var req = URLRequest(url: URL(string: "https://generativelanguage.googleapis.com/v1beta/models/\(model):generateContent")!)
        req.httpMethod = "POST"
        req.timeoutInterval = 300
        req.setValue("application/json", forHTTPHeaderField: "content-type")
        req.setValue(key, forHTTPHeaderField: "x-goog-api-key")
        let body: [String: Any] = [
            "systemInstruction": ["parts": [["text": system]]],
            "contents": [["role": "user", "parts": [["text": prompt]]]],
            "generationConfig": [
                "responseMimeType": "application/json",
                "responseJsonSchema": schema(),
                "maxOutputTokens": 16000,
            ],
        ]
        req.httpBody = try JSONSerialization.data(withJSONObject: body)
        let started = Date()
        var lastError = "no answer"
        for attempt in 1...4 {
            let (data, resp) = try await URLSession.shared.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
            let message = ((json["error"] as? [String: Any])?["message"] as? String) ?? String(data: data, encoding: .utf8) ?? ""
            if code == 429 || code >= 500 {
                // Free tier: a short per-minute limit, so wait and try again.
                lastError = "HTTP \(code): \(message.prefix(200))"
                log("Gemini busy (\(code)), retrying in \(attempt * 30)s")
                try await Task.sleep(nanoseconds: UInt64(attempt) * 30_000_000_000)
                continue
            }
            guard code == 200 else {
                throw NSError(domain: "MeetingMinutes", code: code,
                              userInfo: [NSLocalizedDescriptionKey: "HTTP \(code): \(message.prefix(300))"])
            }
            let cand = (json["candidates"] as? [[String: Any]])?.first ?? [:]
            let parts = ((cand["content"] as? [String: Any])?["parts"] as? [[String: Any]]) ?? []
            let text = parts.filter { $0["thought"] as? Bool != true }.compactMap { $0["text"] as? String }.joined()
            guard let d = text.data(using: .utf8),
                  let out = (try? JSONSerialization.jsonObject(with: d)) as? [String: Any],
                  let sections = out["sections"] as? [String: Any] else {
                lastError = "unreadable answer (finish reason \(cand["finishReason"] ?? "?"))"
                log(lastError)
                continue
            }
            let u = json["usageMetadata"] as? [String: Any] ?? [:]
            log("  \(u["promptTokenCount"] ?? 0) tokens in, \(u["candidatesTokenCount"] ?? 0) out, \(Int(Date().timeIntervalSince(started)))s")
            return sections
        }
        throw NSError(domain: "MeetingMinutes", code: 4, userInfo: [NSLocalizedDescriptionKey: lastError])
    }

    // MARK: Turning the answer into items

    static func clean(_ s: String?) -> String {
        guard var t = s else { return "" }
        t = t.replacingOccurrences(of: "\u{200B}", with: "")
        t = t.replacingOccurrences(of: #"\s*\u{2014}\s*"#, with: ", ", options: .regularExpression)
        t = t.replacingOccurrences(of: "\u{2013}", with: "-")
        t = t.replacingOccurrences(of: #"\s*\u{00B7}\s*"#, with: ", ", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\*\*|__"#, with: "", options: .regularExpression)
        t = t.replacingOccurrences(of: #"^\s*[-*•]\s+"#, with: "", options: .regularExpression)
        t = t.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        return t.trimmingCharacters(in: .whitespaces)
    }

    static func items(_ raw: [String: Any]) -> [String: [Item]] {
        var out: [String: [Item]] = [:]
        for s in SECTIONS {
            var list: [Item] = []
            for it in raw[s.key] as? [[String: Any]] ?? [] {
                func strs(_ k: String) -> [String] { (it[k] as? [String] ?? []).map { clean($0) }.filter { !$0.isEmpty } }
                switch s.kind {
                case .bullets:
                    let t = clean(it["text"] as? String)
                    if !t.isEmpty { list.append(Item(text: t, sub: strs("sub"))) }
                case .goals:
                    let g = clean(it["goal"] as? String)
                    var st = clean(it["status"] as? String)
                    if st.isEmpty { st = "In progress" }
                    if st.hasSuffix(".") { st.removeLast() }
                    if !g.isEmpty { list.append(Item(text: clean(it["detail"] as? String), bold: "\(g): \(st).")) }
                case .questions:
                    let q = clean(it["question"] as? String)
                    if !q.isEmpty { list.append(Item(text: q, sub: strs("answers"))) }
                case .actions:
                    let t = clean(it["task"] as? String)
                    var o = clean(it["owner"] as? String)
                    if o.isEmpty { o = "Team" }
                    if !t.isEmpty { list.append(Item(text: "\(o): \(t)")) }
                }
            }
            out[s.key] = dedupe(list)
        }
        return out
    }

    /// Drops near-duplicate bullets inside a section.
    static func dedupe(_ list: [Item]) -> [Item] {
        var kept: [Item] = []
        for it in list {
            let w = Set(Transcriber.words(it.bold + " " + it.text))
            let dup = kept.contains { k in
                let kw = Set(Transcriber.words(k.bold + " " + k.text))
                let union = w.union(kw).count
                return union > 0 && Double(w.intersection(kw).count) / Double(union) >= 0.85
            }
            if !dup { kept.append(it) }
        }
        return kept
    }
}
