param([Parameter(Mandatory = $true)][string]$Data)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$bin = Join-Path $Data "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$env:CODEX_HOME = Join-Path $Data "codex-package"
$env:CODEX_INSTALL_DIR = $bin
$env:CODEX_NON_INTERACTIVE = "1"
$env:Path = "$bin;$env:Path"
$installer = Join-Path $Data "codex-install.ps1"
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://chatgpt.com/codex/install.ps1" -OutFile $installer
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { throw "Codex installer failed with status $LASTEXITCODE" }
    $exe = Join-Path $bin "codex.exe"
    if (-not (Test-Path -LiteralPath $exe)) { throw "Codex executable not found" }
    & $exe --version
    if ($LASTEXITCODE -ne 0) { throw "Codex could not start" }
} finally {
    Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue
}
