import AppKit
import SwiftUI

/// Shared state between the engine refresh loop and the main window.
final class AppState: ObservableObject {
    @Published var status: StatusResponse?
    @Published var error: String?
    @Published var notice: String?
    @Published var busy = false
    @Published var updated: Date?
    @Published var refreshing = false
    @Published var launchAtLogin = false
    /// Snapshot mode only: render this project's row as if hovered.
    var previewHoverPath: String?
    var perform: (MenuAction) -> Void = { _ in }
}

extension Agent {
    var color: Color {
        switch self {
        case .claude: return Color(red: 0.85, green: 0.47, blue: 0.34)
        case .codex: return Color(red: 0.2, green: 0.62, blue: 0.45)
        case .cursor: return Color(red: 0.3, green: 0.45, blue: 0.95)
        }
    }
}

struct MainView: View {
    @ObservedObject var state: AppState

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            content
            Divider()
            footer
        }
        .frame(minWidth: 640, minHeight: 480)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var header: some View {
        HStack(alignment: .center, spacing: 14) {
            Image(systemName: "arrow.triangle.2.circlepath")
                .font(.system(size: 28, weight: .semibold))
                .foregroundColor(.accentColor)
            VStack(alignment: .leading, spacing: 2) {
                Text("Context Relay").font(.title2.bold())
                Text("Switch between Claude Code, Codex and Cursor without losing context.")
                    .foregroundColor(.secondary)
            }
            Spacer()
        }
        .padding(16)
    }

    @ViewBuilder private var hooksBanner: some View {
        if let hooks = state.status?.hooks {
            let on = Agent.allCases.allSatisfy { hooks[$0.rawValue] == true }
            HStack(alignment: .top, spacing: 10) {
                Circle().fill(on ? Color.green : Color.orange).frame(width: 9, height: 9).padding(.top, 5)
                VStack(alignment: .leading, spacing: 3) {
                    Text(on ? "Auto-handoff is on" : "Auto-handoff is off").font(.headline)
                    Text(on
                         ? "When you start a different agent in a project, it automatically receives the previous agent's full session. Codex asks once to review the hook: approve it."
                         : "Turn it on so new agent sessions automatically receive the previous agent's work.")
                        .font(.callout).foregroundColor(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer()
                Button(on ? "Turn Off" : "Turn On") { state.perform(.setHooks(install: !on)) }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 10).fill((on ? Color.green : Color.orange).opacity(0.08)))
        }
    }

    @ViewBuilder private var content: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                hooksBanner
                if let error = state.error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundColor(.red)
                        .padding(.vertical, 6)
                }
                if let projects = state.status?.projects {
                    if projects.isEmpty {
                        Text("No agent sessions yet. Work in Claude Code, Codex or Cursor and your projects appear here.")
                            .foregroundColor(.secondary)
                            .padding(.top, 30)
                            .frame(maxWidth: .infinity)
                    } else {
                        Text("Recent projects").font(.headline).padding(.top, 4)
                        ForEach(projects, id: \.path) { ProjectCard(project: $0, state: state) }
                    }
                } else if state.error == nil {
                    HStack(spacing: 8) {
                        ProgressView().controlSize(.small)
                        Text("Reading agent sessions…").foregroundColor(.secondary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 40)
                }
            }
            .padding(16)
        }
    }

    private var footer: some View {
        HStack(spacing: 8) {
            if state.busy { ProgressView().controlSize(.small) }
            if let notice = state.notice {
                Text(notice).font(.callout).foregroundColor(.secondary).lineLimit(1)
            }
            Spacer()
            if let updated = state.updated {
                Text("Updated \(updated.formatted(date: .omitted, time: .shortened))")
                    .font(.caption).foregroundColor(.secondary)
            }
            Button("Refresh") { state.perform(.refresh) }.keyboardShortcut("r")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

private struct ProjectCard: View {
    let project: Project
    @ObservedObject var state: AppState

    var body: some View {
        let last = Agent(rawValue: project.lastAgent)
        VStack(alignment: .leading, spacing: 7) {
            HStack(spacing: 8) {
                Text(project.name).font(.headline).lineLimit(1)
                if let last = last { AgentBadge(agent: last) }
                Spacer()
                Text(Format.relativeTime(project.lastActive) ?? "").font(.caption).foregroundColor(.secondary)
            }
            Text(project.path).font(.caption).foregroundColor(.secondary)
                .lineLimit(1).truncationMode(.middle)
            if let title = project.sessions.first?.title, !title.isEmpty {
                Text("Last: \(Agent.displayName(project.lastAgent)) — \(Format.truncate(title, 110))")
                    .font(.callout).lineLimit(2)
            }
            if let git = gitLine { Text(git).font(.caption).foregroundColor(.secondary) }
            HStack(spacing: 6) {
                Text("Continue in").font(.callout).foregroundColor(.secondary)
                ForEach(Agent.allCases, id: \.self) { agent in
                    Button(agent.displayName) {
                        state.perform(.continueIn(agent: agent, project: project.path))
                    }
                    .disabled(state.busy)
                }
                Spacer()
                let context = project.path + "/.relay/CONTEXT.md"
                if FileManager.default.fileExists(atPath: context) {
                    Button("Open Handoff") { state.perform(.open(path: context)) }
                }
                Button {
                    state.perform(.reveal(path: project.path))
                } label: { Image(systemName: "folder") }
                .help("Show in Finder")
            }
            .controlSize(.small)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 10).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color.primary.opacity(0.08)))
    }

    private var gitLine: String? {
        var parts: [String] = []
        if let b = project.branch, !b.isEmpty { parts.append("Branch \(b)") }
        if let n = project.changedFiles {
            parts.append(n == 0 ? "clean" : "\(n) uncommitted file\(n == 1 ? "" : "s")")
        }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}

private struct AgentBadge: View {
    let agent: Agent
    var body: some View {
        Text(agent.displayName)
            .font(.caption.bold())
            .padding(.horizontal, 7).padding(.vertical, 2)
            .background(Capsule().fill(agent.color.opacity(0.16)))
            .foregroundColor(agent.color)
    }
}
