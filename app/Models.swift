import Foundation

// MARK: - Agents

enum Agent: String, CaseIterable {
    case claude, codex, cursor

    var displayName: String {
        switch self {
        case .claude: return "Claude Code"
        case .codex: return "Codex"
        case .cursor: return "Cursor"
        }
    }

    /// Display name for a raw engine string; unknown agents are capitalized rather than rejected.
    static func displayName(_ raw: String) -> String {
        Agent(rawValue: raw)?.displayName ?? raw.capitalized
    }
}

// MARK: - Engine JSON contract

struct Session: Decodable, Equatable {
    let agent: String
    let id: String?
    let title: String?
    let updated: String?
}

struct Project: Decodable, Equatable {
    let path: String
    let name: String
    let lastAgent: String
    let lastActive: String?
    let branch: String?
    let changedFiles: Int?
    let sessions: [Session]

    enum CodingKeys: String, CodingKey {
        case path, name, branch, sessions
        case lastAgent = "last_agent"
        case lastActive = "last_active"
        case changedFiles = "changed_files"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        path = try c.decode(String.self, forKey: .path)
        name = try c.decodeIfPresent(String.self, forKey: .name)
            ?? URL(fileURLWithPath: path).lastPathComponent
        lastAgent = try c.decode(String.self, forKey: .lastAgent)
        lastActive = try c.decodeIfPresent(String.self, forKey: .lastActive)
        branch = try c.decodeIfPresent(String.self, forKey: .branch)
        changedFiles = try c.decodeIfPresent(Int.self, forKey: .changedFiles)
        sessions = try c.decodeIfPresent([Session].self, forKey: .sessions) ?? []
    }
}

struct StatusResponse: Decodable, Equatable {
    let version: Int?
    let hooks: [String: Bool]
    let projects: [Project]

    static func decode(_ data: Data) throws -> StatusResponse {
        try JSONDecoder().decode(StatusResponse.self, from: data)
    }
}

struct ActionResponse: Decodable {
    let ok: Bool
    let error: String?
    let hooks: [String: Bool]?
    let fromAgent: String?

    enum CodingKeys: String, CodingKey {
        case ok, error, hooks
        case fromAgent = "from_agent"
    }
}

// MARK: - Formatting helpers (pure)

enum Format {
    private static let withFraction: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let plain = ISO8601DateFormatter()

    static func parseDate(_ s: String?) -> Date? {
        guard let s = s else { return nil }
        return plain.date(from: s) ?? withFraction.date(from: s)
    }

    /// "just now", "12m ago", "3h ago", "2d ago", or "Oct 3" for older dates.
    static func relativeTime(_ s: String?, now: Date = Date()) -> String? {
        guard let date = parseDate(s) else { return nil }
        let secs = Int(now.timeIntervalSince(date))
        switch secs {
        case ..<60: return "just now"
        case ..<3600: return "\(secs / 60)m ago"
        case ..<86400: return "\(secs / 3600)h ago"
        case ..<(86400 * 14): return "\(secs / 86400)d ago"
        default:
            let f = DateFormatter()
            f.locale = Locale(identifier: "en_US_POSIX")
            f.dateFormat = "MMM d"
            return f.string(from: date)
        }
    }

    /// Collapses whitespace/newlines and truncates with an ellipsis.
    static func truncate(_ s: String, _ limit: Int = 60) -> String {
        let flat = s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        guard flat.count > limit else { return flat }
        return String(flat.prefix(limit - 1)) + "…"
    }
}
