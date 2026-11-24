using System.Diagnostics;
using System.Net;
using System.Net.Sockets;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

internal static class Program
{
    [STAThread]
    private static void Main()
    {
        ApplicationConfiguration.Initialize();
        Application.Run(new TranslationRoom());
    }
}

internal sealed class TranslationRoom : Form
{
    private const string AppName = "PDF翻訳室 Mark.Ⅱ";
    private const string HealthIdentifier = "pdf-translation-room-mac";
    private readonly string data = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "PDF Translation Room");
    private readonly string resources = Path.Combine(AppContext.BaseDirectory, "Resources");
    private readonly WebView2 web = new() { Dock = DockStyle.Fill, Visible = false };
    private readonly Label status = new() { Dock = DockStyle.Fill, TextAlign = ContentAlignment.MiddleCenter };
    private readonly CancellationTokenSource closing = new();
    private readonly object logLock = new();
    private readonly HttpClient readiness = new(new HttpClientHandler { UseProxy = false })
    {
        Timeout = TimeSpan.FromSeconds(1),
    };
    private Process? server;
    private Process? installer;
    private Uri? address;
    private bool stopping;
    private bool stopped;

    internal TranslationRoom()
    {
        Text = AppName;
        ClientSize = new Size(940, 760);
        MinimumSize = new Size(500, 555);
        StartPosition = FormStartPosition.CenterScreen;
        BackColor = Color.FromArgb(246, 245, 239);
        status.Text = "PDF翻訳室を開いています…";
        using var icon = Environment.ProcessPath is string path ? Icon.ExtractAssociatedIcon(path) : null;
        if (icon is not null) Icon = (Icon)icon.Clone();
        Controls.Add(web);
        Controls.Add(status);
        Shown += async (_, _) => await StartAsync();
    }

    private async Task StartAsync()
    {
        try
        {
            Directory.CreateDirectory(data);
            var environment = await CoreWebView2Environment.CreateAsync(
                userDataFolder: Path.Combine(data, "WebView2"));
            await web.EnsureCoreWebView2Async(environment);
            if (closing.IsCancellationRequested) return;
            var browser = web.CoreWebView2;
            browser.Settings.AreDevToolsEnabled = false;
            browser.Settings.AreDefaultContextMenusEnabled = false;
            browser.Settings.AreHostObjectsAllowed = false;
            browser.NewWindowRequested += (_, args) => args.Handled = true;
            browser.NavigationStarting += (_, args) => args.Cancel = !IsLocal(args.Uri);
            browser.DownloadStarting += OnDownload;
            browser.NavigationCompleted += (_, args) =>
            {
                if (args.IsSuccess)
                {
                    status.Visible = false;
                    web.Visible = true;
                }
                else if (!web.Visible)
                    Error("翻訳画面を読み込めませんでした。アプリを開き直してください。");
            };

            string python = Path.Combine(data, "runtime", "Scripts", "python.exe");
            if (!File.Exists(python) || !File.Exists(Path.Combine(data, "runtime-ready-1")))
            {
                if (MessageBox.Show(this,
                    "初回のみ、PDF翻訳に必要なライブラリとフォントをダウンロードします。数分かかることがあります。",
                    AppName, MessageBoxButtons.OKCancel, MessageBoxIcon.Information) != DialogResult.OK)
                {
                    Close();
                    return;
                }
                status.Text = "初回セットアップ中です。数分かかることがあります…";
                installer = StartProcess("powershell.exe", "setup.log",
                    ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
                     Path.Combine(resources, "windows", "install-runtime.ps1"), data]);
                await installer.WaitForExitAsync(closing.Token);
                if (installer.ExitCode != 0 || !File.Exists(python))
                {
                    Error("初回セットアップに失敗しました。ネット接続を確認して開き直してください。");
                    return;
                }
            }

            status.Text = "PDF翻訳室を開いています…";
            int port = AvailablePort();
            address = new Uri($"http://127.0.0.1:{port}/");
            server = StartProcess(python, "server.log",
                ["-B", Path.Combine(resources, "server.py"), "--watch-parent"], port);
            for (int retry = 0; retry < 50 && !server.HasExited; retry++)
            {
                closing.Token.ThrowIfCancellationRequested();
                if (await ServerReadyAsync())
                {
                    browser.Navigate(address.AbsoluteUri);
                    return;
                }
                await Task.Delay(200, closing.Token);
            }
            Error("PDF翻訳を起動できませんでした。アプリを開き直してください。");
        }
        catch (WebView2RuntimeNotFoundException)
        {
            Error("Microsoft Edge WebView2 Runtimeが必要です。Microsoft公式サイトからインストールしてください。");
        }
        catch (OperationCanceledException) when (closing.IsCancellationRequested)
        {
        }
        catch (Exception exception)
        {
            WriteLog(Path.Combine(data, "server.log"), exception.GetType().Name);
            Error("PDF翻訳室を起動できませんでした。アプリを開き直してください。");
        }
    }

    private bool IsLocal(string value) => address is not null
        && Uri.TryCreate(value, UriKind.Absolute, out Uri? parsed)
        && parsed.Scheme == address.Scheme && parsed.Host == address.Host && parsed.Port == address.Port;

    private void OnDownload(object? sender, CoreWebView2DownloadStartingEventArgs args)
    {
        if (!IsLocal(args.DownloadOperation.Uri))
        {
            args.Cancel = true;
            return;
        }
        args.Handled = true;
        var pending = args.GetDeferral();
        BeginInvoke(() =>
        {
            try
            {
                using var dialog = new SaveFileDialog
                {
                    Filter = "PDF (*.pdf)|*.pdf",
                    DefaultExt = "pdf",
                    FileName = Path.GetFileName(args.ResultFilePath),
                    AddExtension = true,
                    OverwritePrompt = true,
                };
                if (dialog.ShowDialog(this) == DialogResult.OK)
                    args.ResultFilePath = dialog.FileName;
                else
                    args.Cancel = true;
            }
            finally { pending.Complete(); }
        });
    }

    private async Task<bool> ServerReadyAsync()
    {
        try
        {
            var body = await readiness.GetStringAsync(new Uri(address!, "api/health"), closing.Token);
            return body.Contains(HealthIdentifier, StringComparison.Ordinal);
        }
        catch (HttpRequestException) { return false; }
        catch (OperationCanceledException) when (!closing.IsCancellationRequested) { return false; }
    }

    private static int AvailablePort()
    {
        using var listener = new TcpListener(IPAddress.Loopback, 0);
        listener.Start();
        return ((IPEndPoint)listener.LocalEndpoint).Port;
    }

    private Process StartProcess(string executable, string logName, IEnumerable<string> arguments, int? port = null)
    {
        var start = new ProcessStartInfo(executable)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            RedirectStandardInput = true,
            WorkingDirectory = resources,
        };
        foreach (var argument in arguments) start.ArgumentList.Add(argument);
        start.Environment["PATH"] = Path.Combine(data, "bin") + ";" + Environment.GetEnvironmentVariable("PATH");
        start.Environment["PDF_ROOM_DATA"] = data;
        if (port is int number) start.Environment["PDF_ROOM_PORT"] = number.ToString();
        foreach (var name in new[] { "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "PDF_ROOM_API_KEY" })
            start.Environment.Remove(name);

        string log = Path.Combine(data, logName);
        File.WriteAllText(log, "");
        var process = new Process { StartInfo = start };
        process.OutputDataReceived += (_, line) => WriteLog(log, line.Data);
        process.ErrorDataReceived += (_, line) => WriteLog(log, line.Data);
        process.Start();
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        if (port is null) process.StandardInput.Close();
        return process;
    }

    private void WriteLog(string log, string? line)
    {
        if (line is null) return;
        try
        {
            lock (logLock) File.AppendAllText(log, line + Environment.NewLine);
        }
        catch (IOException) { }
        catch (UnauthorizedAccessException) { }
    }

    private void Error(string message)
    {
        if (closing.IsCancellationRequested) return;
        status.Text = message + "\n\nログの場所: " + data;
    }

    protected override void OnFormClosing(FormClosingEventArgs args)
    {
        base.OnFormClosing(args);
        if (stopped || args.CloseReason is CloseReason.WindowsShutDown)
        {
            stopping = true;
            closing.Cancel();
            Kill(installer);
            if (server is { HasExited: false }) server.StandardInput.Close();
            return;
        }
        args.Cancel = true;
        if (stopping) return;
        stopping = true;
        BeginInvoke(async () =>
        {
            closing.Cancel();
            Kill(installer);
            if (server is { HasExited: false })
            {
                server.StandardInput.Close();
                using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(6));
                try { await server.WaitForExitAsync(timeout.Token); }
                catch (OperationCanceledException) { Kill(server); }
            }
            stopped = true;
            Close();
        });
    }

    private static void Kill(Process? process)
    {
        if (process is null) return;
        try { if (!process.HasExited) process.Kill(entireProcessTree: true); }
        catch (InvalidOperationException) { }
        catch (System.ComponentModel.Win32Exception) { }
    }
}
