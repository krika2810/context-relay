import AppKit
import ServiceManagement
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let popover = NSPopover()
    private var status: StatusResponse?
    private var lastError: String?
    private var refreshing = false
    private var timer: Timer?
    private var noticeClear: DispatchWorkItem?
    private let state = AppState()
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let button = statusItem.button {
            let image = NSImage(systemSymbolName: "arrow.triangle.2.circlepath", accessibilityDescription: "Context Relay")
            image?.isTemplate = true
            button.image = image
            button.toolTip = "Context Relay"
            button.target = self
            button.action = #selector(togglePopover(_:))
        }
        let panel = NSHostingController(rootView: MenuBarView(state: state))
        panel.sizingOptions = .preferredContentSize
        popover.contentViewController = panel
        popover.behavior = .transient
        popover.animates = true
        state.launchAtLogin = SMAppService.mainApp.status == .enabled
        NSApp.mainMenu = mainMenu()
        state.perform = { [weak self] action in self?.perform(action) }
        showWindow()

        if ProcessInfo.processInfo.environment["RELAY_OPEN_PANEL"] != nil {  // UI testing: open the panel at launch
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) { [weak self] in self?.togglePopover(nil) }
        }
        timer = Timer.scheduledTimer(withTimeInterval: 60, repeats: true) { [weak self] _ in self?.refresh() }
        syncHooks()
    }

    /// Auto-handoff is on by default: turn it on at first launch, and on every later launch re-install
    /// (idempotent) so the hooks point at this copy of the app even if it was moved.
    private func syncHooks() {
        let key = "hooksConfigured"
        let defaults = UserDefaults.standard
        Engine.call(["hooks", "status", "--json"], as: ActionResponse.self) { [weak self] result in
            let anyOn = (try? result.get())?.hooks?.values.contains(true) ?? false
            guard !defaults.bool(forKey: key) || anyOn else { self?.refresh(); return }
            Engine.call(["hooks", "install", "--json"], as: ActionResponse.self) { result in
                if case .success(let r) = result, r.ok { defaults.set(true, forKey: key) }
                self?.refresh()
            }
        }
    }

    // MARK: Refresh

    private func refresh() {
        guard !refreshing else { return }
        refreshing = true
        state.refreshing = true
        Engine.call(["status", "--json"], as: StatusResponse.self) { [weak self] result in
            guard let self = self else { return }
            self.refreshing = false
            self.state.refreshing = false
            switch result {
            case .success(let s): self.status = s; self.lastError = nil
            case .failure(let e): self.lastError = e.message
            }
            self.state.status = self.status
            self.state.error = self.lastError
            self.state.updated = Date()
        }
    }

    // MARK: Menu bar panel

    @objc private func togglePopover(_ sender: Any?) {
        guard let button = statusItem.button else { return }
        if popover.isShown {
            popover.performClose(sender)
            return
        }
        refresh()
        popover.show(relativeTo: button.bounds, of: button, preferredEdge: .minY)
        popover.contentViewController?.view.window?.makeKey()
        NSApp.activate(ignoringOtherApps: true)
    }

    // MARK: Actions

    func perform(_ action: MenuAction) {
        switch action {
        case .continueIn(let agent, let project):
            state.busy = true
            state.notice = "Handing off to \(agent.displayName)…"
            Engine.call(["continue", agent.rawValue, "--project", project, "--json"], as: ActionResponse.self) { [weak self] result in
                guard let self = self else { return }
                self.state.busy = false
                if case .success(let r) = result, r.ok {
                    let from = r.fromAgent.map { Agent.displayName($0) } ?? "the previous agent"
                    self.showNotice(agent == .cursor
                        ? "Opened Cursor with \(from)'s context. Press Return in Cursor's chat to start (the prompt is also on your clipboard)."
                        : "Opened \(agent.displayName) with \(from)'s context.")
                } else {
                    self.state.notice = nil
                }
                self.report(result, title: "Could not continue in \(agent.displayName)")
                self.refresh()
            }
        case .showWindow:
            popover.performClose(nil)
            showWindow()
        case .setHooks(let install):
            UserDefaults.standard.set(true, forKey: "hooksConfigured")
            Engine.call(["hooks", install ? "install" : "uninstall", "--json"], as: ActionResponse.self) { [weak self] result in
                self?.report(result, title: "Could not \(install ? "turn on" : "turn off") auto-handoff")
                self?.refresh()
            }
        case .open(let path):
            NSWorkspace.shared.open(URL(fileURLWithPath: path))
        case .reveal(let path):
            NSWorkspace.shared.activateFileViewerSelecting([URL(fileURLWithPath: path)])
        case .refresh:
            refresh()
        case .toggleLaunchAtLogin:
            toggleLaunchAtLogin()
        case .quit:
            NSApp.terminate(nil)
        }
    }

    /// Shows a confirmation in the window and the menu bar panel, then fades it out.
    private func showNotice(_ text: String) {
        state.notice = text
        noticeClear?.cancel()
        let clear = DispatchWorkItem { [weak self] in
            if self?.state.notice == text { self?.state.notice = nil }
        }
        noticeClear = clear
        DispatchQueue.main.asyncAfter(deadline: .now() + 8, execute: clear)
    }

    private func report(_ result: Result<ActionResponse, EngineError>, title: String) {
        switch result {
        case .success(let r) where r.ok: break
        case .success(let r): alert(title, r.error ?? "The engine reported a failure.")
        case .failure(let e): alert(title, "Engine error: " + e.message)
        }
    }

    private func toggleLaunchAtLogin() {
        do {
            if SMAppService.mainApp.status == .enabled {
                try SMAppService.mainApp.unregister()
            } else {
                try SMAppService.mainApp.register()
            }
        } catch {
            alert("Launch at Login failed", error.localizedDescription)
        }
        state.launchAtLogin = SMAppService.mainApp.status == .enabled
    }

    // MARK: Window

    func showWindow() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 760, height: 600),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable],
                             backing: .buffered, defer: false)
            w.title = "Context Relay"
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: MainView(state: state))
            w.center()
            w.setFrameAutosaveName("ContextRelayMain")
            window = w
        }
        NSApp.unhide(nil)
        window?.deminiaturize(nil)
        window?.makeKeyAndOrderFront(nil)
        // Activation can be refused (e.g. while a system dialog is up); still put the window on top.
        window?.orderFrontRegardless()
        NSApp.activate(ignoringOtherApps: true)
        refresh()
    }

    /// Switching to the app (Dock, Cmd-Tab, Spotlight) always shows the window.
    func applicationDidBecomeActive(_ notification: Notification) {
        // Opening the menu bar panel also activates the app; don't pop the window over it.
        if window?.isVisible != true && !popover.isShown { showWindow() }
    }

    /// Clicking the Dock icon or opening the app again brings the window back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showWindow()
        return true
    }

    /// Closing the window keeps the app (and auto-handoff UI) in the menu bar.
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    private func mainMenu() -> NSMenu {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Context Relay", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Context Relay", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(withTitle: "Quit Context Relay", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let windowItem = NSMenuItem()
        main.addItem(windowItem)
        let windowMenu = NSMenu(title: "Window")
        windowMenu.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowMenu.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windowItem.submenu = windowMenu
        NSApp.windowsMenu = windowMenu
        return main
    }

    private func alert(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.alertStyle = .warning
        alert.messageText = title
        alert.informativeText = message
        NSApp.activate(ignoringOtherApps: true)
        alert.runModal()
    }
}
