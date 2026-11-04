#!/usr/bin/env python3
"""Select the next two nightly minor tracks above current stable, without builds."""

import argparse
import json
import re

import families
from resolve_build_inputs import fetch, tarball_names, version_key, resolve_llamacpp

STABLE_REPO = "lemonade-sdk/llama.cpp"


def select_tracks(stable_versions, names, pairs):
    key = lambda value: tuple(int(n) for n in value.split("."))
    if not stable_versions:
        raise ValueError("current stable qualification evidence is unavailable")
    for value in stable_versions:
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)+", value):
            raise ValueError("invalid stable ROCm version")
    anchor = max(stable_versions, key=key)
    available = []
    for os_name, target in pairs:
        platform = {"ubuntu": "linux", "windows": "windows"}[os_name]
        pattern = re.compile(
            r"^therock-dist-"
            + re.escape(platform + "-" + target)
            + r"-(\d+\.\d+\.\d+a\d{8})\.tar\.gz$"
        )
        available.append({m[1] for name in names if (m := pattern.fullmatch(name))})
    if not available:
        raise ValueError("no requested OS/GPU pairs")
    common = set.intersection(*available)
    by_track = {}
    for version in common:
        track = ".".join(version.split(".")[:2])
        if key(track) <= key(anchor)[:2]:
            continue
        if track not in by_track or version_key(version) > version_key(by_track[track]):
            by_track[track] = version
    selected = sorted(by_track, key=key)[:2]
    return {
        "stable_anchor": anchor,
        "builds": [
            {
                "track": track,
                "rocm_version": by_track[track],
                "create_release": i == len(selected) - 1,
            }
            for i, track in enumerate(selected)
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    pin = json.loads(
        fetch(f"https://api.github.com/repos/{STABLE_REPO}/commits/qualification-data")
    )["sha"]
    if not re.fullmatch(r"[a-f0-9]{40}", pin):
        raise ValueError("invalid stable source commit")
    url = f"https://raw.githubusercontent.com/{STABLE_REPO}/{pin}/qualification/index.json"
    index = json.loads(fetch(url))
    if index.get("schema_version") not in (1, 2) or index.get("repo") != STABLE_REPO:
        raise ValueError("invalid stable qualification index")
    pairs = [
        (os_name, families.tarball_target(f))
        for os_name in ("windows", "ubuntu")
        for f in families.FAMILIES
    ]
    plan = select_tracks(
        [row["rocm_version"] for row in index["builds"]], tarball_names(), pairs
    )
    plan.update(
        stable_input={
            "url": url,
            "commit": pin,
            "schema_version": index["schema_version"],
        },
        gfx_targets=",".join(families.FAMILIES),
        operating_systems="windows,ubuntu",
    )
    if plan["builds"]:
        # Both SDK tracks qualify the same engine revision.
        plan["llamacpp_sha"] = resolve_llamacpp("latest")
    with open(args.output, "w") as handle:
        json.dump(plan, handle, indent=2)
        handle.write("\n")
    print(json.dumps(plan, indent=2))


if __name__ == "__main__":
    main()
