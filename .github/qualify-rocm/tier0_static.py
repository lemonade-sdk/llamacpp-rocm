#!/usr/bin/env python3
"""
Tier 0 -- static verification of a release archive. No GPU required.

Runs on a hosted runner against the zip the build job created, which is the
exact file that gets published. Everything this tier looks for -- a dropped gfx
target, a library left out of the box, a leaked build path -- only shows up in
the packaged bytes.

These archives differ from lemonade-sdk/llama.cpp's in two ways that shape the
checks: there is one archive per GPU family rather than one fat binary for every
GPU, and each archive carries its own ROCm instead of relying on one the user
installed. So the question is never "will the user's ROCm have what this needs?"
but "is everything this needs inside the box?".

Check ids match lemonade-sdk/llama.cpp wherever the meaning is the same, and an
id is never reused for a different check:

  T0.1  archive contents       -- the tools a user runs, the ggml/llama
                                  libraries, the ROCm pieces that are loaded by
                                  name at run time, the kernel folder and the
                                  LICENSE are all present.
  T0.2  HIP backend present    -- ggml-hip exists and is big enough to hold GPU
                                  code for every target of the family.
  T0.3  gfx target audit       -- ggml-hip really contains code for every gfx
                                  target the family was built for. See binfmt.py.
  T0.5  self-contained         -- every library any binary here needs is either
                                  inside the archive or part of the base
                                  operating system. No ROCm library may come from
                                  outside: these archives promise ROCm built in.
                                  Reads only the files, never the runner, so a
                                  ROCm installed on the machine cannot hide a gap.
  T0.6  Linux version support  -- the archive needs no newer C library than the
                                  oldest Linux we support provides.
  T0.7  library search path    -- every Linux binary looks for its libraries in
                                  its own folder ($ORIGIN) and nowhere else.
  T0.8  llama-server flags     -- the flags Lemonade launches with, and the ones
                                  Tier 1 relies on, are all still accepted.
  T0.9  provenance             -- the archive was built from the llama.cpp commit
                                  and ROCm nightly this run pinned, and the binary
                                  itself reports that commit.
  T0.13 kernel data per target -- rocBLAS's precompiled kernels exist for every
                                  gfx target in the family.

Not carried over, because they have no meaning here:
  T0.4  CPU backend variants   -- this repository builds a single CPU backend.
  T0.10 Windows CPU merge      -- there is no merge step.
  T0.12 user ROCm resolution   -- ROCm is bundled; T0.5 covers it instead.

T0.11 is written by check_final.py, against the archive as it is published.

Usage:
    python3 tier0_static.py --archive llama-b1356-ubuntu-rocm-gfx1151-x64.zip \
        --os ubuntu --gfx-target gfx1151 --run-binaries --output tier0.json
"""

import argparse
import fnmatch
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import binfmt  # noqa: E402
import families  # noqa: E402
import report  # noqa: E402

# Tools a consumer actually invokes.
REQUIRED_TOOLS = ["llama-server", "llama-cli", "llama-bench", "llama-perplexity"]

REQUIRED_LIBS = {
    "ubuntu": ["libggml-base.so", "libggml.so", "libggml-cpu.so", "libggml-hip.so",
               "libllama.so"],
    "windows": ["ggml-base.dll", "ggml.dll", "ggml-cpu.dll", "ggml-hip.dll", "llama.dll"],
}

# ROCm pieces that are loaded by name while the program runs rather than linked
# against, so no binary's list of required libraries mentions them and T0.5
# cannot see them. On Linux the HIP runtime links comgr directly, so T0.5 does
# cover it there.
RUNTIME_LOADED = {
    "ubuntu": [],
    "windows": ["amd_comgr*.dll"],
}

# rocBLAS reads its precompiled kernels from here at run time; T0.13 checks the
# per-target contents.
REQUIRED_DIRS = ["rocblas/library"]

HIP_BACKEND = {"ubuntu": "libggml-hip.so", "windows": "ggml-hip.dll"}
HIP_RUNTIME_PREFIX = {"ubuntu": "libamdhip64.so", "windows": "amdhip64_"}

# The GPU code for one gfx target is tens of megabytes (the gfx1151-only
# backend is about 75 MB). A backend under this floor per target holds little
# or no GPU code at all.
MIN_HIP_BYTES_PER_TARGET = 16 << 20

# Libraries every Linux installation provides, so the archive does not and
# should not carry them. Deliberately strict: anything else -- even something as
# common as OpenSSL -- has to be inside the archive, because the archive is
# meant to run on a bare machine.
BASE_LIBS_LINUX = {
    "libc.so.6", "libm.so.6", "libdl.so.2", "librt.so.1", "libpthread.so.0",
    "libstdc++.so.6", "libgcc_s.so.1", "ld-linux-x86-64.so.2", "libutil.so.1",
    "libresolv.so.2",
}

# DLLs Windows itself provides, plus the Microsoft Visual C++ runtime, which
# comes from the Visual C++ Redistributable and has never been bundled.
BASE_DLLS_WIN = {
    "kernel32.dll", "user32.dll", "gdi32.dll", "advapi32.dll", "shell32.dll",
    "shlwapi.dll", "ole32.dll", "oleaut32.dll", "ws2_32.dll", "crypt32.dll",
    "bcrypt.dll", "setupapi.dll", "cfgmgr32.dll", "version.dll", "ntdll.dll",
    "dbghelp.dll", "powrprof.dll", "psapi.dll", "winmm.dll", "ucrtbase.dll",
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "concrt140.dll", "vcomp140.dll",
}
BASE_DLL_PREFIXES_WIN = ("api-ms-win-", "ext-ms-win-")

# Names that identify a ROCm library, used to make a T0.5 failure say plainly
# that ROCm itself is what leaked out of the box.
ROCM_LIB_PREFIXES = (
    "libamdhip64", "libhsa-runtime64", "libamd_comgr", "librocblas", "libhipblas",
    "librocsolver", "librocm", "libroctx", "librocprofiler", "libtensilelite",
    "liborigami", "librocroller", "libhiprtc",
)
ROCM_DLL_PREFIXES = (
    "amdhip64", "amd_comgr", "rocblas", "hipblas", "libhipblas", "rocsolver",
    "rocm_", "origami", "libtensilelite", "hiprtc",
)

# The oldest Linux these archives support. The build job deliberately runs on
# Ubuntu 22.04 for this reason (see commit 0fd2139), and 22.04 ships glibc
# 2.35. Raise this only together with the build job's runner image, and only
# after deciding that dropping the older distributions is acceptable.
SUPPORTED_GLIBC = (2, 35)
SUPPORTED_DISTRO = "Ubuntu 22.04"

# Flags Lemonade passes to llama-server. Upstream renames flags now and then and
# every nightly rebuilds from upstream master, so a rename should fail here the
# same day rather than surface as a Lemonade bug.
LEMONADE_SERVER_FLAGS = [
    "--model", "--ctx-size", "--device", "--port", "--jinja", "--metrics",
    "--mmproj", "--no-mmproj-offload", "--model-draft", "--embeddings",
    "--reranking",
    # Lemonade adds `--parallel 1` to every launch, `--spec-type` for models
    # with a draft or multi-token-prediction head, and `-hf` (long form
    # --hf-repo) for models it lets llama-server download itself.
    "--parallel", "--spec-type", "--hf-repo",
    # Tier 1 depends on these two: without --verbosity the server never prints
    # which device it used, and --n-gpu-layers is how it asks for the GPU. If
    # upstream renames either, failing here names the cause instead of leaving
    # Tier 1 to report a confusing absence of evidence.
    "--n-gpu-layers", "--verbosity",
]

# Environment variables that would let a launched binary borrow a ROCm installed
# on the machine, or pretend to be a different GPU, instead of standing alone.
FOREIGN_ROCM_ENV = (
    "LD_LIBRARY_PATH", "ROCM_PATH", "HIP_PATH", "HIP_DEVICE_LIB_PATH",
    "HSA_OVERRIDE_GFX_VERSION", "ROCBLAS_TENSILE_LIBPATH", "HIPBLASLT_TENSILE_LIBPATH",
)


# ---------------------------------------------------------------------------
# Archive handling
# ---------------------------------------------------------------------------

def extract(archive, dest):
    """Extract `archive` into `dest` and return the folder holding the binaries.

    The archives are flat -- the binaries sit at the top of the zip -- but a
    single top-level folder is tolerated so a layout change fails on the checks
    that matter rather than here.
    """
    with zipfile.ZipFile(archive) as zf:
        names = zf.namelist()
        zf.extractall(dest)
    tops = sorted({n.split("/")[0] for n in names if n.split("/")[0]})
    if len(tops) == 1 and os.path.isdir(os.path.join(dest, tops[0])):
        return os.path.join(dest, tops[0])
    return dest


def top_files(root):
    """Regular files directly inside `root` -- where every binary lives."""
    return sorted(f for f in os.listdir(root) if os.path.isfile(os.path.join(root, f)))


def manifest(root):
    """Every file in the archive with its size, for the qualification record."""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            path = os.path.join(dirpath, name)
            out.append([os.path.relpath(path, root).replace(os.sep, "/"),
                        os.path.getsize(path)])
    return out


def exe(name, os_name):
    return f"{name}.exe" if os_name == "windows" else name


def dependencies(path, os_name):
    """The libraries one binary asks for by name, or None if it is not a binary."""
    if os_name == "ubuntu":
        return binfmt.elf_dynamic(path)[0] if binfmt.is_elf(path) else None
    return binfmt.pe_imports(path) if binfmt.is_pe(path) else None


def is_base_library(name, os_name):
    if os_name == "ubuntu":
        return name in BASE_LIBS_LINUX
    low = name.lower()
    return low in BASE_DLLS_WIN or low.startswith(BASE_DLL_PREFIXES_WIN)


def is_rocm_library(name, os_name):
    if os_name == "ubuntu":
        return name.startswith(ROCM_LIB_PREFIXES)
    return name.lower().startswith(ROCM_DLL_PREFIXES)


def clean_env():
    """The runner's environment minus anything that could point at its own ROCm."""
    env = dict(os.environ)
    for key in FOREIGN_ROCM_ENV:
        env.pop(key, None)
    return env


def same_commit(a, b):
    """True if two commit ids (full or abbreviated) name the same commit."""
    a, b = (a or "").lower(), (b or "").lower()
    if min(len(a), len(b)) < 5:
        return False
    return a.startswith(b) or b.startswith(a)


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def check_contents(root, os_name):
    files = set(top_files(root))
    missing = [t for t in (exe(n, os_name) for n in REQUIRED_TOOLS) if t not in files]
    missing += [lib for lib in REQUIRED_LIBS[os_name] if lib not in files]
    for pattern in RUNTIME_LOADED[os_name]:
        if not fnmatch.filter(sorted(files), pattern):
            missing.append(pattern)
    for folder in REQUIRED_DIRS:
        path = os.path.join(root, *folder.split("/"))
        if not os.path.isdir(path) or not any(os.scandir(path)):
            missing.append(folder + "/")
    if "LICENSE" not in files:
        missing.append("LICENSE")

    listing = manifest(root)
    details = {
        "missing": missing,
        "files": len(listing),
        "bytes": sum(size for _, size in listing),
        "manifest": listing,
    }
    if missing:
        return (report.STATUS_FAIL, f"missing from archive: {', '.join(missing)}", details)
    return (report.STATUS_PASS, None, details)


def check_hip_backend(root, os_name, targets):
    name = HIP_BACKEND[os_name]
    path = os.path.join(root, name)
    floor = MIN_HIP_BYTES_PER_TARGET * max(len(targets), 1)
    details = {"backend": name, "exists": os.path.exists(path), "targets": len(targets),
               "min_bytes": floor}
    if not details["exists"]:
        return (report.STATUS_FAIL,
                f"{name} is missing -- the HIP backend silently failed to build "
                "or was dropped at packaging", details)
    size = os.path.getsize(path)
    details["bytes"] = size
    if size < floor:
        return (report.STATUS_FAIL,
                f"{name} is only {size / 1e6:.1f} MB for {len(targets)} gfx "
                f"target(s); at least {floor / 1e6:.0f} MB is expected, so it holds "
                "little or no GPU code", details)
    return (report.STATUS_PASS, None, details)


def check_gfx_targets(root, os_name, targets):
    name = HIP_BACKEND[os_name]
    path = os.path.join(root, name)
    if not os.path.exists(path):
        return (report.STATUS_FAIL, f"{name} missing (see T0.2)", {})
    if not targets:
        return (report.STATUS_FAIL, "no expected gfx target list was supplied", {})

    result = binfmt.audit_gfx_targets(path, targets)

    # An audit that can silently find nothing is worse than no audit: if the
    # scan returns zero targets the METHOD is broken (the toolchain changed the
    # bundle-entry format), and that must fail loudly rather than pass.
    if result["present_count"] == 0:
        return (report.STATUS_FAIL,
                "found NO gfx code objects at all -- the audit method is broken, "
                "not necessarily the build. Check the offload bundle entry format "
                "against binfmt.BUNDLE_ENTRY_RE.", result)

    if result["missing"]:
        return (report.STATUS_FAIL,
                f"{len(result['missing'])} gfx target(s) requested but NOT in "
                f"{name}: {', '.join(result['missing'])}. Users with those GPUs "
                "get no GPU code from this archive.", result)

    if not result["entry_count_uniform"]:
        counts = result["entries_per_target"]
        low = min(counts, key=counts.get)
        return (report.STATUS_WARN,
                f"all targets present but entry counts differ (e.g. {low} has "
                f"{counts[low]}, max is {max(counts.values())}) -- a target may be "
                "compiled into only some translation units", result)

    return (report.STATUS_PASS, None, result)


def check_self_contained(root, os_name):
    """Every dependency resolves inside the archive or from the base OS.

    The archive promises ROCm built in, so the strict rule here is the reverse
    of llama.cpp's: a ROCm library that is not in the box is a failure, never an
    expected external. Only what every machine of that OS already has may come
    from outside.
    """
    files = top_files(root)
    present = set(files) if os_name == "ubuntu" else {f.lower() for f in files}
    in_archive = (lambda d: d in present) if os_name == "ubuntu" else (lambda d: d.lower() in present)
    by_name = {f if os_name == "ubuntu" else f.lower(): f for f in files}

    unresolved, base_used, graph, scanned = {}, set(), {}, 0
    for name in files:
        path = os.path.join(root, name)
        try:
            deps = dependencies(path, os_name)
        except (ValueError, OSError, struct.error) as exc:
            unresolved[name] = [f"<unreadable: {exc}>"]
            continue
        if deps is None:
            continue
        scanned += 1
        graph[name] = [by_name[d if os_name == "ubuntu" else d.lower()]
                       for d in deps if in_archive(d)]
        missing = []
        for dep in deps:
            if in_archive(dep):
                continue
            if is_base_library(dep, os_name):
                base_used.add(dep)
                continue
            missing.append(dep)
        if missing:
            unresolved[name] = sorted(missing)

    # Everything llama-server pulls in, following the chain inside the archive.
    server = exe("llama-server", os_name)
    closure = set()
    stack = [server] if server in graph else []
    while stack:
        current = stack.pop()
        if current in closure:
            continue
        closure.add(current)
        stack.extend(graph.get(current, []))
    hip_runtime = sorted(f for f in closure if f.startswith(HIP_RUNTIME_PREFIX[os_name]))

    details = {
        "binaries_scanned": scanned,
        "unresolved": unresolved,
        "base_os_libraries_used": sorted(base_used),
        "llama_server_closure": sorted(closure),
        "rocm_libraries_loaded_by_llama_server": sorted(
            f for f in closure if is_rocm_library(f, os_name)),
    }
    if unresolved:
        flat = sorted({d for deps in unresolved.values() for d in deps})
        rocm = [d for d in flat if is_rocm_library(d, os_name)]
        lead = (f"ROCm library/libraries not bundled: {', '.join(rocm)}. On a user's "
                "machine these would come from a separately installed ROCm, or not "
                "at all. " if rocm else "")
        return (report.STATUS_FAIL,
                f"{lead}{len(unresolved)} binary/binaries need libraries that are "
                f"neither in the archive nor part of the base system: "
                f"{', '.join(flat[:8])}", details)
    if not hip_runtime:
        return (report.STATUS_FAIL,
                "llama-server does not load the HIP runtime at all, so this archive "
                "cannot use the GPU", details)
    return (report.STATUS_PASS, None, details)


def check_linux_version_support(root, os_name):
    """Confirm the archive still runs on the oldest Linux we support.

    Each binary records the newest version of the system C library it needs.
    Whichever binary needs the newest one decides the oldest Linux release the
    whole archive can run on.
    """
    if os_name != "ubuntu":
        return (report.STATUS_SKIP, "Linux-only check", {})

    needs, needs_file = (0, 0), None
    per_file = {}
    for name in top_files(root):
        path = os.path.join(root, name)
        if not binfmt.is_elf(path):
            continue
        floor = binfmt.elf_glibc_floor(path)
        if not floor:
            continue
        per_file[name] = ".".join(map(str, floor))
        if floor > needs:
            needs, needs_file = floor, name

    allowed = ".".join(map(str, SUPPORTED_GLIBC))
    actual = ".".join(map(str, needs)) if needs_file else None
    details = {
        "glibc_required": actual,
        "glibc_allowed": allowed,
        "oldest_supported_distro": SUPPORTED_DISTRO,
        "required_by": needs_file,
        "per_file": per_file,
    }
    if not needs_file:
        return (report.STATUS_WARN,
                "could not determine which C library version this archive needs",
                details)
    if needs > SUPPORTED_GLIBC:
        return (report.STATUS_FAIL,
                f"this archive needs glibc {actual}, but the oldest Linux we "
                f"support ({SUPPORTED_DISTRO}) only provides {allowed}. "
                f"{needs_file} is what requires it. Users on a supported "
                "distribution will see \"GLIBC_... not found\" and the program "
                "will not start. This usually means the build job's runner image "
                "moved to a newer Linux.", details)
    return (report.STATUS_PASS, None, details)


def check_rpath(root, os_name):
    """Every Linux binary searches its own folder for libraries, and only that.

    An absolute path left over from the build machine can make a binary pick up
    a different ROCm that happens to be installed at the same place on a user's
    machine. A binary with no search path at all only works when something else
    sets LD_LIBRARY_PATH, which a user who just unzipped the archive has not done.
    """
    if os_name != "ubuntu":
        return (report.STATUS_SKIP, "Linux-only check", {})

    files = set(top_files(root))
    leaks, no_path = {}, []
    for name in sorted(files):
        path = os.path.join(root, name)
        if not binfmt.is_elf(path):
            continue
        needed, runpath = binfmt.elf_dynamic(path)
        bad = [part for entry in runpath for part in entry.split(":")
               if part and not part.startswith("$ORIGIN")]
        if bad:
            leaks[name] = bad
        elif not runpath and any(dep in files for dep in needed):
            no_path.append(name)

    details = {"leaked": leaks, "no_search_path": no_path}
    problems = []
    if leaks:
        flat = sorted({p for parts in leaks.values() for p in parts})
        problems.append(f"{len(leaks)} binary/binaries ({', '.join(sorted(leaks)[:4])}) "
                        f"carry a build-machine path in their library search path, "
                        f"e.g. {flat[0]}")
    if no_path:
        problems.append(f"{len(no_path)} binary/binaries ({', '.join(no_path[:4])}) "
                        "need libraries from the archive but have no search path")
    if problems:
        return (report.STATUS_FAIL,
                "; ".join(problems) + ". The build job's 'Set RPATH' step should set "
                "every ELF file to '$ORIGIN'.", details)
    return (report.STATUS_PASS, None, details)


def run_server(root, os_name, flag):
    server = os.path.join(root, exe("llama-server", os_name))
    if os_name == "ubuntu":
        os.chmod(server, 0o755)
    proc = subprocess.run([server, flag], capture_output=True, text=True,
                          errors="replace", check=False, timeout=180, cwd=root,
                          env=clean_env())
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def check_server_flags(root, os_name, run_binaries):
    """The llama-server flags Lemonade launches with must still parse."""
    if not run_binaries:
        return (report.STATUS_SKIP, "not running binaries on this runner (OS mismatch)", {})
    if not os.path.exists(os.path.join(root, exe("llama-server", os_name))):
        return (report.STATUS_FAIL, "llama-server missing (see T0.1)", {})

    returncode, text = run_server(root, os_name, "--help")
    details: dict = {"returncode": returncode, "help_bytes": len(text)}
    if returncode != 0 or not text.strip():
        details["output_tail"] = text[-1500:]
        return (report.STATUS_FAIL,
                f"llama-server --help exited {returncode} with {len(text)} bytes of "
                "output. If it could not start at all, a library it needs is missing "
                "or unloadable on a machine without ROCm installed.", details)

    missing = [f for f in LEMONADE_SERVER_FLAGS
               if not re.search(rf"(?<![\w-]){re.escape(f)}(?![\w-])", text)]
    details["flags_checked"] = len(LEMONADE_SERVER_FLAGS)
    details["flags_missing"] = missing
    if missing:
        return (report.STATUS_FAIL,
                f"llama-server no longer accepts {', '.join(missing)} -- upstream "
                "renamed or removed them and Lemonade's launch command will break",
                details)
    return (report.STATUS_PASS, None, details)


def check_provenance(root, os_name, info, expected_sha, expected_rocm, run_binaries):
    """The archive is what this run pinned, and the binary agrees.

    Every build leg is meant to compile the same upstream commit against the
    same ROCm nightly. The build job records what it used; this compares that
    with what the run pinned, then asks llama-server itself which commit it was
    built from, so a leg that quietly built something else cannot hide behind
    its metadata.
    """
    archive_sha = info.get("upstream_sha")
    archive_rocm = info.get("rocm_version")
    details: dict = {
        "archive_upstream_sha": archive_sha,
        "expected_upstream_sha": expected_sha,
        "archive_rocm_version": archive_rocm,
        "expected_rocm_version": expected_rocm,
    }
    # What was actually compared. A pass is only claimed when at least one
    # comparison happened; otherwise the check reports itself skipped.
    compared = []
    problems, notes = [], []

    if expected_sha and archive_sha:
        compared.append("archive commit")
        if not same_commit(archive_sha, expected_sha):
            problems.append(f"built from llama.cpp {archive_sha[:12]}, but this run "
                            f"pinned {expected_sha[:12]}")
    if expected_rocm and archive_rocm:
        compared.append("archive ROCm version")
        if archive_rocm != expected_rocm:
            problems.append(f"bundles ROCm {archive_rocm}, but this run pinned {expected_rocm}")

    reference = expected_sha or archive_sha
    if run_binaries and os.path.exists(os.path.join(root, exe("llama-server", os_name))):
        returncode, text = run_server(root, os_name, "--version")
        details["version_returncode"] = returncode
        details["version_output"] = text.strip()[-600:]
        # Current upstream prints "version: <v> (build <n>, commit <sha>)";
        # older builds printed "version: <n> (<sha>)". Accept either.
        commit = (re.search(r"commit\s+([0-9a-f]{6,40})\b", text)
                  or re.search(r"version:\s*\d+\s*\(([0-9a-f]{6,40})\)", text))
        build = (re.search(r"build\s+(\d+)", text)
                 or re.search(r"version:\s*(\d+)\s*\(", text))
        details["binary_commit"] = commit.group(1) if commit else None
        details["binary_build_number"] = int(build.group(1)) if build else None
        if not commit:
            notes.append("could not read a commit from `llama-server --version`")
        elif reference:
            compared.append("binary commit")
            if not same_commit(commit.group(1), reference):
                problems.append(f"llama-server reports commit {commit.group(1)}, but the "
                                f"archive is supposed to be {reference[:12]}")

    details["compared"] = compared
    if problems:
        return (report.STATUS_FAIL, "; ".join(problems), details)
    if not compared and not notes:
        return (report.STATUS_SKIP, "nothing to compare: no recorded versions in the "
                "archive's metadata and the binary was not run", details)
    if notes:
        return (report.STATUS_WARN, "; ".join(notes), details)
    return (report.STATUS_PASS, None, details)


def check_kernel_data(root, targets):
    """rocBLAS has precompiled kernels for every gfx target in the family.

    ggml-hip does its matrix multiplications through hipBLAS, which hands them
    to rocBLAS, and rocBLAS loads per-target kernels from files at run time. A
    target whose kernels were left out loads fine and then fails the first time
    it multiplies two matrices. rocBLAS's kernels ship either as a
    rocblas/library/<target> folder or inside a .kpack/blas_lib_<target>.kpack
    pack; either counts.

    hipBLASLt kernels and the other packs are recorded but never required:
    hipBLASLt supports only some targets, so its absence is normal for others.
    """
    rocblas_dir = os.path.join(root, "rocblas", "library")
    hipblaslt_dir = os.path.join(root, "hipblaslt", "library")
    kpack_dir = os.path.join(root, ".kpack")
    kpacks = sorted(os.listdir(kpack_dir)) if os.path.isdir(kpack_dir) else []
    flat_rocblas = (sorted(os.listdir(rocblas_dir))
                    if os.path.isdir(rocblas_dir) else [])

    def non_empty(path):
        return os.path.isdir(path) and any(os.scandir(path))

    per_target, missing, no_pack = {}, [], []
    for target in targets:
        rocblas_folder = non_empty(os.path.join(rocblas_dir, target))
        rocblas_flat = any(target in name for name in flat_rocblas
                           if os.path.isfile(os.path.join(rocblas_dir, name)))
        packs = [k for k in kpacks if k.endswith(f"_{target}.kpack")]
        rocblas_pack = f"blas_lib_{target}.kpack" in packs
        per_target[target] = {
            "rocblas_folder": rocblas_folder or rocblas_flat,
            "rocblas_kpack": rocblas_pack,
            "hipblaslt_folder": non_empty(os.path.join(hipblaslt_dir, target)),
            "kpacks": packs,
        }
        if not (rocblas_folder or rocblas_flat or rocblas_pack):
            missing.append(target)
        if not packs:
            no_pack.append(target)

    details = {"per_target": per_target, "kpack_files": kpacks}
    if missing:
        return (report.STATUS_FAIL,
                f"rocBLAS has no kernels for {', '.join(missing)}: on those GPUs the "
                "first matrix multiplication fails at run time", details)
    if no_pack:
        return (report.STATUS_WARN,
                f"no .kpack kernel packs for {', '.join(no_pack)}; rocBLAS kernels "
                "are present, so this is recorded rather than failed", details)
    return (report.STATUS_PASS, None, details)


def main():
    parser = argparse.ArgumentParser(description="Tier 0 static archive verification")
    parser.add_argument("--meta", default=None,
                        help="qualify-meta-*.json written by the build job; supplies "
                             "--os/--gfx-target/--gpu-targets and the pinned versions, "
                             "and names the archive to find under --archive-dir")
    parser.add_argument("--archive-dir", default=".",
                        help="where to look for the archive named by --meta")
    parser.add_argument("--archive", help="the .zip to verify")
    parser.add_argument("--os", dest="os_name", choices=["ubuntu", "windows"])
    parser.add_argument("--gfx-target", help="the family, e.g. gfx110X")
    parser.add_argument("--gpu-targets", default=None,
                        help="';'-separated gfx targets; defaults to the family's list "
                             "in families.py")
    parser.add_argument("--expected-upstream-sha", default=None,
                        help="the llama.cpp commit this run pinned (enables T0.9)")
    parser.add_argument("--expected-rocm-version", default=None,
                        help="the ROCm nightly this run pinned (enables T0.9)")
    parser.add_argument("--release-tag", default=None)
    parser.add_argument("--run-binaries", action="store_true",
                        help="this runner's OS matches the archive, so its executables "
                             "can be launched (enables T0.8 and the binary half of T0.9)")
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    parser.add_argument("--output", default="tier0-report.json")
    parser.add_argument("--fail-on-error", action="store_true")
    args = parser.parse_args()

    info = {}
    if args.meta:
        with open(args.meta, encoding="utf-8-sig") as handle:
            info = json.load(handle)
        args.os_name = args.os_name or info["os"]
        args.gfx_target = args.gfx_target or info["gfx_family"]
        args.gpu_targets = args.gpu_targets or info.get("gpu_targets")
        if not args.archive:
            args.archive = os.path.join(args.archive_dir, info["artifact"])

    for required in ("archive", "os_name", "gfx_target"):
        if not getattr(args, required):
            parser.error(f"--{required.replace('_name', '').replace('_', '-')} is "
                         "required when --meta is not given")
    if not os.path.exists(args.archive):
        raise SystemExit(f"::error::archive not found: {args.archive}")

    targets = families.split_targets(args.gpu_targets or families.gpu_targets(args.gfx_target))

    meta = report.build_meta(
        artifact=os.path.basename(args.archive),
        os_name=args.os_name,
        gfx_family=args.gfx_target,
        gpu_targets=targets,
        rocm_version=info.get("rocm_version") or args.expected_rocm_version,
        upstream_sha=info.get("upstream_sha") or args.expected_upstream_sha,
        release_tag=args.release_tag,
        hardware_validated=False,
        run_id=args.run_id,
        run_attempt=args.run_attempt,
    )
    tier = report.TierReport("tier0", meta)

    workdir = tempfile.mkdtemp(prefix="tier0-", dir=os.environ.get("RUNNER_TEMP") or None)
    try:
        root = extract(args.archive, workdir)
        print(f"Extracted {args.archive} -> {root} ({len(top_files(root))} top-level files)")

        tier.run("T0.1", "archive contents",
                 lambda: check_contents(root, args.os_name))
        tier.run("T0.2", "HIP backend present",
                 lambda: check_hip_backend(root, args.os_name, targets))
        tier.run("T0.3", "gfx target audit",
                 lambda: check_gfx_targets(root, args.os_name, targets))
        tier.run("T0.5", "self-contained (no library from outside the archive)",
                 lambda: check_self_contained(root, args.os_name))
        tier.run("T0.6", "Linux version support",
                 lambda: check_linux_version_support(root, args.os_name))
        tier.run("T0.7", "library search path",
                 lambda: check_rpath(root, args.os_name))
        tier.run("T0.8", "llama-server flag surface",
                 lambda: check_server_flags(root, args.os_name, args.run_binaries))
        tier.run("T0.9", "provenance",
                 lambda: check_provenance(root, args.os_name, info,
                                          args.expected_upstream_sha,
                                          args.expected_rocm_version,
                                          args.run_binaries))
        tier.run("T0.13", "kernel data per target",
                 lambda: check_kernel_data(root, targets))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    tier.write(args.output)
    tier.print_summary()
    print(f"\nWrote {args.output}")

    if args.fail_on_error and tier.rollup_status() == report.STATUS_FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
