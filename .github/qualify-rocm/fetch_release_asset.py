#!/usr/bin/env python3
"""
Resolve a published release and, optionally, download one of its archives.

Used by the manual "Test Llama.cpp + ROCm" workflow to re-qualify what users
actually downloaded. Written in Python so it behaves the same on hosted runners
and on self-hosted GPU machines, which may not have gh or jq.

  * Resolves "latest" to a tag.
  * Reads the llama.cpp commit the release notes name, so T0.9 can compare the
    binary against it: the full SHA when the notes carry one, otherwise the
    short hash older releases used.
  * With --asset, downloads that archive and, when the release has a
    checksums.txt, verifies it against that.

Usage:
    python3 fetch_release_asset.py --repo lemonade-sdk/llamacpp-rocm --tag latest
    python3 fetch_release_asset.py --repo lemonade-sdk/llamacpp-rocm --tag b1356 \
        --asset llama-b1356-ubuntu-rocm-gfx1151-x64.zip --output-dir artifact
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request

API = "https://api.github.com"

FULL_SHA_RE = re.compile(r"Llama\.cpp Commit \(full\)\**:\s*`?([0-9a-f]{40})", re.I)
# The same pattern Lemonade uses to read these notes.
SHORT_SHA_RE = re.compile(r"Llama\.cpp Commit Hash[^:]*:\s*`?([0-9a-f]+)", re.I)


def api_get(path):
    headers = {"Accept": "application/vnd.github+json",
               "User-Agent": "llamacpp-rocm-qualification"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(urllib.request.Request(API + path, headers=headers),
                                timeout=60) as resp:
        return json.load(resp)


def download(url, dest, attempts=3):
    """Fetch a public release asset. No token is sent: the download redirects to
    storage that rejects requests carrying a second form of authentication."""
    last = None
    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "llamacpp-rocm-qualification"})
            with urllib.request.urlopen(req, timeout=600) as resp, open(dest, "wb") as handle:
                shutil.copyfileobj(resp, handle)
            return dest
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            print(f"Download failed (attempt {attempt}/{attempts}): {exc}")
            time.sleep(5 * attempt)
    raise SystemExit(f"::error::could not download {url}: {last}")


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Resolve a release and fetch an archive")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", default="latest")
    parser.add_argument("--asset", default=None)
    parser.add_argument("--output-dir", default=".")
    args = parser.parse_args()

    path = (f"/repos/{args.repo}/releases/latest" if args.tag == "latest"
            else f"/repos/{args.repo}/releases/tags/{args.tag}")
    try:
        release = api_get(path)
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"::error::release '{args.tag}' not found in {args.repo} ({exc.code})")
    tag = release["tag_name"]
    body = release.get("body") or ""
    match = FULL_SHA_RE.search(body) or SHORT_SHA_RE.search(body)
    upstream = match.group(1) if match else ""
    print(f"Release {tag}; llama.cpp commit named in its notes: {upstream or 'none'}")

    outputs = {"tag": tag, "upstream_sha": upstream}

    if args.asset:
        assets = {a["name"]: a for a in release.get("assets", [])}
        if args.asset not in assets:
            raise SystemExit(f"::error::asset {args.asset} not in release {tag}. "
                             f"Available: {', '.join(sorted(assets))}")
        os.makedirs(args.output_dir, exist_ok=True)
        dest = os.path.join(args.output_dir, args.asset)
        download(assets[args.asset]["browser_download_url"], dest)
        actual = sha256_of(dest)
        print(f"Downloaded {dest} ({os.path.getsize(dest):,} bytes, sha256 {actual})")
        if "checksums.txt" in assets:
            sums = os.path.join(args.output_dir, "checksums.txt")
            download(assets["checksums.txt"]["browser_download_url"], sums)
            listed = {}
            with open(sums, encoding="utf-8") as handle:
                for line in handle:
                    parts = line.split(None, 1)
                    if len(parts) == 2:
                        # sha256sum writes "<digest>  <name>", or "<digest> *<name>"
                        # in binary mode.
                        listed[parts[1].strip().lstrip("*")] = parts[0]
            if listed.get(args.asset) != actual:
                raise SystemExit(f"::error::{args.asset} does not match the release's "
                                 "checksums.txt")
            print("Matches the release's checksums.txt")
        outputs["archive"] = dest

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
