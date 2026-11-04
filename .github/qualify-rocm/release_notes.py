#!/usr/bin/env python3
"""
Write the release notes: a download table first, then the build details.

A release carries one archive per family per operating system plus a
qualification record for each, so the Assets list runs to dozens of files whose
names only make sense to someone who already knows their gfx family. The notes
instead open with one row per family -- its gfx targets, the products it serves
(families.py) and a direct link to each operating system's archive -- so a user
can find their GPU by name and download in one click.

A family is marked hardware-validated only when every one of its archives is,
according to qualification-summary.json.

Two lines are read by other programs and keep their exact format:

  * "**Llama.cpp Commit Hash**: <short sha>" -- Lemonade reads it to decide
    which model architectures it supports, and fetch_release_asset.py falls
    back to it.
  * "**Llama.cpp Commit (full)**: <sha>" -- fetch_release_asset.py reads it, so
    the SHA comes straight after the colon and the link to it follows.

Usage (CI, after check_final.py and aggregate.py have filled the release folder):
    python3 release_notes.py --repo lemonade-sdk/llamacpp-rocm --tag b1366 \
        --release-dir release --gfx-targets gfx1151,gfx110X \
        --operating-systems windows,ubuntu --rocm-version 10.3.0a20261104 \
        --llamacpp-short cd959 --llamacpp-sha cd959bae99ff4bed22bef2604f45241c09bbb8c8 \
        --output release-notes.md
"""

import argparse
import io
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import families  # noqa: E402
from check_final import published_name  # noqa: E402

# Column order and heading for each operating system.
OS_COLUMNS = (("windows", "Windows"), ("ubuntu", "Linux"))
LLAMACPP_COMMIT_URL = "https://github.com/ggml-org/llama.cpp/commit/"


def human_size(size):
    """Decimal units, as GitHub shows asset sizes."""
    if size >= 1000 ** 3:
        return f"{size / 1000 ** 3:.2f} GB"
    return f"{size / 1000 ** 2:.0f} MB"


def validated_families(summary_path, tag, oses, targets):
    """Families whose every archive in this release ran on real hardware."""
    with open(summary_path, encoding="utf-8") as handle:
        archives = {a["artifact"]: a for a in json.load(handle).get("archives", [])}
    validated = set()
    for family in targets:
        names = [published_name(tag, o, family) for o in oses]
        if all(archives.get(n, {}).get("hardware_validated") for n in names):
            validated.add(family)
    return validated


def family_cell(family, validated):
    cell = f"**{family}**" + (" ✅" if family in validated else "")
    gfx = families.split_targets(families.gpu_targets(family))
    if gfx != [family]:
        cell += "<br><sub>" + " · ".join(gfx) + "</sub>"
    return cell


def render(args, oses, targets, sizes, validated):
    download = f"https://github.com/{args.repo}/releases/download/{args.tag}/"
    columns = [label for os_name, label in OS_COLUMNS if os_name in oses]

    lines = ["| gfx family | Supported GPUs | " + " | ".join(columns) + " |",
             "|---|---|" + "---|" * len(columns)]
    for family in targets:
        products = families.supported_gpus(family) or "—"
        links = []
        for os_name, _ in OS_COLUMNS:
            if os_name in oses:
                name = published_name(args.tag, os_name, family)
                links.append(f"[zip · {human_size(sizes[name])}]({download}{name})")
        lines.append(f"| {family_cell(family, validated)} | {products} | " + " | ".join(links) + " |")

    notes = []
    if validated:
        notes.append("✅ next to a gfx family means its archives were hardware-validated "
                     "(run on a real GPU) for this release.")
    notes += ["Every archive passed the release checks.",
              "Each archive bundles its ROCm runtime, so nothing else needs to be installed.",
              "If your GPU's gfx target isn't listed here, these builds don't support it."]

    build_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    lines += [""] + [f"- {note}" for note in notes] + ["", "---", "",
              f"**Build Number**: {args.tag}",
              f"**ROCm Version**: {args.rocm_version}",
              f"**Llama.cpp Commit Hash**: {args.llamacpp_short}",
              f"**Llama.cpp Commit (full)**: {args.llamacpp_sha} "
              f"([view on GitHub]({LLAMACPP_COMMIT_URL}{args.llamacpp_sha}))",
              f"**Build Date**: {build_date}",
              "",
              "<details>",
              "<summary>Verify your download</summary>",
              "",
              f"[`checksums.txt`]({download}checksums.txt) holds the SHA-256 of every archive.",
              "",
              "- Linux: `sha256sum --ignore-missing -c checksums.txt` in the folder holding "
              "the archive and `checksums.txt`.",
              "- Windows (PowerShell): `Get-FileHash <archive>.zip -Algorithm SHA256`, then "
              "compare the hash with the archive's line in `checksums.txt` (case does not matter).",
              "",
              f"[`qualification-summary.json`]({download}qualification-summary.json) records "
              "why this release was published. Each archive also has a "
              "`qualification-<archive>.json` in the assets below, listing what it contains, "
              "which gfx target (if any) it was run on real hardware with, and every check's "
              "result.",
              "",
              "</details>",
              ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Write the release notes with a download table")
    parser.add_argument("--repo", required=True, help="owner/name the release is created in")
    parser.add_argument("--tag", required=True)
    parser.add_argument("--release-dir", required=True,
                        help="folder holding the archives and qualification-summary.json")
    parser.add_argument("--gfx-targets", required=True,
                        help="comma-separated families, in table order")
    parser.add_argument("--operating-systems", required=True,
                        help="comma-separated: windows,ubuntu")
    parser.add_argument("--rocm-version", required=True)
    parser.add_argument("--llamacpp-short", required=True)
    parser.add_argument("--llamacpp-sha", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    targets = families.split_targets(args.gfx_targets)
    oses = families.split_targets(args.operating_systems.lower())

    # A link to a file that is not in the release would be a broken download.
    sizes, absent = {}, []
    for family in targets:
        for os_name in oses:
            name = published_name(args.tag, os_name, family)
            path = os.path.join(args.release_dir, name)
            if os.path.exists(path):
                sizes[name] = os.path.getsize(path)
            else:
                absent.append(name)
    if absent:
        raise SystemExit(f"::error::Not in {args.release_dir}: {', '.join(absent)}")

    summary = os.path.join(args.release_dir, "qualification-summary.json")
    if not os.path.exists(summary):
        raise SystemExit(f"::error::{summary} is missing; run aggregate.py first")
    validated = validated_families(summary, args.tag, oses, targets)

    text = render(args, oses, targets, sizes, validated)
    with open(args.output, "w", encoding="utf-8") as handle:
        handle.write(text)
    # Echoed for the job log; a console that cannot show ✅ should not fail the step.
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(errors="replace")
    print(text)


if __name__ == "__main__":
    main()
