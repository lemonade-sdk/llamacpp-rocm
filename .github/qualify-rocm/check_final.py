#!/usr/bin/env python3
"""
T0.11 -- the archive about to be published is the archive that was tested.

Runs in the release job, which is the only place this can be answered. For
every archive a build job recorded, it:

  * compares the file's SHA-256 with the one the build job wrote down when it
    created the zip, so the bytes Tier 0 and Tier 1 vouched for are exactly the
    bytes that ship;
  * confirms the zip still opens and still holds llama-server and the HIP
    backend;
  * on Linux, confirms the programs kept their "executable" permission, so a
    user who unzips the archive by hand can start them. This one is a warning
    rather than a failure, because Lemonade sets the permission itself;
  * moves the zip into the release folder under its published name,
    llama-<tag>-<os>-rocm-<family>-x64.zip.

Deliberately cheap: it reads the zip's index and four bytes per top-level file
rather than extracting gigabytes again.

Usage:
    python3 check_final.py --meta-dir meta --artifacts-dir artifacts \
        --release-dir release --fragments-dir fragments --tag b1357
"""

import argparse
import glob
import json
import os
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import families  # noqa: E402
import package  # noqa: E402
import report  # noqa: E402

REQUIRED = {
    "ubuntu": ["llama-server", "libggml-hip.so"],
    "windows": ["llama-server.exe", "ggml-hip.dll"],
}


def published_name(tag, os_name, family):
    return f"llama-{tag}-{os_name}-rocm-{family}-x64.zip"


def check_archive(path, info):
    os_name = info["os"]
    details = {"expected_sha256": info.get("sha256"), "expected_bytes": info.get("bytes")}
    if not os.path.exists(path):
        return (report.STATUS_FAIL,
                f"{os.path.basename(path)} was never delivered to the release job", details)

    details["sha256"] = package.sha256_of(path)
    details["bytes"] = os.path.getsize(path)
    if details["sha256"] != info.get("sha256"):
        return (report.STATUS_FAIL,
                "this is not the archive that was tested: its SHA-256 differs from "
                "the one the build job recorded", details)

    try:
        with zipfile.ZipFile(path) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            details["entries"] = len(infos)
            missing = [r for r in REQUIRED[os_name] if r not in names]
            not_executable = []
            if os_name == "ubuntu":
                for item in infos:
                    if "/" in item.filename or item.is_dir() or ".so" in item.filename:
                        continue
                    with zf.open(item) as handle:
                        if handle.read(4) != b"\x7fELF":
                            continue
                    if not (item.external_attr >> 16) & 0o111:
                        not_executable.append(item.filename)
    except zipfile.BadZipFile as exc:
        return (report.STATUS_FAIL, f"the archive does not open: {exc}", details)

    details["missing"] = missing
    details["not_executable"] = sorted(not_executable)
    if missing:
        return (report.STATUS_FAIL, f"the archive is missing {', '.join(missing)}", details)
    if not_executable:
        return (report.STATUS_WARN,
                f"{len(not_executable)} program(s) lack the executable permission "
                f"(e.g. {sorted(not_executable)[0]}); a user starting them by hand "
                "gets 'permission denied'", details)
    return (report.STATUS_PASS, None, details)


def main():
    parser = argparse.ArgumentParser(description="T0.11: the published archive is the tested one")
    parser.add_argument("--meta-dir", required=True)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--release-dir", required=True)
    parser.add_argument("--fragments-dir", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    args = parser.parse_args()

    metas = sorted(glob.glob(os.path.join(args.meta_dir, "qualify-meta-*.json")))
    if not metas:
        raise SystemExit(f"::error::no qualify-meta-*.json in {args.meta_dir}")
    os.makedirs(args.release_dir, exist_ok=True)
    os.makedirs(args.fragments_dir, exist_ok=True)

    for meta_path in metas:
        with open(meta_path, encoding="utf-8-sig") as handle:
            info = json.load(handle)
        os_name, family = info["os"], info["gfx_family"]
        name = published_name(args.tag, os_name, family)
        source = os.path.join(args.artifacts_dir, info["artifact"])

        tier = report.TierReport("tier0", report.build_meta(
            artifact=name,
            os_name=os_name,
            gfx_family=family,
            gpu_targets=families.split_targets(info.get("gpu_targets") or ""),
            rocm_version=info.get("rocm_version"),
            upstream_sha=info.get("upstream_sha"),
            release_tag=args.tag,
            run_id=args.run_id,
            run_attempt=args.run_attempt,
        ))
        print(f"::group::T0.11 -- {name}")
        tier.run("T0.11", "published archive is the tested archive",
                 lambda: check_archive(source, info))
        tier.write(os.path.join(args.fragments_dir, f"tier0-final-{os_name}-{family}.json"))
        tier.print_summary()
        if os.path.exists(source):
            shutil.move(source, os.path.join(args.release_dir, name))
            print(f"Staged {source} -> {os.path.join(args.release_dir, name)}")
        print("::endgroup::")


if __name__ == "__main__":
    main()
