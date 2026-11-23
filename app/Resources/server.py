"""A loopback-only interface for this Mac's existing pdf2zh installation."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import secrets
from shutil import which
import signal
import socket
import sys
from tempfile import TemporaryDirectory
from time import monotonic
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field, SecretStr
from typing import Literal
import uvicorn

RESOURCES = Path(__file__).resolve().parent
PORT = 18647
HOST = f"127.0.0.1:{PORT}"
ADDRESS = f"http://{HOST}"
MAX_BYTES = 50 * 1024 * 1024
RESULT_LIFETIME = 2 * 60 * 60
WORKER_COMMAND = (sys.executable, str(RESOURCES / "translate_worker.py"))
FORMATS = {"mono": "日本語", "dual": "対訳"}


class ProviderInput(BaseModel):
    mode: Literal["codex", "api"]
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=200)
    api_key: SecretStr = Field(default=SecretStr(""))


@dataclass(frozen=True)
class Provider:
    mode: str = "codex"
    base_url: str = ""
    model: str = ""
    api_key: str = field(default="", repr=False)

    def public(self):
        return {"mode": self.mode, "base_url": self.base_url, "model": self.model}


provider = Provider()


@dataclass
class Job:
    id: str
    name: str
    directory: TemporaryDirectory
    provider: Provider = field(default_factory=Provider, repr=False)
    state: str = "uploading"
    progress: int = 0
    stage: str = "PDFを準備しています"
    count: str = ""
    error: str = ""
    ended: float = 0
    process: asyncio.subprocess.Process | None = field(default=None, repr=False)
    task: asyncio.Task | None = field(default=None, repr=False)

    @property
    def folder(self):
        return Path(self.directory.name)

    def public(self):
        return {key: getattr(self, key) for key in
                ("id", "name", "state", "progress", "stage", "count", "error")}


jobs: dict[str, Job] = {}
login_process: asyncio.subprocess.Process | None = None
login_starting = False
install_task: asyncio.Task | None = None
install_failed = False


def codex_command():
    bundled = Path("/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex")
    return os.environ.get("PDF_ROOM_CODEX_BIN") or which("codex") or (
        str(bundled) if os.access(bundled, os.X_OK) else None
    )


async def account_state():
    if install_task and not install_task.done():
        return "installing"
    binary = codex_command()
    if not binary:
        return "install-failed" if install_failed else "missing"
    if login_starting or (login_process and login_process.returncode is None):
        return "signing-in"
    try:
        process = await asyncio.create_subprocess_exec(
            binary, "login", "status", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        except asyncio.TimeoutError:
            if process.returncode is None:
                process.kill()
            await process.communicate()
            return "unavailable"
    except OSError:
        return "unavailable"
    if process.returncode == 0 and b"logged in using chatgpt" in output.lower():
        return "ready"
    return "signed-out"


async def prune():
    while True:
        for job in list(jobs.values()):
            if job.ended and monotonic() - job.ended > RESULT_LIFETIME:
                jobs.pop(job.id).directory.cleanup()
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(_):
    global provider
    cleanup = asyncio.create_task(prune())
    try:
        yield
    finally:
        cleanup.cancel()
        if install_task and not install_task.done():
            install_task.cancel()
            await asyncio.gather(install_task, return_exceptions=True)
        provider = Provider()
        if login_process and login_process.returncode is None:
            login_process.terminate()
            await login_process.wait()
        tasks = [job.task for job in jobs.values() if job.task and not job.task.done()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(cleanup, *tasks, return_exceptions=True)
        for job in jobs.values():
            job.directory.cleanup()
        jobs.clear()


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.exception_handler(RequestValidationError)
async def validation_error(_request, _exception):
    return JSONResponse({"detail": "入力内容を確認してください。"}, status_code=422)


@app.middleware("http")
async def local_only(request, call_next):
    if request.headers.get("host") != HOST:
        return JSONResponse({"detail": "このMacから画面を開いてください。"}, status_code=403)
    if request.method not in {"GET", "HEAD"}:
        if (request.headers.get("origin") not in {None, ADDRESS}
                or request.headers.get("sec-fetch-site") == "cross-site"):
            return JSONResponse({"detail": "この画面から操作してください。"}, status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; frame-ancestors 'none'; object-src 'none'; base-uri 'none'"
    )
    return response


@app.get("/")
async def home():
    return FileResponse(RESOURCES / "index.html")


@app.get("/style.css")
async def styles():
    return FileResponse(RESOURCES / "style.css", media_type="text/css")


@app.get("/app.js")
async def javascript():
    return FileResponse(RESOURCES / "app.js", media_type="text/javascript")


@app.get("/api/health")
async def health():
    return {"app": "pdf-translation-room-mac"}


@app.get("/api/provider")
async def get_provider():
    return provider.public()


@app.post("/api/provider")
async def set_provider(form: ProviderInput):
    global provider
    if form.mode == "codex":
        provider = Provider()
        return provider.public()
    model = form.model.strip()
    key = form.api_key.get_secret_value().strip()
    base = form.base_url.strip().rstrip("/")
    try:
        url = urlsplit(base)
        loopback = url.hostname in {"localhost", "127.0.0.1", "::1"}
        valid = (url.hostname and url.username is None and url.password is None
                 and not url.query and not url.fragment
                 and (url.scheme == "https" or (url.scheme == "http" and loopback))
                 and all(char.isprintable() and not char.isspace() for char in base))
        _ = url.port
    except ValueError:
        valid = False
    if not valid:
        raise HTTPException(422, "APIのBase URLはHTTPSで入力してください。このMac内の接続先はHTTPも使えます。")
    valid_key = key and len(key) <= 4096 and all(33 <= ord(char) <= 126 for char in key)
    if not model or not model.isprintable() or not valid_key:
        raise HTTPException(422, "モデル名と有効な形式のAPIキーを入力してください。")
    if url.path.rstrip("/").endswith("/chat/completions"):
        raise HTTPException(422, "Base URLには末尾の /chat/completions を含めないでください。")
    provider = Provider("api", base, model, key)
    return provider.public()


async def install_codex():
    global install_failed
    folder = Path(os.environ.get("PDF_ROOM_DATA", Path.home() / "Library/Application Support/PDF翻訳室"))
    folder.mkdir(parents=True, exist_ok=True)
    process = None
    install_failed = False
    try:
        with (folder / "codex-install.log").open("wb") as log:
            process = await asyncio.create_subprocess_exec(
                str(RESOURCES / "install-codex"), str(folder),
                stdout=log, stderr=log, start_new_session=True,
            )
            install_failed = await process.wait() != 0
    except asyncio.CancelledError:
        if process:
            await stop(process)
        raise
    except OSError:
        install_failed = True


@app.post("/api/account/install", status_code=202)
async def begin_install():
    global install_task
    if not codex_command() and (not install_task or install_task.done()):
        install_task = asyncio.create_task(install_codex())
        return {"state": "installing"}
    return {"state": await account_state()}


@app.get("/api/account")
async def account():
    return {"state": await account_state()}


@app.post("/api/account/login", status_code=202)
async def login():
    global login_process, login_starting
    state = await account_state()
    if state in {"ready", "signing-in"} or login_starting or (login_process and login_process.returncode is None):
        return {"state": state if state == "ready" else "signing-in"}
    if state in {"missing", "unavailable", "installing", "install-failed"}:
        raise HTTPException(503, "Codexを起動できません。アプリを開き直してください。")
    login_starting = True
    try:
        login_process = await asyncio.create_subprocess_exec(
            codex_command(), "login", stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
    except OSError:
        raise HTTPException(503, "Codexを起動できません。アプリを開き直してください。") from None
    finally:
        login_starting = False
    return {"state": "signing-in"}


def stage_label(stage):
    if "Translate" in stage:
        return "Codexで本文を翻訳しています"
    if any(word in stage for word in ("Typesetting", "Font", "drawing", "Save")):
        return "日本語版と対訳版を作成しています"
    return "PDFのレイアウトを解析しています"


async def stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()


def diagnostic(log_path, mode):
    with log_path.open("rb") as log:
        log.seek(max(0, log_path.stat().st_size - 128 * 1024))
        message = log.read().lower()
    if mode == "api":
        if any(code in message for code in (b"compatible api http 401", b"compatible api http 403")):
            return "APIキーが受け付けられませんでした。翻訳設定を確認してください。"
        if b"compatible api http 429" in message:
            return "APIの利用上限に達しました。接続先の利用状況を確認してください。"
        if b"compatible api http 404" in message:
            return "APIのBase URLかモデルが見つかりません。翻訳設定を確認してください。"
        if b"compatible api" in message:
            return "互換APIで翻訳できませんでした。接続先、モデル、APIキーを確認してください。"
    if b"usage limit" in message or b"rate limit" in message:
        return "Codexの利用上限に達しました。利用状況を確認してから再試行してください。"
    if any(text in message for text in (b"authentication", b"codex login", b"unauthorized")):
        return "Codexに接続できません。ChatGPTアカウントでログインし直してください。"
    if b"timed out" in message:
        return "Codexからの応答が時間内にありませんでした。再試行してください。"
    return "翻訳を完了できませんでした。PDFを確認して再試行してください。"


async def run_job(job):
    log_path = job.folder / "worker.log"
    try:
        environment = dict(os.environ)
        selected = job.provider
        environment.update(PDF_ROOM_MODE=selected.mode)
        if selected.mode == "api":
            environment.update(
                PDF_ROOM_BASE_URL=selected.base_url, PDF_ROOM_MODEL=selected.model,
                PDF_ROOM_API_KEY=selected.api_key,
            )
        job.provider = Provider(mode=selected.mode)
        with log_path.open("wb") as log:
            job.process = process = await asyncio.create_subprocess_exec(
                *WORKER_COMMAND, str(job.folder / "source.pdf"), cwd=job.folder,
                stdout=asyncio.subprocess.PIPE, stderr=log, start_new_session=True, env=environment,
            )
            if job.state == "cancelling":
                await stop(process)
            async for line in process.stdout:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if "error" in event:
                    job.error = event["error"]
                if "progress" in event and job.state == "working":
                    job.progress = max(job.progress, min(99, round(event["progress"])))
                    job.stage = stage_label(event.get("stage", ""))
                    if selected.mode == "api":
                        job.stage = job.stage.replace("Codexで", "互換APIで")
                    total = event.get("total", 0)
                    job.count = f"{event['current']} / {total}" if total else ""
            code = await process.wait()
        if job.state == "cancelling":
            job.state, job.stage = "cancelled", "翻訳を中止しました"
        elif code == 0 and all((job.folder / f"source.ja.{mode}.pdf").is_file() for mode in FORMATS):
            job.state, job.progress, job.stage = "done", 100, "翻訳が完了しました"
        else:
            job.state, job.error = "failed", job.error or diagnostic(log_path, selected.mode)
    except asyncio.CancelledError:
        if job.process:
            await stop(job.process)
        raise
    except Exception:
        if job.process:
            await stop(job.process)
        job.state, job.error = "failed", "PDF翻訳を開始できませんでした。"
    finally:
        job.process = None
        job.ended = monotonic()
        (job.folder / "source.pdf").unlink(missing_ok=True)
        log_path.unlink(missing_ok=True)
        (job.folder / "translation.toml").unlink(missing_ok=True)
        if job.state != "done":
            for leftover in job.folder.glob("*.pdf"):
                leftover.unlink(missing_ok=True)


def find_job(identifier):
    if identifier not in jobs:
        raise HTTPException(404, "翻訳結果の保存期間が終了しました。")
    return jobs[identifier]


@app.post("/api/jobs", status_code=202)
async def create_job(request: Request):
    selected = provider
    if selected.mode == "codex" and await account_state() != "ready":
        raise HTTPException(401, "翻訳する前にChatGPTにログインしてください。")
    if any(job.state in {"uploading", "working", "cancelling"} for job in jobs.values()):
        raise HTTPException(409, "このMacで別のPDFを翻訳中です。完了してからお試しください。")
    length = request.headers.get("content-length")
    if length and length.isdecimal() and int(length) > MAX_BYTES:
        raise HTTPException(413, "PDFは50 MB以内にしてください。")
    name = unquote(request.headers.get("x-pdf-name", "document.pdf"))
    name = "".join(char for char in name.replace("\\", "/").rsplit("/", 1)[-1]
                   if char.isprintable()).strip()[:200] or "document.pdf"
    job = Job(secrets.token_urlsafe(18), name, TemporaryDirectory(prefix="pdf-room-"), selected)
    jobs[job.id] = job
    source = job.folder / "source.pdf"
    try:
        size = 0
        with source.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "PDFは50 MB以内にしてください。")
                output.write(chunk)
        with source.open("rb") as uploaded:
            if b"%PDF-" not in uploaded.read(1024):
                raise HTTPException(422, "有効なPDFを選んでください。")
        job.state = "working"
        job.task = asyncio.create_task(run_job(job))
    except BaseException:
        jobs.pop(job.id).directory.cleanup()
        raise
    return job.public()


@app.get("/api/jobs/{identifier}")
async def status(identifier: str):
    return find_job(identifier).public()


@app.delete("/api/jobs/{identifier}", status_code=202)
async def cancel(identifier: str):
    job = find_job(identifier)
    if job.state == "working":
        job.state, job.stage = "cancelling", "翻訳を中止しています"
        if job.process is None:
            await asyncio.sleep(0)
        if job.process and job.process.returncode is None:
            await stop(job.process)
    return job.public()


@app.get("/api/jobs/{identifier}/{mode}")
async def download(identifier: str, mode: str):
    job = find_job(identifier)
    if mode not in FORMATS or job.state != "done":
        raise HTTPException(404, "このPDFはまだダウンロードできません。")
    job.ended = monotonic()
    return FileResponse(
        job.folder / f"source.ja.{mode}.pdf", media_type="application/pdf",
        filename=f"{Path(job.name).stem} - {FORMATS[mode]}.pdf",
    )


def main():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind(("127.0.0.1", PORT))
        except OSError:
            with urlopen(f"{ADDRESS}/api/health", timeout=2) as response:
                if json.load(response).get("app") != "pdf-translation-room-mac":
                    raise RuntimeError("PDF翻訳室のポートは使用中です。")
            return
        listener.listen()
        uvicorn.Server(uvicorn.Config(app, log_level="warning")).run(sockets=[listener])


if __name__ == "__main__":
    main()
