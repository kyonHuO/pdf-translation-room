from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from subprocess import run
from tempfile import TemporaryDirectory

from tiktoken import encoding_for_model

from babeldoc.assets.assets import (
    generate_all_assets_file_list,
    get_cache_file_path,
    verify_file,
)
from babeldoc.assets.embedding_assets_metadata import (
    CMAP_URL_BY_UPSTREAM,
    DOC_LAYOUT_ONNX_MODEL_URL,
    FONT_URL_BY_UPSTREAM,
)


def download(job):
    category, entry = job
    name = entry["name"]
    expected_digest = entry["sha3_256"]
    destination = get_cache_file_path(name, category)
    if verify_file(destination, expected_digest):
        return True
    if category == "models":
        urls = [DOC_LAYOUT_ONNX_MODEL_URL[source] for source in ("huggingface", "modelscope")]
    else:
        upstream_urls = FONT_URL_BY_UPSTREAM if category == "fonts" else CMAP_URL_BY_UPSTREAM
        urls = [upstream_urls[source](name) for source in ("github", "huggingface", "modelscope")]
    with TemporaryDirectory(dir=destination.parent) as temp_dir:
        temp_path = Path(temp_dir) / name
        for url in urls:
            result = run([
                "/usr/bin/curl", "--proto", "=https", "--tlsv1.2", "-fsSL",
                "--max-time", "100", "--connect-timeout", "10",
                "--output", str(temp_path), url
            ], capture_output=True, text=True)
            if result.returncode == 0 and verify_file(temp_path, expected_digest):
                temp_path.replace(destination)
                print(f"Fetched {category}/{name}", flush=True)
                return True
            print(f"Retrying {category}/{name}: {result.stderr.strip() or 'checksum mismatch'}", flush=True)
    print(f"Failed {category}/{name}", flush=True)
    return False


assets = generate_all_assets_file_list()
groups = ("models", "fonts", "cmap")
jobs = [(group, entry) for group in groups for entry in assets[group]]
with ThreadPoolExecutor(max_workers=6) as pool:
    results = list(pool.map(download, jobs))
print(f"Verified {sum(results)}/{len(results)} assets")
if not all(results):
    raise SystemExit(1)
encoding_for_model("gpt-4o")
