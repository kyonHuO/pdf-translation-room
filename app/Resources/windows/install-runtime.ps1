param([Parameter(Mandatory = $true)][string]$Data)
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$bin = Join-Path $Data "bin"
New-Item -ItemType Directory -Force -Path $bin | Out-Null
$env:UV_UNMANAGED_INSTALL = $bin
$env:UV_NO_MODIFY_PATH = "1"
$env:UV_CACHE_DIR = Join-Path $Data "cache"
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Data "python"
$env:UV_NO_CONFIG = "1"
$uv = Join-Path $bin "uv.exe"
$installer = Join-Path $Data "uv-install.ps1"

function Run-Checked([string]$Executable, [string[]]$Arguments) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Executable failed with status $LASTEXITCODE" }
}

if (-not (Test-Path -LiteralPath $uv)) {
    try {
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/0.13.7/install.ps1" -OutFile $installer
        Run-Checked "powershell.exe" @("-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $installer)
    } finally {
        Remove-Item -LiteralPath $installer -ErrorAction SilentlyContinue
    }
}

$runtime = Join-Path $Data "runtime"
$python = Join-Path $runtime "Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    Run-Checked $uv @("venv", "--python", "3.12", "--managed-python", $runtime)
}
$requirements = Join-Path $PSScriptRoot "runtime-requirements.txt"
Run-Checked $uv @("pip", "sync", "--python", $python, "--only-binary", ":all:", $requirements)
Run-Checked $python @("-B", "-c", "import fastapi, uvicorn, pymupdf; from pdf2zh_next.high_level import do_translate_async_stream")
$prepare = Join-Path (Split-Path $PSScriptRoot -Parent) "prepare_assets.py"
Run-Checked $python @("-B", $prepare)
[IO.File]::WriteAllText((Join-Path $Data "runtime-ready-1"), "")
