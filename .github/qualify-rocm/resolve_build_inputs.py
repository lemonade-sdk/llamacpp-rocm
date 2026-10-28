#!/usr/bin/env python3
"""
Resolve, once per run, everything the build legs must agree on.

A run has one build leg per family per operating system (fourteen by default),
and they start minutes apart. Left to themselves, each leg cloned llama.cpp's
master branch and picked the newest ROCm nightly on its own, so one release
could mix upstream commits and ROCm versions while its notes named only one of
each -- whichever leg happened to report last. Lemonade reads the commit named
in those notes to decide which model architectures it supports, so a mixed
release misleads more than just the person reading it.

This runs once, in prepare-matrix, and pins:

  * the upstream llama.cpp commit, as a full SHA, and
  * one ROCm nightly version that exists for every requested family on every
    requested operating system. When the newest nightly is missing for some
    family, the newest version that all of them have is used instead, and the
    log says which families held it back.

It also emits the build, Tier 0 and Tier 1 matrices, so the family list is
expanded in exactly one place (families.py).

Usage (CI):
    python3 resolve_build_inputs.py --gfx-targets "gfx1151,gfx1150" \
        --operating-systems "windows,ubuntu" --rocm-version latest \
        --llamacpp-version latest

With --skip-pins only the Tier 0 / Tier 1 matrices are produced, for workflows
that test an already-published release.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import families  # noqa: E402

ROCM_INDEX_URL = "https://nightly.repo.amd.com/rocm/core/tarball/"
LLAMACPP_REPO = "https://github.com/ggml-org/llama.cpp.git"

KNOWN_OSES = ("windows", "ubuntu")
# TheRock names its tarballs by platform, not by distribution.
TARBALL_OS = {"windows": "windows", "ubuntu": "linux"}
# Tier 0 runs each archive on its own operating system, because two of its
# checks launch llama-server out of the archive. The Linux runner is the oldest
# distribution the archives support, so those launches also prove they start
# there.
TIER0_RUNNER = {"windows": "windows-2022", "ubuntu": "ubuntu-22.04"}
# The OS label of the self-hosted GPU runners; the other labels live in the
# workflow next to `runs-on`.
TIER1_RUNNER_OS = {"windows": "Windows", "ubuntu": "Linux"}

_VERSION = r"\d+\.\d+\.\d+(?:a|rc)\d+"


def fetch(url, attempts=5):
    last = None
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                return resp.read().decode("utf-8", "replace")
        except (urllib.error.URLError, OSError) as exc:
            last = exc
            print(f"Fetching {url} failed (attempt {attempt}/{attempts}): {exc}")
            time.sleep(10)
    raise SystemExit(f"::error::Could not fetch {url}: {last}")


def tarball_names():
    """Every file name in TheRock's nightly tarball index.

    The index is a static HTML page with the listing embedded as a JSON array
    (`const files = [...]`), which is what gets parsed here.
    """
    html = fetch(ROCM_INDEX_URL)
    match = re.search(r"const files = (\[.*?\]);", html, re.S)
    if not match:
        raise SystemExit(f"::error::Failed to parse the file index at {ROCM_INDEX_URL}")
    return [entry["name"] for entry in json.loads(match.group(1)) if "name" in entry]


def version_key(version):
    """Order nightlies by build date, then by version number on the same date."""
    date = re.search(r"(\d{8})$", version)
    numbers = tuple(int(n) for n in re.findall(r"\d+", version))
    return (date.group(1) if date else "", numbers)


def resolve_rocm(requested, pairs):
    """Pick the one ROCm nightly every (os, tarball target) pair will use."""
    names = tarball_names()
    available = {}
    for os_name, tarball in pairs:
        prefix = f"therock-dist-{TARBALL_OS[os_name]}-{tarball}-"
        # Anchored so that variants sharing the prefix, such as
        # "...-all-tests-<version>.tar.gz", are never picked.
        pattern = re.compile("^" + re.escape(prefix) + f"({_VERSION})" + r"\.tar\.gz$")
        available[(os_name, tarball)] = {m.group(1) for m in map(pattern.match, names) if m}

    print("Newest ROCm nightly per family:")
    for (os_name, tarball), versions in sorted(available.items()):
        newest = max(versions, key=version_key) if versions else "none"
        print(f"  {os_name:8s} {tarball:12s} {newest}")

    if requested != "latest":
        missing = sorted(f"{o}/{t}" for (o, t), v in available.items() if requested not in v)
        if missing:
            raise SystemExit(f"::error::ROCm {requested} is not published for: "
                             f"{', '.join(missing)}")
        return requested

    empty = sorted(f"{o}/{t}" for (o, t), v in available.items() if not v)
    if empty:
        raise SystemExit(f"::error::No ROCm nightly tarball at all for: {', '.join(empty)}")

    common = set.intersection(*available.values())
    if not common:
        raise SystemExit("::error::No single ROCm nightly is published for every "
                         "requested family and operating system, so no consistent "
                         "release can be built today.")
    chosen = max(common, key=version_key)

    newest = max(set.union(*available.values()), key=version_key)
    if newest != chosen:
        holding_back = sorted(f"{o}/{t}" for (o, t), v in available.items()
                              if newest not in v)
        print(f"::notice::ROCm {newest} is not yet published for "
              f"{', '.join(holding_back)}, so every leg uses {chosen}, the newest "
              "nightly all of them have.")
    return chosen


def resolve_llamacpp(requested):
    """Turn 'latest', a branch, a tag or a full SHA into one full commit SHA."""
    if re.fullmatch(r"[0-9a-f]{40}", requested):
        return requested
    ref = "master" if requested == "latest" else requested
    wanted = [f"refs/heads/{ref}", f"refs/tags/{ref}", f"refs/tags/{ref}^{{}}"]
    out = subprocess.run(["git", "ls-remote", LLAMACPP_REPO, *wanted],
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise SystemExit(f"::error::git ls-remote failed: {out.stderr.strip()}")
    found = {}
    for line in out.stdout.splitlines():
        sha, _, name = line.partition("\t")
        found[name.strip()] = sha.strip()
    # A peeled tag (^{}) is the commit an annotated tag points at.
    for name in (f"refs/tags/{ref}^{{}}", f"refs/tags/{ref}", f"refs/heads/{ref}"):
        if name in found:
            return found[name]
    raise SystemExit(f"::error::'{requested}' is not a branch or tag of "
                     "ggml-org/llama.cpp. Pass a branch, a tag, or a full "
                     "40-character commit SHA.")


def unique(items):
    seen, out = set(), []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def main():
    parser = argparse.ArgumentParser(description="Pin one llama.cpp commit and one ROCm nightly per run")
    parser.add_argument("--gfx-targets", required=True,
                        help="comma-separated families, e.g. gfx1151,gfx110X")
    parser.add_argument("--operating-systems", required=True,
                        help="comma-separated: windows,ubuntu")
    parser.add_argument("--rocm-version", default="latest")
    parser.add_argument("--llamacpp-version", default="latest")
    parser.add_argument("--skip-pins", action="store_true",
                        help="only emit the Tier 0 / Tier 1 matrices")
    args = parser.parse_args()

    targets = unique(families.split_targets(args.gfx_targets))
    oses = unique(families.split_targets(args.operating_systems.lower()))
    unknown = [o for o in oses if o not in KNOWN_OSES]
    if unknown or not oses:
        raise SystemExit(f"::error::Unknown operating system(s) {unknown}; "
                         f"use a comma-separated subset of {', '.join(KNOWN_OSES)}")
    if not targets:
        raise SystemExit("::error::No gfx targets requested")

    print(f"Families: {', '.join(targets)}")
    print(f"Operating systems: {', '.join(oses)}")

    outputs = {
        "gfx_targets": ",".join(targets),
        "operating_systems": ",".join(oses),
    }

    build_include = [{"gfx_target": f,
                      "gpu_targets": families.gpu_targets(f),
                      "tarball_target": families.tarball_target(f)} for f in targets]
    for os_name in KNOWN_OSES:
        selected = os_name in oses
        outputs[f"should_build_{os_name}"] = "true" if selected else "false"
        outputs[f"{os_name}_matrix"] = json.dumps({"include": build_include if selected else []})

    tier0 = [{"os": o, "gfx_target": f, "runner": TIER0_RUNNER[o]}
             for o in oses for f in targets]
    tier1 = []
    if families.HARDWARE_VALIDATED_FAMILY in targets:
        tier1 = [{"os": o, "gfx_target": families.HARDWARE_VALIDATED_FAMILY,
                  "runner_os": TIER1_RUNNER_OS[o]} for o in oses]
    outputs["tier0_matrix"] = json.dumps({"include": tier0})
    outputs["tier1_matrix"] = json.dumps({"include": tier1})
    outputs["run_tier1"] = "true" if tier1 else "false"

    if not args.skip_pins:
        pairs = unique((o, families.tarball_target(f)) for o in oses for f in targets)
        rocm = resolve_rocm(args.rocm_version, pairs)
        sha = resolve_llamacpp(args.llamacpp_version)
        outputs["rocm_version"] = rocm
        outputs["llamacpp_sha"] = sha
        # The release notes have always carried a five-character hash, and
        # Lemonade parses that line, so its format is kept as it was.
        outputs["llamacpp_short"] = sha[:5]
        print(f"Pinned ROCm nightly: {rocm}")
        print(f"Pinned llama.cpp commit: {sha}")

    for key, value in outputs.items():
        print(f"{key}={value}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            for key, value in outputs.items():
                handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
