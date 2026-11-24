# PDF翻訳室 Mark.Ⅱ

英語のPDFを日本語に翻訳するデスクトップアプリです。PDFのレイアウトをできるだけ保った日本語版と、原文と日本語を左右に並べた対訳版を保存できます。

## ダウンロード

[macOS版をダウンロード](https://github.com/kyonHuO/pdf-translation-room/releases/latest/download/PDF-Translation-Room-Mark-II-macOS.zip)

1. ZIPを解凍して「PDF翻訳室 Mark.Ⅱ.app」を開きます。
2. 初回に案内が出たら「セットアップ」を押します。ライブラリとフォントのダウンロードにインターネット接続が必要です。
3. 「翻訳設定」で翻訳先を選び、英語のPDFを選んで「翻訳する」を押します。
4. 「日本語版」または「対訳版」を選んで保存します。

macOS 14以降向けです。Appleシリコン用とIntel用の実行ファイルを同梱しています。実機での動作確認はAppleシリコンで行いました。配布版はAppleの公証を受けていません。初回にmacOSが起動を止めた場合は、[Appleサポート](https://support.apple.com/ja-jp/102445)をご確認ください。

### Windows 11向け試用版

[Windows x64版をダウンロード](https://github.com/kyonHuO/pdf-translation-room/releases/download/v2.0.0-windows-preview.1/PDF-Translation-Room-Mark-II-Windows-x64.zip)

ZIPをすべて展開して、同梱ファイルと同じフォルダーから `PDF-Translation-Room.exe` を開きます。初回は翻訳用のライブラリとフォントをダウンロードします。Microsoft Edge WebView2 Runtimeが必要です。

Windows向けのビルドと配布ZIPの検証は済んでいます。Windows実機での起動・翻訳は未確認です。実行ファイルにコード署名はありません。

## 翻訳先

- **Codex**では、ご自身のChatGPTアカウントのCodex利用枠を使います。必要に応じて公式Codex CLIをアプリからセットアップできます。
- **OpenAI互換API**では、接続先が指定するBase URL、モデル名、APIキーを入力します。Chat Completionsに対応する接続先が必要です。Base URLの例は `https://api.openai.com/v1` です。末尾の `/chat/completions` はアプリが追加します。料金や利用上限は接続先によります。

PDFの解析と組み直しはMac上で行い、翻訳する本文を選んだ接続先に送ります。互換APIキーはアプリの使用中だけメモリで保持します。Codexのログイン情報は公式CLIが管理します。

文字を選択できる英語PDFに対応し、ファイルの上限は50 MBです。画像だけのPDFは先にOCRで文字認識してください。

## ソースからビルド

Xcode Command Line ToolsがインストールされたMacで実行します。

```sh
./scripts/build-release.sh
```

ZIPは `dist/` に生成されます。アプリのウィンドウにはAppKitとWebKitを使い、PDFの解析と出力はPDFMathTranslate NextとBabelDOCが担当します。

### Windows x64実行ファイルをビルド

.NET 10 SDKでWindows用の実行ファイルと依存ファイルを生成します。

```sh
dotnet publish windows/PdfTranslationRoom.csproj -c Release -r win-x64 --self-contained true -o dist/windows-x64
```
