import AppKit
import SwiftUI

/// `ContextRelay --self-test <status.json> [--now <iso8601>]`: prints the menu structure and exits.
func runSelfTest(_ args: [String]) -> Never {
    guard let i = args.firstIndex(of: "--self-test"), i + 1 < args.count else {
        FileHandle.standardError.write(Data("usage: --self-test <status.json>\n".utf8))
        exit(2)
    }
    var input = MenuInput()
    if let j = args.firstIndex(of: "--now"), j + 1 < args.count, let d = Format.parseDate(args[j + 1]) {
        input.now = d
    }
    do {
        let data = try Data(contentsOf: URL(fileURLWithPath: args[i + 1]))
        input.status = try StatusResponse.decode(data)
    } catch {
        FileHandle.standardError.write(Data("self-test: \(error)\n".utf8))
        exit(1)
    }
    print(MenuBuilder.render(MenuBuilder.build(input)))
    exit(0)
}

if CommandLine.arguments.contains("--self-test") { runSelfTest(CommandLine.arguments) }

/// `ContextRelay --snapshot <status.json> <out.png>`: renders the main window offscreen and exits.
if let i = CommandLine.arguments.firstIndex(of: "--snapshot"), i + 2 < CommandLine.arguments.count {
    let args = CommandLine.arguments
    _ = NSApplication.shared
    let state = AppState()
    state.status = try? StatusResponse.decode(Data(contentsOf: URL(fileURLWithPath: args[i + 1])))
    state.updated = Date()
    let view = NSHostingView(rootView: MainView(state: state))
    view.frame = NSRect(x: 0, y: 0, width: 760, height: 600)
    let window = NSWindow(contentRect: view.frame, styleMask: [.titled], backing: .buffered, defer: false)
    window.contentView = view
    view.layoutSubtreeIfNeeded()
    RunLoop.main.run(until: Date().addingTimeInterval(0.5))
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
