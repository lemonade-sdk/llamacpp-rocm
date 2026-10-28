#!/usr/bin/env python3
"""
Package a build into the exact archive that will be published, and describe it.

Previously the build job uploaded its raw output folder, the GPU test ran
against that folder, and the release job zipped a re-downloaded copy at the very
end. Nobody ever opened the zip a user actually downloads, and something was
lost on the way: GitHub's artifact storage does not keep the Linux "executable"
permission, so no Linux binary in a release could be started without a chmod.

Now the build job creates the final zip itself. Every later job -- the static
checks, the GPU checks and the release -- works on these exact bytes, and the
release job compares their SHA-256 before publishing (T0.11).

Symbolic links are stored as the files they point to, exactly as before, so the
extracted layout a consumer sees does not change.

Also writes qualify-meta-<os>-<family>.json next to the zip: which family and
gfx targets it was built for, the pinned ROCm and llama.cpp versions, and the
zip's SHA-256. The qualification jobs read it instead of repeating any of that.

Usage (CI):
    python3 package.py --source-dir llama.cpp/build/bin --os ubuntu \
        --gfx-target gfx110X --gpu-targets "gfx1100;gfx1101;gfx1102;gfx1103" \
        --rocm-version 10.3.0a20261027 --upstream-sha <40 hex> --output-dir dist
"""

import argparse
import hashlib
import json
import os
import zipfile


def archive_name(os_name, family):
    return f"llama-{os_name}-rocm-{family}-x64.zip"


def meta_name(os_name, family):
    return f"qualify-meta-{os_name}-{family}.json"


def write_zip(source, dest):
    """Zip `source`'s contents (not the folder itself) into `dest`.

    zipfile records each file's permission bits, so a Linux build keeps its
    executables executable. Entries are written in sorted order so two runs over
    the same files list them identically.
    """
    count = 0
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_DEFLATED,
                         compresslevel=6) as zf:
        for dirpath, dirnames, filenames in os.walk(source):
            dirnames.sort()
            rel_dir = os.path.relpath(dirpath, source)
            if rel_dir != ".":
                zf.write(dirpath, rel_dir.replace(os.sep, "/"))
            for name in sorted(filenames):
                path = os.path.join(dirpath, name)
                zf.write(path, os.path.relpath(path, source).replace(os.sep, "/"))
                count += 1
    return count


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Create the release archive and its metadata")
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--os", dest="os_name", required=True, choices=["windows", "ubuntu"])
    parser.add_argument("--gfx-target", required=True, help="the family, e.g. gfx110X")
    parser.add_argument("--gpu-targets", required=True,
                        help="the ';'-separated gfx targets it was compiled for")
    parser.add_argument("--rocm-version", required=True)
    parser.add_argument("--upstream-sha", required=True)
    parser.add_argument("--output-dir", default="dist")
    args = parser.parse_args()

    if not os.path.isdir(args.source_dir):
        raise SystemExit(f"::error::build output not found: {args.source_dir}")
    os.makedirs(args.output_dir, exist_ok=True)

    name = archive_name(args.os_name, args.gfx_target)
    dest = os.path.join(args.output_dir, name)
    files = write_zip(args.source_dir, dest)

    meta = {
        "os": args.os_name,
        "gfx_family": args.gfx_target,
        "gpu_targets": args.gpu_targets,
        "rocm_version": args.rocm_version,
        "upstream_sha": args.upstream_sha,
        "artifact": name,
        "files": files,
        "bytes": os.path.getsize(dest),
        "sha256": sha256_of(dest),
    }
    meta_path = os.path.join(args.output_dir, meta_name(args.os_name, args.gfx_target))
    with open(meta_path, "w", encoding="utf-8") as handle:
        json.dump(meta, handle, indent=2)
        handle.write("\n")

    print(f"Wrote {dest}: {files} files, {meta['bytes']:,} bytes, sha256 {meta['sha256']}")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
