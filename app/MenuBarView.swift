import AppKit
import SwiftUI

/// The panel that drops down from the menu bar icon.
struct MenuBarView: View {
    @ObservedObject var state: AppState

    private var hooksOn: Bool? {
        guard let hooks = state.status?.hooks else { return nil }
        return Agent.allCases.allSatisfy { hooks[$0.rawValue] == true }
    }

    var body: some View {
        VStack(spacing: 0) {
            header
            if hooksOn == false { offBanner }
            Divider().opacity(0.6)
            content
            if let notice = state.notice { NoticeStrip(text: notice, busy: state.busy) }
            Divider().opacity(0.6)
            footer
        }
        .frame(width: 384)
        .animation(.easeOut(duration: 0.18), value: state.notice)
    }

    // MARK: Header

    private var header: some View {
        HStack(spacing: 11) {
            LogoTile(size: 36)
            VStack(alignment: .leading, spacing: 3) {
                Text("Context Relay").font(.system(size: 14, weight: .semibold))
                StatusPill(on: hooksOn)
            }
            Spacer(minLength: 8)
            if state.refreshing {
                ProgressView().controlSize(.small).frame(width: 26, height: 26)
            } else {
                IconButton(symbol: "arrow.clockwise", help: "Refresh") { state.perform(.refresh) }
            }
            IconButton(symbol: "macwindow", help: "Open Context Relay window") { state.perform(.showWindow) }
            settingsMenu
        }
        .padding(.horizontal, 14)
        .padding(.top, 14)
        .padding(.bottom, 12)
    }

    private var settingsMenu: some View {
        Menu {
            if let on = hooksOn {
                Button(on ? "Turn Off Auto-handoff" : "Turn On Auto-handoff") { state.perform(.setHooks(install: !on)) }
            }
            Button(state.launchAtLogin ? "✓ Launch at Login" : "Launch at Login") { state.perform(.toggleLaunchAtLogin) }
            Divider()
            Button("Open Context Relay Window") { state.perform(.showWindow) }
            Button("Quit Context Relay") { state.perform(.quit) }
        } label: {
            Image(systemName: "ellipsis.circle").font(.system(size: 15, weight: .regular))
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .frame(width: 26, height: 26)
        .help("Settings")
    }

    private var offBanner: some View {
        HStack(spacing: 10) {
            Image(systemName: "bolt.slash.fill").foregroundColor(.orange)
            VStack(alignment: .leading, spacing: 1) {
                Text("Auto-handoff is off").font(.system(size: 12, weight: .semibold))
                Text("New agent sessions won't receive context.").font(.system(size: 11)).foregroundColor(.secondary)
            }
            Spacer()
            Button { state.perform(.setHooks(install: true)) } label: {
                Text("Turn On")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundColor(.white)
                    .padding(.horizontal, 11)
                    .padding(.vertical, 4)
                    .background(Capsule().fill(Color.orange))
            }
            .buttonStyle(.plain)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.orange.opacity(0.12)))
        .padding(.horizontal, 12)
        .padding(.bottom, 10)
    }

    // MARK: Content

    @ViewBuilder private var content: some View {
        if let error = state.error, state.status == nil {
            Placeholder(symbol: "exclamationmark.triangle", tint: .red, title: "Can't read agent sessions", detail: error)
        } else if let projects = state.status?.projects {
            if projects.isEmpty {
                Placeholder(symbol: "tray", tint: .secondary, title: "No agent sessions yet",
                            detail: "Work in Claude Code, Codex or Cursor and your projects show up here.")
            } else {
                VStack(alignment: .leading, spacing: 0) {
                    HStack {
                        Text("RECENT PROJECTS").font(.system(size: 10, weight: .semibold)).foregroundColor(.secondary)
                        Spacer()
                        Text("Hover a project to switch agents").font(.system(size: 10)).foregroundColor(.secondary.opacity(0.8))
                    }
                    .padding(.horizontal, 16)
                    .padding(.top, 10)
                    .padding(.bottom, 4)
                    ScrollView {
                        VStack(spacing: 2) {
                            ForEach(projects.prefix(12), id: \.path) { ProjectRow(project: $0, state: state) }
                        }
                        .padding(.horizontal, 6)
                        .padding(.bottom, 8)
                    }
                    .frame(height: min(CGFloat(projects.prefix(12).count) * 52 + 10, 372))
                }
            }
        } else {
            HStack(spacing: 8) {
                ProgressView().controlSize(.small)
                Text("Reading agent sessions…").font(.system(size: 12)).foregroundColor(.secondary)
            }
            .frame(maxWidth: .infinity)
            .padding(.vertical, 34)
        }
    }

    // MARK: Footer

    private var footer: some View {
        HStack {
            if let updated = state.updated {
                Text("Updated \(updated.formatted(date: .omitted, time: .shortened))")
            } else {
                Text("Updating…")
            }
            Spacer()
            Button {
                state.perform(.showWindow)
            } label: {
                HStack(spacing: 4) {
                    Text("Open Context Relay")
                    Image(systemName: "arrow.up.forward.app")
                }
            }
            .buttonStyle(.plain)
            .foregroundColor(.accentColor)
        }
        .font(.system(size: 11))
        .foregroundColor(.secondary)
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
    }
}

// MARK: - Rows

private struct ProjectRow: View {
    let project: Project
    @ObservedObject var state: AppState
    @State private var hovering = false

    private var lastAgent: Agent? { Agent(rawValue: project.lastAgent) }
    private var showActions: Bool { hovering || state.previewHoverPath == project.path }
    private var contextPath: String { project.path + "/.relay/CONTEXT.md" }

    var body: some View {
        HStack(spacing: 10) {
            AgentAvatar(agent: lastAgent, size: 30)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(project.name)
                        .font(.system(size: 13, weight: .semibold))
                        .lineLimit(1)
                        .truncationMode(.middle)
                    if let branch = project.branch, !branch.isEmpty {
                        BranchChip(branch: branch, changes: project.changedFiles)
                    }
                }
                if showActions {
                    HStack(spacing: 4) {
                        Text("Continue in").font(.system(size: 10)).foregroundColor(.secondary)
                        ForEach(Agent.allCases, id: \.self) { agent in
                            ContinueButton(agent: agent, isLast: agent == lastAgent, disabled: state.busy) {
                                state.perform(.continueIn(agent: agent, project: project.path))
                            }
                        }
                    }
                    .transition(.opacity)
                } else {
                    Text(subtitle)
                        .font(.system(size: 11))
                        .foregroundColor(.secondary)
                        .lineLimit(1)
                        .transition(.opacity)
                }
            }
            Spacer(minLength: 6)
            if !showActions {
                Text(Format.relativeTime(project.lastActive) ?? "")
                    .font(.system(size: 11))
                    .foregroundColor(.secondary)
                    .transition(.opacity)
            }
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 8)
        .frame(height: 50)
        .background(
            RoundedRectangle(cornerRadius: 9, style: .continuous)
                .fill(showActions ? Color.primary.opacity(0.07) : Color.clear)
        )
        .contentShape(Rectangle())
        .onHover { h in withAnimation(.easeOut(duration: 0.14)) { hovering = h } }
        .contextMenu {
            ForEach(Agent.allCases, id: \.self) { agent in
                Button("Continue in \(agent.displayName)") { state.perform(.continueIn(agent: agent, project: project.path)) }
            }
            Divider()
            if FileManager.default.fileExists(atPath: contextPath) {
                Button("Open Last Handoff") { state.perform(.open(path: contextPath)) }
            }
            Button("Show in Finder") { state.perform(.reveal(path: project.path)) }
        }
        .help(project.path)
    }

    private var subtitle: String {
        let who = Agent.displayName(project.lastAgent)
        guard let title = project.sessions.first?.title, !title.isEmpty else { return who }
        return "\(who) · \(Format.truncate(title, 70))"
    }
}

private struct ContinueButton: View {
    let agent: Agent
    let isLast: Bool
    let disabled: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 3) {
                Image(systemName: agent.symbol).font(.system(size: 8, weight: .bold))
                Text(agent.shortName).font(.system(size: 10, weight: .semibold))
            }
            .foregroundColor(hovering ? .white : agent.color)
            .padding(.horizontal, 7)
            .padding(.vertical, 2)
            .background(Capsule().fill(agent.color.opacity(hovering ? 1 : 0.15)))
            .overlay(Capsule().stroke(agent.color.opacity(isLast ? 0.55 : 0), lineWidth: 1))
            .fixedSize()
        }
        .buttonStyle(.plain)
        .disabled(disabled)
        .opacity(disabled ? 0.4 : 1)
        .onHover { hovering = $0 }
        .help(isLast ? "Continue in \(agent.displayName) (fresh session)" : "Continue in \(agent.displayName)")
    }
}

// MARK: - Small components

extension Agent {
    var shortName: String {
        switch self {
        case .claude: return "Claude"
        case .codex: return "Codex"
        case .cursor: return "Cursor"
        }
    }

    var symbol: String {
        switch self {
        case .claude: return "sparkle"
        case .codex: return "terminal.fill"
        case .cursor: return "cursorarrow.rays"
        }
    }
}

struct LogoTile: View {
    let size: CGFloat
    var body: some View {
        RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
            .fill(LinearGradient(colors: [Color(red: 0.36, green: 0.30, blue: 0.95), Color(red: 0.62, green: 0.28, blue: 0.86)],
                                 startPoint: .topLeading, endPoint: .bottomTrailing))
            .frame(width: size, height: size)
            .overlay(
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: size * 0.46, weight: .bold))
                    .foregroundColor(.white)
            )
            .shadow(color: Color(red: 0.45, green: 0.3, blue: 0.9).opacity(0.35), radius: 4, y: 2)
    }
}

private struct StatusPill: View {
    let on: Bool?
    var body: some View {
        let color: Color = on == nil ? .secondary : (on! ? .green : .orange)
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(on == nil ? "Checking…" : (on! ? "Auto-handoff on" : "Auto-handoff off"))
                .font(.system(size: 11, weight: .medium))
                .foregroundColor(on == nil ? .secondary : color)
        }
        .padding(.horizontal, 7)
        .padding(.vertical, 2)
        .background(Capsule().fill(color.opacity(0.13)))
    }
}

private struct AgentAvatar: View {
    let agent: Agent?
    let size: CGFloat
    var body: some View {
        let color = agent?.color ?? .gray
        Circle()
            .fill(color.opacity(0.16))
            .frame(width: size, height: size)
            .overlay(
                Image(systemName: agent?.symbol ?? "questionmark")
                    .font(.system(size: size * 0.42, weight: .semibold))
                    .foregroundColor(color)
            )
            .help(agent.map { "Last worked on by \($0.displayName)" } ?? "")
    }
}

private struct BranchChip: View {
    let branch: String
    let changes: Int?
    var body: some View {
        HStack(spacing: 3) {
            Image(systemName: "arrow.triangle.branch").font(.system(size: 8, weight: .semibold))
            Text(branch).lineLimit(1)
            if let n = changes, n > 0 {
                Text("· \(n)").foregroundColor(.orange)
            }
        }
        .font(.system(size: 10, weight: .medium, design: .monospaced))
        .foregroundColor(.secondary)
        .padding(.horizontal, 6)
        .padding(.vertical, 1)
        .background(Capsule().fill(Color.primary.opacity(0.07)))
        .fixedSize()
        .help(changes.map { "\($0) uncommitted file\($0 == 1 ? "" : "s")" } ?? branch)
    }
}

private struct IconButton: View {
    let symbol: String
    let help: String
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 13, weight: .medium))
                .frame(width: 26, height: 26)
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(Color.primary.opacity(hovering ? 0.09 : 0)))
        }
        .buttonStyle(.plain)
        .foregroundColor(.secondary)
        .onHover { hovering = $0 }
        .help(help)
    }
}

private struct Placeholder: View {
    let symbol: String
    let tint: Color
    let title: String
    let detail: String
    var body: some View {
        VStack(spacing: 8) {
            Image(systemName: symbol).font(.system(size: 26, weight: .light)).foregroundColor(tint)
            Text(title).font(.system(size: 13, weight: .semibold))
            Text(detail).font(.system(size: 11)).foregroundColor(.secondary)
                .multilineTextAlignment(.center).fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 30)
        .padding(.vertical, 30)
    }
}

private struct NoticeStrip: View {
    let text: String
    let busy: Bool
    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            if busy {
                ProgressView().controlSize(.small).frame(width: 14, height: 14)
            } else {
                Image(systemName: "checkmark.circle.fill").foregroundColor(.green)
            }
            Text(text).font(.system(size: 11)).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.green.opacity(busy ? 0.06 : 0.11)))
        .padding(.horizontal, 12)
        .padding(.bottom, 10)
        .transition(.opacity.combined(with: .move(edge: .bottom)))
    }
}
