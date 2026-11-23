"""Use pdf2zh's progress stream in a worker that can be cancelled as a group."""

import asyncio
from contextlib import aclosing
import json
import logging
import os
from pathlib import Path
import shlex
import sys

import pymupdf
from pdf2zh_next.config import ConfigManager
from pdf2zh_next.high_level import do_translate_async_stream


def emit(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def translate(source):
    try:
        with pymupdf.open(source) as document:
            if document.needs_pass:
                emit(error="パスワードを解除してからPDFを選び直してください。")
                return False
            if not any(page.get_text().strip() for page in document):
                emit(error="選べる文字がありません。OCRで文字認識したPDFを選び直してください。")
                return False
    except (RuntimeError, ValueError):
        emit(error="PDFを読み込めませんでした。ファイルを選び直してください。")
        return False

    config = source.with_name("translation.toml")
    bridge = Path(__file__).with_name("codex_translate.py")
    args = [sys.executable, "-B", str(bridge)]
    if os.environ.get("PDF_ROOM_MODE") == "api":
        args = [
            sys.executable, "-B", str(bridge.with_name("api_translate.py")),
            os.environ["PDF_ROOM_BASE_URL"], os.environ["PDF_ROOM_MODEL"],
        ]
    command = shlex.join(args)
    config.write_text(
        "clitranslator = true\n[translation]\nlang_in = \"en\"\nlang_out = \"ja\"\n"
        "qps = 1\npool_max_workers = 2\nno_auto_extract_glossary = true\n"
        "[clitranslator_detail]\nclitranslator_timeout = 125\n"
        f"clitranslator_command = {json.dumps(command, ensure_ascii=False)}\n",
        encoding="utf-8",
    )
    sys.argv = [sys.argv[0], "--config-file", str(config), "--output", str(source.parent),
                "--skip-scanned-detection", "--lang-in", "en", "--lang-out", "ja",
                "--clitranslator"]
    settings = ConfigManager().initialize_config()
    settings.pdf.no_mono = False
    settings.pdf.no_dual = False
    settings.pdf.use_alternating_pages_dual = False
    async with aclosing(do_translate_async_stream(settings, source)) as events:
        async for event in events:
            if event["type"].startswith("progress_"):
                emit(progress=event.get("overall_progress", 0), stage=event.get("stage", ""),
                     current=event.get("stage_current", 0), total=event.get("stage_total", 0))
            elif event["type"] == "finish":
                return True
    return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    try:
        succeeded = asyncio.run(translate(Path(sys.argv[1])))
    except Exception:
        logging.exception("PDF translation failed")
        succeeded = False
    sys.exit(0 if succeeded else 1)
