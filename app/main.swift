import AppKit
import SwiftUI

/// `ContextRelay --snapshot <status.json> <out.png> [--view window|menubar] [--dark] [--hover <n>]`
/// renders a view offscreen to a PNG and exits (used to check the UI without clicking anything).
if let i = CommandLine.arguments.firstIndex(of: "--snapshot"), i + 2 < CommandLine.arguments.count {
    let args = CommandLine.arguments
    func option(_ name: String) -> String? {
        args.firstIndex(of: name).flatMap { $0 + 1 < args.count ? args[$0 + 1] : nil }
    }
    _ = NSApplication.shared
    let state = AppState()
    state.status = try? StatusResponse.decode(Data(contentsOf: URL(fileURLWithPath: args[i + 1])))
    state.updated = Date()
    if let n = option("--hover").flatMap(Int.init), let projects = state.status?.projects, n < projects.count {
        state.previewHoverPath = projects[n].path
    }
    if let notice = option("--notice") { state.notice = notice }
    let menubar = option("--view") == "menubar"
    let root: AnyView = menubar
        ? AnyView(MenuBarView(state: state).background(Color(nsColor: .windowBackgroundColor)))
        : AnyView(MainView(state: state))
    let view = NSHostingView(rootView: root)
    view.appearance = NSAppearance(named: args.contains("--dark") ? .darkAqua : .aqua)
    let size = menubar ? view.fittingSize : NSSize(width: 760, height: 600)
    view.frame = NSRect(origin: .zero, size: size)
    let window = NSWindow(contentRect: view.frame, styleMask: [.borderless], backing: .buffered, defer: false)
    window.contentView = view
    view.layoutSubtreeIfNeeded()
    RunLoop.main.run(until: Date().addingTimeInterval(0.6))
    guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { exit(1) }
    view.cacheDisplay(in: view.bounds, to: rep)
    try? rep.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: args[i + 2]))
    exit(0)
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
