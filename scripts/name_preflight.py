#!/usr/bin/env python3
"""Check PyPI collision candidates without reserving or uploading a package."""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request

VARIANTS = ("depfix", "dep-fix", "dep_fix", "dep.fix")
ENDPOINTS = ("depfix", "dep-fix")


def check(name: str) -> dict[str, object]:
    url = f"https://pypi.org/pypi/{name}/json"
    request = urllib.request.Request(url, headers={"User-Agent": "depfix-name-preflight/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
        return {
            "name": name,
            "url": url,
            "exists": True,
            "project": payload.get("info", {}).get("name"),
            "version": payload.get("info", {}).get("version"),
        }
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return {"name": name, "url": url, "exists": False}
        return {"name": name, "url": url, "error": f"HTTP {exc.code}"}
    except (OSError, ValueError) as exc:
        return {"name": name, "url": url, "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    results = [check(name) for name in ENDPOINTS]
    report = {
        "collision_variants": VARIANTS,
        "normalized_endpoints_checked": ENDPOINTS,
        "results": results,
        "warning": (
            "A missing project page is not a reservation guarantee; only the first accepted upload is definitive. "
            "Do not upload an empty placeholder package."
        ),
    }
    if arguments.json:
        print(json.dumps(report, sort_keys=True))
    else:
        print("Depfix PyPI name preflight")
        print("Collision candidates:", ", ".join(VARIANTS))
        for item in results:
            if item.get("error"):
                print(f"- {item['name']}: unable to verify ({item['error']})")
            elif item["exists"]:
                print(f"- {item['name']}: existing project {item.get('project')} {item.get('version')}")
            else:
                print(f"- {item['name']}: no current project JSON endpoint")
        print(report["warning"])
    if any(item.get("error") for item in results):
        return 2
    return 1 if any(item.get("exists") for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
