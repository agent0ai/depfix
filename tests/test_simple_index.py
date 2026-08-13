from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet

import depfix.resolver as resolver_module
from depfix._file_urls import file_url_to_path
from depfix.cache import Cache
from depfix.config import ImportDeclaration, ProjectConfig
from depfix.errors import ResolutionError
from depfix.resolver import Resolver


class _Response(io.BytesIO):
    def __init__(self, body: str, content_type: str, url: str, *, length: int | None = None) -> None:
        super().__init__(body.encode())
        self.headers = {"Content-Type": content_type}
        if length is not None:
            self.headers["Content-Length"] = str(length)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


def _resolver(tmp_path: Path, index: str = "https://packages.example/simple") -> Resolver:
    return Resolver(Cache(tmp_path / "cache"), index_url=index)


@pytest.mark.parametrize(
    "content_type",
    ["application/vnd.pypi.simple.v1+html; charset=UTF-8", "Text/HTML; charset=utf-8"],
)
def test_simple_html_media_types_redirects_metadata_and_hash_filtering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    content_type: str,
) -> None:
    digest_a = "a" * 64
    digest_b = "b" * 64
    digest_c = "c" * 64
    page = f"""
        <html><body>
        <a href="../files/torch-2.7.0%2Bcpu-py3-none-any.whl#sha256={digest_a}"
           data-requires-python="&gt;=3.11" data-yanked>torch</a>
        <a href="https://cdn.example/torch-2.8.0%2Bcpu-py3-none-any.whl#sha256={digest_b}"
           data-yanked="bad build">torch</a>
        <a href="torch-2.9.0%2Bcpu-py3-none-any.whl#sha256={digest_c}">torch</a>
        <a href="torch-3.0.0-py3-none-any.whl">hashless</a>
        <a href="not-a-package">malformed</a>
        </body></html>
    """
    requests: list[tuple[str, str, str | None]] = []

    def open_url(request: urllib.request.Request, **_kwargs: object) -> _Response:
        requests.append((request.full_url, request.get_method(), request.get_header("Accept")))
        if request.get_method() == "HEAD":
            return _Response("", "application/octet-stream", request.full_url, length=1234)
        return _Response(page, content_type, "https://mirror.example/redirected/torch/")

    monkeypatch.setattr(resolver_module, "_open_url", open_url)
    resolver = _resolver(tmp_path)
    payload = resolver._project_artifact_payload("torch", SpecifierSet())

    releases = payload["releases"]
    first, second, third = releases["2.7.0+cpu"][0], releases["2.8.0+cpu"][0], releases["2.9.0+cpu"][0]
    assert first["url"] == "https://mirror.example/redirected/files/torch-2.7.0%2Bcpu-py3-none-any.whl"
    assert first["filename"] == "torch-2.7.0+cpu-py3-none-any.whl"
    assert first["requires_python"] == ">=3.11"
    assert first["yanked"] is True and first["yanked_reason"] == ""
    assert second["url"].startswith("https://cdn.example/")
    assert second["yanked"] is True and second["yanked_reason"] == "bad build"
    assert third["yanked"] is False and third["digests"] == {"sha256": digest_c}
    assert "3.0.0" in releases and releases["3.0.0"][0]["digests"] == {}
    candidate = resolver._select_pypi("torch", SpecifierSet(), prefer_newest=True)
    assert candidate.version == "2.9.0+cpu" and candidate.size == 1234
    assert set(item[1] for item in requests) == {"GET", "HEAD"}
    assert sum(item[1] == "HEAD" for item in requests) == 1
    assert all("/torch/json" not in item[0] for item in requests)


def test_pep691_json_is_preferred_and_uses_final_response_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    digest = "d" * 64
    body = json.dumps(
        {
            "files": [
                {
                    "filename": "demo-1.0.0-py3-none-any.whl",
                    "url": "../files/demo.whl",
                    "hashes": {"sha256": digest},
                    "size": 42,
                }
            ]
        }
    )
    requests: list[urllib.request.Request] = []

    def open_url(request: urllib.request.Request, **_kwargs: object) -> _Response:
        requests.append(request)
        return _Response(body, "Application/Vnd.Pypi.Simple.V1+Json; charset=utf-8", "https://mirror.example/demo/")

    monkeypatch.setattr(resolver_module, "_open_url", open_url)
    payload = _resolver(tmp_path)._project_artifact_payload("demo", SpecifierSet())

    assert payload["releases"]["1.0.0"][0]["url"] == "https://mirror.example/files/demo.whl"
    assert len(requests) == 1
    accept = requests[0].get_header("Accept") or ""
    assert accept.startswith("application/vnd.pypi.simple.v1+json")
    assert "application/vnd.pypi.simple.v1+html" in accept and "text/html" in accept


def test_grouped_pytorch_projects_remain_on_selected_index_without_json_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    wheel_factory,
) -> None:
    torch_wheel = wheel_factory("torch", "1.0.0", {"torch.py": "VERSION = 'test'\n"})
    vision_wheel = wheel_factory("torchvision", "1.0.0", {"torchvision.py": "VERSION = 'test'\n"})
    wheels = {"torch": torch_wheel, "torchvision": vision_wheel}
    requested: list[str] = []

    def open_url(request: urllib.request.Request, **_kwargs: object) -> _Response:
        requested.append(request.full_url)
        if request.get_method() == "HEAD":
            path = file_url_to_path(request.full_url)
            return _Response("", "application/octet-stream", request.full_url, length=path.stat().st_size)
        project = request.full_url.rstrip("/").rsplit("/", 1)[-1]
        wheel = wheels[project]
        digest = __import__("hashlib").sha256(wheel.read_bytes()).hexdigest()
        return _Response(
            f'<a href="{wheel.as_uri()}#sha256={digest}">{wheel.name}</a>',
            "text/html",
            request.full_url,
        )

    monkeypatch.setattr(resolver_module, "_open_url", open_url)
    index = "https://download.pytorch.org/whl/cpu"

    class Backend:
        def version(self) -> str:
            return "test"

        def resolve_root_version(self, requirement: str, distribution: str) -> str:
            assert requirement == distribution
            return "1.0.0"

    graph = Resolver(Cache(tmp_path / "cache"), index_url=index, backend=Backend()).resolve(
        ProjectConfig(
            tmp_path / "grouped.toml",
            (
                ImportDeclaration("torch", "torch", api="load_package"),
                ImportDeclaration("torchvision", "torchvision", api="load_package"),
            ),
            {"prefer-newest": True},
        )
    )

    assert {node.distribution for node in graph.nodes} == {"torch", "torchvision"}
    assert any(url.endswith("/cpu/torch/") for url in requested)
    assert any(url.endswith("/cpu/torchvision/") for url in requested)
    assert all("pypi.org" not in url and "/json" not in url for url in requested)


def test_unknown_simple_media_type_is_reported_without_parsing_as_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def open_url(request: urllib.request.Request, **_kwargs: object) -> _Response:
        requested.append(request.full_url)
        if request.full_url.endswith("/json"):
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, None)
        return _Response('{"files": []}', "application/octet-stream", request.full_url)

    monkeypatch.setattr(resolver_module, "_open_url", open_url)
    with pytest.raises(ResolutionError, match="unsupported Simple API media type"):
        _resolver(tmp_path)._project_artifact_payload("demo", SpecifierSet())

    assert requested == [
        "https://packages.example/simple/demo/",
        "https://packages.example/simple/demo/json",
    ]
