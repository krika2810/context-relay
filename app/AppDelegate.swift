import AppKit
import ServiceManagement
import SwiftUI

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let menu = NSMenu()
    private var status: StatusResponse?
    private var lastError: String?
    private var refreshing = false
    private var menuOpen = false
    private var timer: Timer?
    private let state = AppState()
    private var window: NSWindow?

    func applicationDidFinishLaunching(_ notification: Notification) {
        if let button = statusItem.button {
            let image = NSImage(systemSymbolName: "arrow.triangle.2.circlepath", accessibilityDescription: "Context Relay")
            image?.isTemplate = true
            button.image = image
            button.toolTip = "Context Relay"
        }
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
        populate()
        NSApp.mainMenu = mainMenu()
        state.perform = { [weak self] action in self?.perform(action) }
        showWindow()

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
        Engine.call(["status", "--json"], as: StatusResponse.self) { [weak self] result in
            guard let self = self else { return }
            self.refreshing = false
            switch result {
            case .success(let s): self.status = s; self.lastError = nil
            case .failure(let e): self.lastError = e.message
            }
            self.state.status = self.status
            self.state.error = self.lastError
            self.state.updated = Date()
            self.populate() // updates in place, also while the menu is open
        }
    }

    // MARK: NSMenuDelegate

    func menuNeedsUpdate(_ menu: NSMenu) { populate() }

    func menuWillOpen(_ menu: NSMenu) {
        menuOpen = true
        refresh()
    }

    func menuDidClose(_ menu: NSMenu) { menuOpen = false }

    // MARK: Menu rendering

    private func populate() {
        let input = MenuInput(status: status, error: lastError, launchAtLogin: SMAppService.mainApp.status == .enabled)
        menu.removeAllItems()
        add(MenuBuilder.build(input), to: menu)
    }

    private func add(_ entries: [MenuEntry], to menu: NSMenu) {
        for entry in entries {
            switch entry {
            case .separator:
                menu.addItem(.separator())
            case .item(let spec):
                menu.addItem(makeItem(spec))
            case .submenu(let spec, let children):
                let item = makeItem(spec)
                let sub = NSMenu(title: spec.title)
                sub.autoenablesItems = false
                add(children, to: sub)
                item.submenu = sub
                item.isEnabled = true
                menu.addItem(item)
            }
        }
    }

    private func makeItem(_ spec: ItemSpec) -> NSMenuItem {
        let item = NSMenuItem(title: spec.title, action: nil, keyEquivalent: spec.keyEquivalent)
        if let action = spec.action {
            item.action = #selector(handleMenuItem(_:))
            item.target = self
            item.representedObject = action
        }
        item.isEnabled = spec.action != nil
        item.state = spec.checked ? .on : .off
        if let sub = spec.subtitle {
            let text = NSMutableAttributedString(string: spec.title, attributes: [.font: NSFont.menuFont(ofSize: 0)])
            text.append(NSAttributedString(string: "  " + sub, attributes: [
                .font: NSFont.menuFont(ofSize: NSFont.smallSystemFontSize),
                .foregroundColor: NSColor.secondaryLabelColor,
            ]))
            item.attributedTitle = text
        }
        return item
    }

    // MARK: Actions

    @objc private func handleMenuItem(_ sender: NSMenuItem) {
        guard let action = sender.representedObject as? MenuAction else { return }
        perform(action)
    }

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
                    self.state.notice = agent == .cursor
                        ? "Opened Cursor with \(from)'s context. Press Return in Cursor's chat to start (prompt is also on your clipboard)."
                        : "Opened \(agent.displayName) with \(from)'s context."
                } else {
                    self.state.notice = nil
                }
                self.report(result, title: "Could not continue in \(agent.displayName)")
                self.refresh()
            }
        case .showWindow:
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
        populate()
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
        if window?.isVisible != true { showWindow() }
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
