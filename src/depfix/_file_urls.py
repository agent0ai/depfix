"""Cross-platform conversion of local file URLs into filesystem paths."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname


def file_url_to_path(url: str) -> Path:
    """Convert a ``file:`` URL while preserving Windows drives and UNC paths."""
    split = urlsplit(url)
    if split.scheme != "file":
        raise ValueError(f"expected a file URL, received {split.scheme or 'no'} scheme")
    pathname = split.path
    if split.netloc not in {"", "localhost"}:
        pathname = f"//{split.netloc}{pathname}"
    return Path(url2pathname(pathname))
