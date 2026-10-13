import Foundation

// A platform-independent description of the menu, so structure can be tested without AppKit.

enum MenuAction: Equatable {
    case continueIn(agent: Agent, project: String)
    case open(path: String)
    case reveal(path: String)
    case setHooks(install: Bool)
    case refresh
    case showWindow
    case toggleLaunchAtLogin
    case quit
}

struct ItemSpec {
    var title: String
    var subtitle: String? = nil
    /// nil means the item is shown disabled (informational).
    var action: MenuAction? = nil
    var checked = false
    var keyEquivalent = ""
}

indirect enum MenuEntry {
    case separator
    case item(ItemSpec)
    case submenu(ItemSpec, [MenuEntry])
}

struct MenuInput {
    var status: StatusResponse?
    var error: String?
    var launchAtLogin = false
    var now = Date()
    var contextExists: (String) -> Bool = { FileManager.default.fileExists(atPath: $0) }
}

enum MenuBuilder {
    static let maxProjects = 10

    static func build(_ input: MenuInput) -> [MenuEntry] {
        var out: [MenuEntry] = [info("Context Relay"),
                                .item(ItemSpec(title: "Open Context Relay…", action: .showWindow, keyEquivalent: "o")),
                                .separator]

        if let error = input.error {
            out.append(info("Engine error: " + Format.truncate(error, 120)))
        }
        if let status = input.status {
            out += hooksEntries(status.hooks)
            out.append(.separator)
            out.append(info("Recent Projects"))
            if status.projects.isEmpty {
                out.append(info("No agent sessions found yet"))
            }
            for project in status.projects.prefix(maxProjects) {
                out.append(projectEntry(project, input))
            }
        }

        out.append(.separator)
        out.append(.item(ItemSpec(title: "Refresh", action: .refresh, keyEquivalent: "r")))
        out.append(.item(ItemSpec(title: "Launch at Login", action: .toggleLaunchAtLogin, checked: input.launchAtLogin)))
        out.append(.item(ItemSpec(title: "Quit Context Relay", action: .quit, keyEquivalent: "q")))
        return out
    }

    private static func info(_ title: String) -> MenuEntry { .item(ItemSpec(title: title)) }

    static func hooksEntries(_ hooks: [String: Bool]) -> [MenuEntry] {
        let off = Agent.allCases.filter { hooks[$0.rawValue] != true }
        if off.isEmpty {
            let names = Agent.allCases.map { $0.displayName }.joined(separator: ", ")
            return [info("✓ Auto-handoff on for \(names)"),
                    info("   Codex asks once to review the hook: approve it"),
                    .item(ItemSpec(title: "Turn Off Auto-handoff", action: .setHooks(install: false)))]
        }
        let names = off.map { $0.displayName }.joined(separator: ", ")
        return [info("Auto-handoff off for: \(names)"),
                .item(ItemSpec(title: "Turn On Auto-handoff", action: .setHooks(install: true)))]
    }

    static func projectEntry(_ p: Project, _ input: MenuInput) -> MenuEntry {
        let lastName = Agent.displayName(p.lastAgent)
        let when = Format.relativeTime(p.lastActive, now: input.now)
        let subtitle = [lastName, when].compactMap { $0 }.joined(separator: " · ")

        var children: [MenuEntry] = []
        var last = "Last: \(lastName)"
        if let title = p.sessions.first?.title, !title.isEmpty {
            last += " — " + Format.truncate(title)
        }
        children.append(info(last))

        var gitParts: [String] = []
        if let b = p.branch, !b.isEmpty { gitParts.append("Branch: \(b)") }
        if let n = p.changedFiles {
            gitParts.append(n == 0 ? "clean" : "\(n) uncommitted file\(n == 1 ? "" : "s")")
        }
        if !gitParts.isEmpty { children.append(info(gitParts.joined(separator: " · "))) }

        children.append(.separator)
        for agent in Agent.allCases {
            let suffix = agent.rawValue == p.lastAgent ? " (same agent)" : ""
            children.append(.item(ItemSpec(title: "Continue in \(agent.displayName)\(suffix)",
                                           action: .continueIn(agent: agent, project: p.path))))
        }

        children.append(.separator)
        let context = (p.path as NSString).appendingPathComponent(".relay/CONTEXT.md")
        children.append(.item(ItemSpec(title: "Open Handoff Context",
                                       action: input.contextExists(context) ? .open(path: context) : nil)))
        children.append(.item(ItemSpec(title: "Reveal in Finder", action: .reveal(path: p.path))))

        let sessions: [MenuEntry] = p.sessions.isEmpty ? [info("No sessions")] : p.sessions.map { s in
            var line = Agent.displayName(s.agent)
            if let t = Format.relativeTime(s.updated, now: input.now) { line += " · " + t }
            if let title = s.title, !title.isEmpty { line += " — " + Format.truncate(title) }
            return info(line)
        }
        children.append(.submenu(ItemSpec(title: "Recent sessions"), sessions))

        return .submenu(ItemSpec(title: p.name, subtitle: subtitle), children)
    }

    // MARK: Plain-text rendering (used by --self-test)

    static func render(_ entries: [MenuEntry], indent: Int = 0) -> String {
        let pad = String(repeating: "  ", count: indent)
        var lines: [String] = []
        for entry in entries {
            switch entry {
            case .separator:
                lines.append(pad + "--------")
            case .item(let s):
                lines.append(pad + describe(s))
            case .submenu(let s, let children):
                lines.append(pad + describe(s, enabled: true) + " >")
                lines.append(render(children, indent: indent + 1))
            }
        }
        return lines.joined(separator: "\n")
    }

    private static func describe(_ s: ItemSpec, enabled: Bool? = nil) -> String {
        var text = s.title
        if let sub = s.subtitle { text += "    (\(sub))" }
        if s.checked { text = "[x] " + text }
        if !(enabled ?? (s.action != nil)) { text += "  [disabled]" }
        if !s.keyEquivalent.isEmpty { text += "  [Cmd+\(s.keyEquivalent.uppercased())]" }
        return text
    }
}
