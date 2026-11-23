import AppKit
import UniformTypeIdentifiers
import WebKit

@MainActor
final class TranslationRoom: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    private let address = URL(string: "http://127.0.0.1:18647/")!
    private let title = "PDF翻訳室 Mark.Ⅱ"
    private var window: NSWindow!
    private var webView: WKWebView!
    private var loading: NSStackView!
    private var server: Process?
    private var installer: Process?
    private var loadingLabel: NSTextField!
    private var startup: Task<Void, Never>?

    func applicationDidFinishLaunching(_ notification: Notification) {
        installMenu()
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .nonPersistent()
        webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.isHidden = true

        let container = NSView()
        container.wantsLayer = true
        container.layer?.backgroundColor = NSColor(srgbRed: 0.965, green: 0.961, blue: 0.937, alpha: 1).cgColor
        let spinner = NSProgressIndicator()
        spinner.style = .spinning
        spinner.startAnimation(nil)
        loadingLabel = NSTextField(labelWithString: "PDF翻訳室 Mark.Ⅱを開いています…")
        loading = NSStackView(views: [spinner, loadingLabel])
        loading.orientation = .vertical
        loading.spacing = 14

        for view in [webView!, loading!] {
            view.translatesAutoresizingMaskIntoConstraints = false
            container.addSubview(view)
        }
        NSLayoutConstraint.activate([
            webView.leadingAnchor.constraint(equalTo: container.leadingAnchor),
            webView.trailingAnchor.constraint(equalTo: container.trailingAnchor),
            webView.topAnchor.constraint(equalTo: container.topAnchor),
            webView.bottomAnchor.constraint(equalTo: container.bottomAnchor),
            loading.centerXAnchor.constraint(equalTo: container.centerXAnchor),
            loading.centerYAnchor.constraint(equalTo: container.centerYAnchor),
        ])

        window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 960, height: 780),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false
        )
        window.title = title
        window.minSize = NSSize(width: 480, height: 560)
        window.contentView = container
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        startup = Task { await start() }
    }

    private func installMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem()
        menu.addItem(appItem)
        let appMenu = NSMenu(title: title)
        appMenu.addItem(withTitle: "PDF翻訳室 Mark.Ⅱを終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        appItem.submenu = appMenu
        let editItem = NSMenuItem(title: "編集", action: nil, keyEquivalent: "")
        menu.addItem(editItem)
        let editMenu = NSMenu(title: "編集")
        editMenu.addItem(withTitle: "切り取り", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "コピー", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "ペースト", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "すべて選択", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = editMenu
        NSApp.mainMenu = menu
    }

    private func serverIsReady() async -> Bool {
        let request = URLRequest(url: address.appending(path: "api/health"), timeoutInterval: 0.5)
        guard let (data, _) = try? await URLSession.shared.data(for: request),
              let status = try? JSONDecoder().decode([String: String].self, from: data)
        else { return false }
        return status["app"] == "pdf-translation-room-mac"
    }

    private func start() async {
        if await serverIsReady() {
            webView.load(URLRequest(url: address))
            return
        }
        let home = FileManager.default.homeDirectoryForCurrentUser
        let data = home.appending(path: "Library/Application Support/PDF翻訳室")
        let managedPython = data.appending(path: "runtime/bin/python")
        let readyFile = data.appending(path: "runtime-ready-1")
        let existingPython = home.appending(path: ".local/share/uv/tools/pdf2zh-next/bin/python")
        let files = FileManager.default
        let hasManaged = files.isExecutableFile(atPath: managedPython.path)
            && files.fileExists(atPath: readyFile.path)
        let python: URL
        if hasManaged {
            python = managedPython
        } else if files.isExecutableFile(atPath: existingPython.path) {
            python = existingPython
        } else {
            guard await confirmSetup() else { NSApp.terminate(nil); return }
            guard await setup(in: data) else {
                showError("セットアップに失敗しました。接続を確認してアプリを開き直してください。詳細は setup.log に記録されています。")
                return
            }
            python = managedPython
        }
        guard let script = Bundle.main.url(forResource: "server", withExtension: "py") else {
            showError("PDF翻訳の実行ファイルが見つかりません。")
            return
        }

        let process = Process()
        process.executableURL = python
        process.arguments = ["-B", script.path]
        process.currentDirectoryURL = Bundle.main.resourceURL
        var environment = ProcessInfo.processInfo.environment
        environment["PATH"] = data.appending(path: "bin").path
            + ":" + home.appending(path: ".local/bin").path
            + ":/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        environment["PDF_ROOM_DATA"] = data.path
        for name in ["OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "PDF_ROOM_API_KEY"] {
            environment.removeValue(forKey: name)
        }
        process.environment = environment
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = FileHandle.nullDevice
        process.standardError = FileHandle.nullDevice
        do { try process.run() }
        catch {
            showError("PDF翻訳を起動できませんでした。")
            return
        }
        server = process

        for _ in 0..<40 {
            if Task.isCancelled { return }
            if await serverIsReady() {
                webView.load(URLRequest(url: address))
                return
            }
            if !process.isRunning { break }
            try? await Task.sleep(for: .milliseconds(200))
        }
        showError("PDF翻訳室 Mark.Ⅱが起動しませんでした。アプリを開き直してください。")
    }

    private func confirmSetup() async -> Bool {
        let alert = NSAlert()
        alert.messageText = "PDF翻訳室 Mark.Ⅱの初回セットアップ"
        alert.informativeText = "翻訳用のライブラリとフォントをダウンロードします。インターネット接続が必要です。"
        alert.addButton(withTitle: "セットアップ")
        alert.addButton(withTitle: "キャンセル")
        return await withCheckedContinuation { result in
            alert.beginSheetModal(for: window) { code in
                result.resume(returning: code == .alertFirstButtonReturn)
            }
        }
    }

    private func setup(in data: URL) async -> Bool {
        guard let script = Bundle.main.url(forResource: "install-runtime", withExtension: nil) else {
            return false
        }
        loadingLabel.stringValue = "初回セットアップ中です。数分かかることがあります…"
        let log = data.appending(path: "setup.log")
        do {
            try FileManager.default.createDirectory(at: data, withIntermediateDirectories: true)
            _ = FileManager.default.createFile(atPath: log.path, contents: nil,
                                                attributes: [.posixPermissions: 0o600])
            let output = try FileHandle(forWritingTo: log)
            defer { try? output.close() }
            let process = Process()
            process.executableURL = script
            process.arguments = [data.path]
            process.standardInput = FileHandle.nullDevice
            process.standardOutput = output
            process.standardError = output
            installer = process
            let succeeded: Bool = await withCheckedContinuation { result in
                process.terminationHandler = { finished in
                    result.resume(returning: finished.terminationStatus == 0)
                }
                do { try process.run() }
                catch { result.resume(returning: false) }
            }
            if succeeded { loadingLabel.stringValue = "PDF翻訳室 Mark.Ⅱを開いています…" }
            return succeeded
        } catch { return false }
    }

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
        alert.addButton(withTitle: "閉じる")
        alert.beginSheetModal(for: window) { _ in NSApp.terminate(nil) }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }

    func applicationWillTerminate(_ notification: Notification) {
        startup?.cancel()
        if let server, server.isRunning { server.terminate() }
        if let installer, installer.isRunning { installer.terminate() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading.isHidden = true
        webView.isHidden = false
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        // Attachment responses interrupt navigation while leaving the current page in place.
        guard webView.isHidden else { return }
        showError("翻訳画面を読み込めませんでした。アプリを開き直してください。")
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url, url.scheme == address.scheme,
              url.host == address.host, url.port == address.port
        else { decisionHandler(.cancel); return }
        decisionHandler(action.shouldPerformDownload ? .download : .allow)
    }

    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse,
                 decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let attachment = (response.response as? HTTPURLResponse)?
            .value(forHTTPHeaderField: "Content-Disposition")?.lowercased().hasPrefix("attachment") ?? false
        decisionHandler(attachment ? .download : .allow)
    }

    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let panel = NSOpenPanel()
        panel.allowedContentTypes = [.pdf]
        panel.allowsMultipleSelection = parameters.allowsMultipleSelection
        panel.canChooseDirectories = false
        panel.prompt = "選ぶ"
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.urls : nil)
        }
    }

    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) {
        download.delegate = self
    }

    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) {
        download.delegate = self
    }

    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse,
                  suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let panel = NSSavePanel()
        panel.allowedContentTypes = [.pdf]
        panel.nameFieldStringValue = suggestedFilename
        panel.prompt = "保存"
        panel.beginSheetModal(for: window) { result in
            completionHandler(result == .OK ? panel.url : nil)
        }
    }

    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        if (error as NSError).code == NSURLErrorCancelled { return }
        let alert = NSAlert()
        alert.messageText = "PDFを保存できませんでした"
        alert.informativeText = error.localizedDescription
        alert.beginSheetModal(for: window)
    }
}

@main
struct Main {
    @MainActor
    static func main() {
        let application = NSApplication.shared
        let delegate = TranslationRoom()
        application.delegate = delegate
        application.setActivationPolicy(.regular)
        withExtendedLifetime(delegate) { application.run() }
    }
}
