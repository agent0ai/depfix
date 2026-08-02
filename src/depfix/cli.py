"""Depfix command-line interface; primary operations delegate to Python APIs."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import runpy
import shlex
import shutil
import sys
import sysconfig
import tomllib
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from packaging.utils import canonicalize_name

from . import __version__, configure, load_package
from .aliases import generate_aliases
from .cache import Cache
from .errors import DepfixError
from .manifest import load_manifest
from .project import create_bundle, export_project, install_manifest, verify_manifest
from .scanner import scan_project
from .settings import resolve_settings
from .uv_backend import UvBackend


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="depfix",
        description="Run multiple Python package versions together without dependency conflicts",
    )
    parser.add_argument("--version", action="version", version=f"depfix {__version__}")
    parser.add_argument("--cache-dir", type=Path, help="override the global Depfix cache")
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)

    export = commands.add_parser("export", help="scan, resolve, and write a deployment manifest")
    export.add_argument("project", nargs="?", default=".", type=Path)
    export.add_argument("--output", "-o", type=Path, default=Path(".depfix/imports.lock"))
    export.add_argument("--include", action="append", default=[])
    export.add_argument("--exclude", action="append", default=[])
    export.add_argument("--requirements", action="append", type=Path, default=[])
    export.add_argument("--index-url")
    export.add_argument("--extra-index-url", action="append", default=[])
    export.add_argument("--refresh", action="store_true")

    install = commands.add_parser("install", help="materialize an exact manifest or bundle")
    install.add_argument("source", type=Path)
    _install_options(install)

    bundle = commands.add_parser("bundle", help="create a deterministic air-gap bundle")
    bundle.add_argument("manifest", type=Path)
    bundle.add_argument("--output", "-o", type=Path, required=True)
    bundle.add_argument("--include-depfix-runtime", action="store_true")

    prepare = commands.add_parser("prepare", help="export, install, verify, and generate IDE aliases")
    prepare.add_argument("project", nargs="?", default=".", type=Path)
    prepare.add_argument("--output", "-o", type=Path, default=Path(".depfix/imports.lock"))

    scan = commands.add_parser("scan", help="report static and dynamic Depfix calls without executing code")
    scan.add_argument("project", nargs="?", default=".", type=Path)
    scan.add_argument("--include", action="append", default=[])
    scan.add_argument("--exclude", action="append", default=[])

    fetch = commands.add_parser("fetch", help="resolve and pre-cache an ad hoc package request")
    fetch.add_argument("specifier")
    fetch.add_argument("--refresh", action="store_true")

    run = commands.add_parser("run", help="activate prepared state and run a script or module")
    run.add_argument("-m", "--module")
    run.add_argument("script", nargs="?", type=Path)
    run.add_argument("--manifest", type=Path)
    run.add_argument("--bundle", type=Path)
    run.add_argument("--frozen", action="store_true")
    run.add_argument("--offline", action="store_true")
    run.add_argument("args", nargs=argparse.REMAINDER)

    verify = commands.add_parser("verify", help="verify manifest/bundle identity and cached artifacts")
    verify.add_argument("source", type=Path)

    check = commands.add_parser("check", help="verify source declarations against a manifest")
    check.add_argument("project", nargs="?", default=".", type=Path)
    check.add_argument("--manifest", type=Path, default=Path(".depfix/imports.lock"))
    check.add_argument("--offline", action="store_true")

    tree = commands.add_parser("tree", help="display exact nodes and dependency edges")
    tree.add_argument("manifest", nargs="?", type=Path, default=Path(".depfix/imports.lock"))
    show = commands.add_parser("show", help="resolve and display one package request")
    show.add_argument("specifier")
    why = commands.add_parser("why", help="explain why a distribution is present")
    why.add_argument("package")
    why.add_argument("--manifest", type=Path, default=Path(".depfix/imports.lock"))
    listing = commands.add_parser("list", help="list requests or cached live resolutions")
    listing.add_argument("manifest", nargs="?", type=Path)
    commands.add_parser("doctor", help="diagnose backend, cache, manifest, and native policy")

    migrate = commands.add_parser("migrate", help="import roots from requirements or pyproject metadata")
    migrate.add_argument("file", type=Path)
    migrate.add_argument("--output", "-o", type=Path, default=Path(".depfix/config.toml"))

    requirements = commands.add_parser("requirements", help="per-realm requirements operations")
    req_commands = requirements.add_subparsers(dest="requirements_command", required=True)
    req_export = req_commands.add_parser("export")
    req_export.add_argument("manifest", type=Path)
    req_export.add_argument("--realm", required=True)
    req_export.add_argument("--output", "-o", required=True, type=Path)

    ide = commands.add_parser("ide", help="manage generated static-analysis aliases")
    ide_commands = ide.add_subparsers(dest="ide_command", required=True)
    for name in ("sync", "path", "attach", "configure", "status", "clean"):
        command = ide_commands.add_parser(name)
        if name not in {"status", "clean"}:
            command.add_argument("manifest", nargs="?", type=Path, default=Path(".depfix/imports.lock"))
        if name == "sync":
            command.add_argument("--local", action="store_true")
    ide_commands.add_parser("detach")

    pip = commands.add_parser("pip", help="delegate a conventional environment operation to uv pip")
    pip.add_argument("--version", action="store_true", dest="pip_version")
    pip.add_argument("arguments", nargs=argparse.REMAINDER)

    cache = commands.add_parser("cache", help="inspect or maintain the global cache")
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    for name in ("dir", "list", "prune", "clean", "verify"):
        cache_commands.add_parser(name)
    cleanup = cache_commands.add_parser("cleanup", help="remove packages unused beyond a retention window")
    cleanup.add_argument("--days", type=int, help="unused days; defaults to configured cache retention")
    cleanup.add_argument("--dry-run", action="store_true")
    remove = cache_commands.add_parser("remove", help="remove one cached distribution selection")
    remove.add_argument("package")
    remove.add_argument("--version")
    remove.add_argument("--artifact")
    remove.add_argument("--dry-run", action="store_true")
    for command in commands.choices.values():
        _common_output_options(command)
    return parser


def _common_output_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--cache-dir", type=Path, default=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--quiet", action="store_true", default=argparse.SUPPRESS)
    parser.add_argument("--verbose", action="store_true", default=argparse.SUPPRESS)


def _install_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--frozen", action="store_true")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--cached-only", action="store_true")
    parser.add_argument("--target", type=Path)
    parser.add_argument("--local", action="store_true")
    parser.add_argument("--python")
    parser.add_argument("--platform")
    parser.add_argument("--architecture")
    parser.add_argument(
        "--no-build",
        action="store_true",
        help="require artifact-only installation (install never builds or resolves)",
    )
    parser.add_argument("--compile-bytecode", action="store_true")


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.cache_dir is not None:
        configure(cache_dir=args.cache_dir)
    if args.quiet or args.json:
        configure(log_level="WARNING")
    elif args.verbose:
        configure(log_level="DEBUG")
    try:
        result = _dispatch(args)
        if isinstance(result, int):
            return result
        if result is not None and not args.quiet:
            _print_result(result, as_json=args.json)
        return 0
    except (DepfixError, OSError, ValueError, KeyError) as exc:
        if args.verbose:
            traceback.print_exc()
            return 2
        if args.json:
            print(json.dumps({"ok": False, "error": type(exc).__name__, "message": str(exc)}, sort_keys=True))
        else:
            print(f"depfix: {exc}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> object | None:
    if args.command == "export":
        includes = list(args.include)
        for requirements in args.requirements:
            includes.extend(_requirements_lines(requirements))
        return export_project(
            args.project,
            output=args.output,
            include=includes,
            exclude=args.exclude,
            index_url=args.index_url,
            extra_index_url=args.extra_index_url,
            refresh=args.refresh,
        )
    if args.command == "install":
        _validate_target_options(args)
        return install_manifest(
            args.source,
            frozen=args.frozen,
            offline=args.offline,
            cached_only=args.cached_only,
            local=args.local,
            target=args.target,
            compile_bytecode=args.compile_bytecode,
            cache_dir=args.cache_dir,
        )
    if args.command == "bundle":
        return create_bundle(
            args.manifest, args.output, include_depfix_runtime=args.include_depfix_runtime, cache_dir=args.cache_dir
        )
    if args.command == "prepare":
        exported = export_project(args.project, output=args.output)
        installed = install_manifest(exported.manifest, frozen=True)
        verified = verify_manifest(exported.manifest)
        return {"export": exported, "install": installed, "verify": verified, "ide_path": str(exported.ide_path)}
    if args.command == "scan":
        return scan_project(args.project, include=args.include, exclude=args.exclude)
    if args.command == "fetch":
        package = load_package(args.specifier, refresh=args.refresh)
        return {
            "name": package.name,
            "version": package.version,
            "modules": package.module_names,
            "artifact": package.artifact_hash,
        }
    if args.command == "run":
        manifest = args.manifest
        if args.bundle is not None:
            manifest = install_manifest(args.bundle, frozen=True, offline=True, cache_dir=args.cache_dir).manifest
        if manifest is not None:
            install_manifest(manifest, frozen=args.frozen, offline=args.offline, cache_dir=args.cache_dir)
            configure(manifest=manifest, frozen=args.frozen, offline=args.offline)
        else:
            configure(frozen=args.frozen, offline=args.offline)
        if args.module:
            module_args = ([str(args.script)] if args.script is not None else []) + list(args.args)
            if module_args[:1] == ["--"]:
                module_args.pop(0)
            sys.argv = [args.module, *module_args]
            runpy.run_module(args.module, run_name="__main__", alter_sys=True)
        else:
            if args.script is None:
                raise ValueError("run requires a script path or -m/--module")
            sys.argv = [str(args.script), *args.args]
            script = args.script.resolve()
            sys.path.insert(0, str(script.parent))
            try:
                runpy.run_path(str(script), run_name="__main__")
            finally:
                if sys.path and sys.path[0] == str(script.parent):
                    sys.path.pop(0)
        return None
    if args.command == "verify":
        return verify_manifest(args.source, cache_dir=args.cache_dir)
    if args.command == "check":
        return _check(args.project, args.manifest, args.offline)
    if args.command == "tree":
        return _tree(load_manifest(args.manifest.resolve()))
    if args.command == "show":
        package = load_package(args.specifier)
        return _package_dict(package)
    if args.command == "why":
        return _why(load_manifest(args.manifest.resolve()), args.package)
    if args.command == "list":
        return _list(args.manifest)
    if args.command == "doctor":
        return _doctor(args.cache_dir)
    if args.command == "migrate":
        return _migrate(args.file, args.output)
    if args.command == "requirements":
        return _requirements_export(args.manifest, args.realm, args.output)
    if args.command == "ide":
        return _ide(args)
    if args.command == "pip":
        settings = resolve_settings(cache_dir=args.cache_dir, discover=False)
        backend = UvBackend(settings, Cache(settings.cache_dir))
        if args.pip_version:
            executable = backend.ensure_available()
            print(f"uv {executable.version}")
            return 0
        return backend.passthrough(args.arguments)
    if args.command == "cache":
        return _cache(args)
    raise ValueError(f"unsupported command {args.command}")


def _check(project: Path, manifest: Path, offline: bool) -> dict[str, object]:
    root = project.resolve()
    path = manifest if manifest.is_absolute() else root / manifest
    graph = load_manifest(path)
    scan = scan_project(root)
    declared = {
        (request.normalized_specifier, request.api, request.explicit_module and request.module or None)
        for request in graph.aliases
    }
    actual = {
        (request.normalized_specifier, request.api, request.module) for request in scan.requests if not request.group_id
    }
    missing = sorted(actual - declared)
    if missing:
        raise ValueError(f"manifest is missing or differs from {len(missing)} static requests: {missing}")
    declared_groups = {(group.mode, tuple(sorted(group.normalized_specifiers))) for group in graph.groups}
    actual_groups = {(group.mode, tuple(sorted(group.normalized_specifiers))) for group in scan.groups}
    missing_groups = sorted(actual_groups - declared_groups)
    if missing_groups:
        raise ValueError(
            f"manifest is missing or differs from {len(missing_groups)} standard-import groups: {missing_groups}"
        )
    has_dynamic_includes = any(
        request.source_file in {"<explicit>", ".depfix/config.toml"} for request in graph.aliases
    )
    if graph.dynamic_diagnostics and not has_dynamic_includes:
        raise ValueError("manifest records unresolved dynamic requests: " + "; ".join(graph.dynamic_diagnostics))
    if offline:
        verify_manifest(path)
    return {"ok": True, "manifest_id": graph.graph_id, "requests": len(actual), "groups": len(actual_groups)}


def _tree(graph: Any) -> dict[str, object]:
    nodes = []
    for node in graph.nodes:
        nodes.append(
            {
                "id": node.id,
                "distribution": node.distribution,
                "version": node.version,
                "modules": node.public_modules,
                "native": node.native_classification,
                "dependencies": dict(node.dependencies),
            }
        )
    return {"manifest_id": graph.graph_id, "nodes": nodes}


def _why(graph: Any, package: str) -> list[dict[str, object]]:
    target = str(canonicalize_name(package))
    result = []
    for node in graph.nodes:
        if node.distribution != target:
            continue
        parents = [parent.id for parent in graph.nodes if node.id in parent.dependencies.values()]
        requests = [request.name for request in graph.aliases if request.node == node.id]
        result.append({"node": node.id, "version": node.version, "parents": parents, "requests": requests})
    if not result:
        raise ValueError(f"{target} is not present in the manifest")
    return result


def _list(manifest: Path | None) -> object:
    settings = resolve_settings(discover=False)
    cache = Cache(settings.cache_dir)
    if manifest is not None:
        graph = load_manifest(manifest.resolve())
        return [
            {
                "alias": item.name,
                "specifier": item.specifier,
                "normalized": item.normalized_specifier,
                "api": item.api,
                "module": item.module,
            }
            for item in graph.aliases
        ]
    root = cache.root / "resolutions"
    return sorted(str(path) for path in root.glob("*/imports.lock")) if root.exists() else []


def _doctor(cache_dir: Path | None) -> dict[str, object]:
    settings = resolve_settings(cache_dir=cache_dir, discover=True)
    cache = Cache(settings.cache_dir)
    uv = UvBackend(settings, cache).ensure_available()
    manifest = str(settings.manifest) if settings.manifest else None
    manifest_status: object = None
    if settings.manifest:
        graph = load_manifest(settings.manifest)
        native = [
            f"{node.distribution}=={node.version}:{node.native_classification}"
            for node in graph.nodes
            if node.native_classification != "pure-python"
        ]
        manifest_status = {"id": graph.graph_id, "native_risks": native}
    return {
        "ok": True,
        "uv": str(uv.version),
        "uv_path": str(uv.path),
        "cache": str(cache.root),
        "manifest": manifest,
        "manifest_status": manifest_status,
    }


def _migrate(source: Path, output: Path) -> dict[str, object]:
    requirements: list[str]
    if source.name == "pyproject.toml":
        with source.open("rb") as handle:
            raw = tomllib.load(handle)
        requirements = list(raw.get("project", {}).get("dependencies", []))
    else:
        requirements = _requirements_lines(source)
    destination = output.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    rows = ["# Generated by depfix migrate; review module ambiguity before export.\n"]
    for requirement in requirements:
        rows.extend(["[[dynamic]]\n", f"specifier = {json.dumps(requirement)}\n\n"])
    destination.write_text("".join(rows), encoding="utf-8")
    return {"output": str(destination), "requirements": len(requirements)}


def _requirements_lines(path: Path, _seen: set[Path] | None = None) -> list[str]:
    source = path.expanduser().resolve()
    seen = _seen if _seen is not None else set()
    if source in seen:
        raise ValueError(f"recursive requirements include detected at {source}")
    seen.add(source)
    physical_lines = source.read_text(encoding="utf-8").splitlines()
    logical_lines: list[str] = []
    pending = ""
    for line in physical_lines:
        value = line.strip()
        pending += value[:-1].rstrip() + " " if value.endswith("\\") else value
        if not value.endswith("\\"):
            logical_lines.append(pending.strip())
            pending = ""
    if pending:
        logical_lines.append(pending.strip())
    result: list[str] = []
    for value in logical_lines:
        if not value or value.startswith("#"):
            continue
        tokens = shlex.split(value, comments=True)
        if not tokens:
            continue
        if tokens[0] in {"-r", "--requirement"} and len(tokens) == 2:
            included = Path(tokens[1])
            if not included.is_absolute():
                included = source.parent / included
            result.extend(_requirements_lines(included, seen))
            continue
        if tokens[0] in {"-c", "--constraint", "--index-url", "--extra-index-url"}:
            continue
        if tokens[0] in {"-e", "--editable"} and len(tokens) == 2:
            editable = Path(tokens[1])
            if not editable.is_absolute():
                editable = source.parent / editable
            result.append(f"file:{editable.resolve()}")
            continue
        cleaned = value.split(" #", 1)[0]
        cleaned = re.sub(r"\s+--hash(?:=|\s+)\S+", "", cleaned).strip()
        remaining_tokens = shlex.split(cleaned, comments=True)
        unsupported = [token for token in remaining_tokens if token.startswith("--")]
        if unsupported:
            raise ValueError(f"unsupported requirements option {unsupported[0]!r} in {source}")
        if cleaned:
            result.append(cleaned)
    seen.remove(source)
    return result


def _requirements_export(manifest: Path, realm: str, output: Path) -> dict[str, object]:
    graph = load_manifest(manifest.resolve())
    nodes = graph.node_index
    if realm not in nodes:
        raise ValueError(f"unknown realm {realm!r}")
    seen: set[str] = set()
    lines: list[str] = []

    def walk(node_id: str) -> None:
        if node_id in seen:
            return
        seen.add(node_id)
        node = nodes[node_id]
        artifact = graph.artifact_index[node.artifact]
        lines.append(f"{node.distribution}=={node.version} --hash=sha256:{artifact.sha256}\n")
        for child in sorted(node.dependencies.values()):
            walk(child)

    walk(realm)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("".join(lines), encoding="utf-8")
    return {"output": str(output.resolve()), "realm": realm, "requirements": len(lines)}


def _ide(args: argparse.Namespace) -> object:
    settings = resolve_settings(cache_dir=args.cache_dir, discover=False)
    cache = Cache(settings.cache_dir)
    record = cache.root / "ide" / "attachment.json"
    if args.ide_command in {"sync", "path", "attach", "configure"}:
        graph = load_manifest(args.manifest.resolve())
        output = (
            args.manifest.resolve().parent / "ide"
            if getattr(args, "local", False)
            else cache.root / "ide" / graph.graph_id.removeprefix("sha256:")
        )
        if args.ide_command == "sync":
            install_manifest(args.manifest, frozen=True, cache_dir=args.cache_dir)
            path = generate_aliases(graph, cache, output)
            return {"path": str(path.parent), "local": getattr(args, "local", False)}
        if args.ide_command == "path":
            return {"path": str(output)}
        if args.ide_command == "configure":
            overlay = output / "default_imports"
            paths = [str(overlay), str(output)] if overlay.is_dir() else [str(output)]
            return {
                "extraPaths": paths,
                "mypy_path": os.pathsep.join(paths),
                "pycharm_source_root": str(output),
                "default_overlay": str(overlay) if overlay.is_dir() else None,
            }
        if args.ide_command == "attach":
            if sys.prefix == sys.base_prefix:
                raise ValueError("ide attach requires an explicitly active virtual environment")
            generate_aliases(graph, cache, output)
            site_packages = Path(sysconfig.get_path("purelib"))
            pth = site_packages / f"depfix-{graph.graph_id.removeprefix('sha256:')[:16]}.pth"
            pth.write_text(str(output.resolve()) + "\n", encoding="utf-8")
            record.parent.mkdir(parents=True, exist_ok=True)
            record.write_text(
                json.dumps({"pth": str(pth), "path": str(output.resolve())}, sort_keys=True) + "\n", encoding="utf-8"
            )
            return {"attached": str(pth), "path": str(output)}
    if args.ide_command == "detach":
        if record.is_file():
            data = json.loads(record.read_text(encoding="utf-8"))
            Path(data["pth"]).unlink(missing_ok=True)
            record.unlink()
            return {"detached": data["pth"]}
        return {"detached": None}
    if args.ide_command == "status":
        return json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {"attached": False}
    if args.ide_command == "clean":
        ide_root = cache.root / "ide"
        if ide_root.exists():
            shutil.rmtree(ide_root)
        return {"cleaned": str(ide_root)}
    raise ValueError(args.ide_command)


def _cache(args: argparse.Namespace) -> object:
    settings = resolve_settings(cache_dir=args.cache_dir, discover=True)
    cache = Cache(settings.cache_dir)
    command = args.cache_command
    if command == "dir":
        return {"path": str(cache.root)}
    if command == "list":
        return list(cache.list_packages())
    if command == "verify":
        for path in cache.list_blobs():
            cache.verify_blob(path.name)
        return {"verified": len(cache.list_blobs())}
    if command == "prune":
        referenced: set[str] = set()
        for manifest in (cache.root / "manifests").glob("*/imports.lock"):
            referenced.update(artifact.sha256 for artifact in load_manifest(manifest).artifacts)
        removed = cache.prune(referenced)
        return {"removed": len(removed)}
    if command == "clean":
        if cache.root.exists():
            shutil.rmtree(cache.root)
        return {"cleaned": str(cache.root)}
    if command == "cleanup":
        days = settings.cache_retention_days if args.days is None else args.days
        return cache.cleanup(days, dry_run=args.dry_run)
    if command == "remove":
        return cache.remove_package(
            args.package,
            version=args.version,
            artifact_hash=args.artifact,
            dry_run=args.dry_run,
        )
    raise ValueError(command)


def _validate_target_options(args: argparse.Namespace) -> None:
    if args.python and Path(args.python).resolve() != Path(sys.executable).resolve():
        raise ValueError("cross-interpreter install is not implemented; run Depfix with the target interpreter")
    if args.platform and args.platform != sys.platform:
        raise ValueError("the manifest currently contains only the host platform target")
    if args.architecture and args.architecture.lower() != platform.machine().lower():
        raise ValueError("the manifest currently contains only the host architecture target")
    if args.target is not None and not args.local:
        raise ValueError("--target requires --local so the selected artifacts are copied there")


def _package_dict(package: Any) -> dict[str, object]:
    return {
        "name": package.name,
        "version": package.version,
        "modules": package.module_names,
        "artifact": package.artifact_hash,
        "realm": package.realm_id,
        "native": package.metadata.native_classification,
    }


def _print_result(value: object, *, as_json: bool) -> None:
    serialized = _serialize(value)
    if as_json:
        print(json.dumps(serialized, sort_keys=True))
    elif isinstance(serialized, (dict, list)):
        print(json.dumps(serialized, indent=2, sort_keys=True))
    else:
        print(serialized)


def _serialize(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {name: _serialize(getattr(value, name)) for name in value.__dataclass_fields__}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    return value


if __name__ == "__main__":
    raise SystemExit(main())
