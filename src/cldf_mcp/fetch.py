"""Download pinned dataset files and verify their checksums."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .registry import DatasetSpec

USER_AGENT = "cldf-mcp (+https://pypi.org/project/cldf-mcp/)"


class ChecksumError(RuntimeError):
    pass


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path, timeout: float = 120.0) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=dest.parent, prefix=".part-")
    try:
        with os.fdopen(fd, "wb") as out, urllib.request.urlopen(req, timeout=timeout) as resp:
            shutil.copyfileobj(resp, out, length=1 << 20)
        os.replace(tmp, dest)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def dataset_dir(spec: DatasetSpec, raw_root: Path) -> Path:
    if spec.local_path:
        return Path(spec.local_path).expanduser()
    return raw_root / spec.id / spec.version


def ensure_files(spec: DatasetSpec, raw_root: Path, log: Callable[[str], None] = lambda _: None) -> Path:
    """Make sure every file of a dataset is present and matches its pinned checksum."""
    target = dataset_dir(spec, raw_root)
    for filename, expected in spec.files.items():
        path = target / filename
        if spec.local_path:
            if not path.exists():
                raise FileNotFoundError(f"{spec.id}: {path} not found")
        elif not (path.exists() and (expected is None or sha256_of(path) == expected)):
            url = spec.url_for(filename)
            log(f"  downloading {spec.id} {spec.version}: {filename}")
            _download(url, path)
        if expected is not None:
            actual = sha256_of(path)
            if actual != expected:
                if not spec.local_path:
                    path.unlink(missing_ok=True)
                raise ChecksumError(
                    f"{spec.id}/{filename}: checksum mismatch (expected {expected[:12]}…, got {actual[:12]}…). "
                    "The pinned release may have been altered upstream; refusing to load it."
                )
    return target
