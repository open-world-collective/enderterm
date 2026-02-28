#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _api_request(url: str, *, token: str, method: str = "GET", body: dict | None = None) -> object:
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "enderterm-label-sync",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: {e.code} {e.reason}\n{msg}") from e


def _get_repo_from_env() -> str | None:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo and "/" in repo:
        return repo
    return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Sync GitHub labels from scripts/github_labels.json")
    p.add_argument(
        "--repo",
        default=_get_repo_from_env() or "qarl/enderterm",
        help="GitHub repo as owner/name (default: %(default)s)",
    )
    p.add_argument(
        "--labels",
        type=Path,
        default=Path(__file__).resolve().parent / "github_labels.json",
        help="Path to labels JSON (default: %(default)s)",
    )
    args = p.parse_args(argv)

    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_API_KEY")
    if not token:
        print("Missing token: set $GITHUB_TOKEN (or $GH_TOKEN / $GITHUB_API_KEY).", file=sys.stderr)
        return 2

    labels_spec = json.loads(args.labels.read_text("utf-8"))
    if not isinstance(labels_spec, list):
        raise SystemExit("labels file must contain a JSON list")

    owner, name = str(args.repo).split("/", 1)
    base = f"https://api.github.com/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}/labels"

    existing: dict[str, dict] = {}
    page = 1
    while True:
        url = f"{base}?per_page=100&page={page}"
        batch = _api_request(url, token=token, method="GET")
        if not isinstance(batch, list) or not batch:
            break
        for item in batch:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                existing[item["name"]] = item
        page += 1

    created = 0
    updated = 0
    for spec in labels_spec:
        if not isinstance(spec, dict) or not isinstance(spec.get("name"), str):
            raise SystemExit(f"Invalid label spec: {spec!r}")
        label_name = str(spec["name"])
        color = str(spec.get("color", "")).lstrip("#")
        desc = str(spec.get("description", "") or "")

        if label_name in existing:
            _api_request(
                f"{base}/{urllib.parse.quote(label_name)}",
                token=token,
                method="PATCH",
                body={"new_name": label_name, "color": color, "description": desc},
            )
            updated += 1
        else:
            _api_request(
                base,
                token=token,
                method="POST",
                body={"name": label_name, "color": color, "description": desc},
            )
            created += 1

    print(f"Labels: {created} created, {updated} updated, {len(existing)} previously existed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
