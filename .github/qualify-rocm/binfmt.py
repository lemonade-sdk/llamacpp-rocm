#!/usr/bin/env python3
"""
Read-only inspection of compiled binaries: which gfx targets they support,
which libraries they need, and which version of the C library they require.

Copied unchanged from lemonade-sdk/llama.cpp (.github/qualify-rocm/binfmt.py),
so a fix in one should be made in both.

Background, because none of this is obvious from the outside.

A compiled program is stored in a structured file format. Linux uses ELF (.so
shared libraries, and executables); Windows uses PE (.dll and .exe). They hold
the same kinds of information -- machine code, data, and a list of the other
libraries the program needs -- just arranged differently. Both divide the file
into named blocks called "sections".

AMD GPUs come in different architectures, called gfx targets -- gfx1151,
gfx1030, and so on. Each one needs machine code built specifically for it. When
llama.cpp's HIP backend is compiled it is asked to produce code for a whole list
of gfx targets at once, and the result is a single "fat binary": one file with a
stack of per-target builds inside it. That is why the HIP backend is around a
gigabyte while every other backend is a few megabytes.

The problem this module exists to solve: if the compiler produces code for only
some of the requested gfx targets, NOTHING reports an error. The file is still
created, the build still succeeds, and the file still has the expected name. A
user whose gfx target was dropped then runs llama.cpp, which looks for code for
their GPU, does not find it, and quietly falls back to the CPU -- much slower,
with no error message anywhere. So the only way to know is to open the fat
binary and count what is actually inside it.

Everything here uses the Python standard library only, so it can run on any
runner without installing a GPU toolchain.
"""

import os
import re
import struct

# ---------------------------------------------------------------------------
# Counting the gfx targets inside a fat binary
# ---------------------------------------------------------------------------

# Each per-target build inside the fat binary is labelled with a plain readable
# tag, stored as ordinary text right in the file. The tags look like this:
#
#     hip-amdgcn-amd-amdhsa--gfx1151
#     |    |                   |
#     |    |                   the gfx target this build is for
#     |    the AMD GPU platform the code was built for
#     the kind of GPU offloading
#
# So finding every gfx target is simply a matter of searching the file's bytes
# for that pattern and collecting the names. The double dash is not a typo --
# it is an intentionally empty field in the middle of the platform name.
#
# Two details the pattern handles deliberately:
#   * The leading word is matched loosely ("hip", "hipv4", ...) because
#     different compiler versions spell it differently.
#   * Requiring "amdgcn-amd-amdhsa" means only GPU builds match. The same file
#     also contains a regular CPU build labelled with an Intel/AMD CPU platform
#     name, and we do not want to count that as a gfx target.
BUNDLE_ENTRY_RE = re.compile(rb"[a-z0-9_]+-amdgcn-amd-amdhsa-[-0-9a-zA-Z_.:+]*")

# Marker text that begins each group of per-target builds. Counted purely as a
# sanity signal, so the report can show how many groups were found.
BUNDLE_MAGIC = b"__CLANG_OFFLOAD_BUNDLE__"

# gfx target names look like gfx1151, gfx90a, gfx1030.
_GFX_RE = re.compile(r"(gfx[0-9a-fA-F]+)")

# The fat binary is around a gigabyte, so read it in pieces instead of loading
# the whole thing into memory at once.
_CHUNK = 64 << 20
# When reading in pieces, a tag could land half in one piece and half in the
# next. Carrying this many bytes over to the next piece guarantees every tag is
# seen whole. It comfortably exceeds the length of any tag.
_OVERLAP = 256


def strip_target_id(entry_id):
    """Pull the gfx target name out of a tag.

    Some builds append extra hardware options after the name, separated by a
    colon, e.g. 'gfx90a:xnack+'. Those describe optional features, not a
    different target, so everything from the colon onward is discarded.

        'hip-amdgcn-amd-amdhsa--gfx90a:xnack+'  ->  'gfx90a'
    """
    match = _GFX_RE.search(entry_id.split(":", 1)[0])
    return match.group(1) if match else None


def scan_offload_bundles(path):
    """Search a binary for per-target build tags.

    Returns (how many times each tag appears, how many build groups were found,
    how many bytes were read).

    This reads the file's raw bytes rather than asking a compiler tool to list
    the contents, for two reasons.

    First, it works the same on Linux and Windows. The GPU code lives in a
    section whose name is '.hip_fatbin' on Linux -- but Windows caps section
    names at eight characters once a file is linked, so the very same section
    is called '.hip_fat' there. Tools that are told to look up the section by
    name therefore find nothing on Windows. Searching the bytes does not care
    what the section is called, so one piece of code covers both.

    Second, it needs no AMD toolchain installed, which means this check can run
    on an ordinary runner against the archive we actually publish, instead of
    only on the machine that compiled it.
    """
    counts = {}
    magic = 0
    total = 0

    def tally(window, limit):
        """Count tags that begin before `limit`.

        The bytes from `limit` onward get carried into the next piece of the
        file, where they will be examined then. That way each position in the
        file is considered exactly once: nothing is counted twice, and no tag
        is missed because it straddled a boundary.
        """
        nonlocal magic
        for match in BUNDLE_ENTRY_RE.finditer(window):
            if match.start() >= limit:
                break
            entry = match.group(0).decode("ascii", "replace")
            counts[entry] = counts.get(entry, 0) + 1
        start = 0
        while True:
            found = window.find(BUNDLE_MAGIC, start)
            if found < 0 or found >= limit:
                break
            magic += 1
            start = found + 1

    tail = b""
    with open(path, "rb") as handle:
        while True:
            buf = handle.read(_CHUNK)
            if not buf:
                tally(tail, len(tail))
                break
            total += len(buf)
            window = tail + buf
            safe = max(len(window) - _OVERLAP, 0)
            tally(window, safe)
            tail = window[safe:]
    return counts, magic, total


def audit_gfx_targets(path, expected):
    """Check which gfx targets a fat binary actually contains code for.

    `expected` is the list of gfx targets the build was asked to produce, taken
    from the build job itself so Linux and Windows are each compared against
    their own list rather than a shared guess.

    The returned dict reports which targets are present, which are missing, and
    how many times each one appears. That last number is useful on its own: the
    compiler emits one entry per source file it compiles, so in a healthy build
    every target has the SAME count. A target with a noticeably lower count was
    built into only part of the project, which a simple present/absent check
    would miss entirely.
    """
    counts, magic, total = scan_offload_bundles(path)
    per_target = {}
    for entry, n in counts.items():
        target = strip_target_id(entry)
        if target:
            per_target[target] = per_target.get(target, 0) + n

    expected_set = {strip_target_id(t) or t for t in expected}
    present = set(per_target)
    uniform = len(set(per_target.values())) <= 1

    return {
        "binary": os.path.basename(path),
        "bytes_scanned": total,
        "bundle_magic_count": magic,
        "expected_count": len(expected_set),
        "present_count": len(present),
        "expected": sorted(expected_set),
        "present": sorted(present),
        "missing": sorted(expected_set - present),
        "extra": sorted(present - expected_set),
        "entries_per_target": dict(sorted(per_target.items())),
        "entry_count_uniform": uniform,
    }


# ---------------------------------------------------------------------------
# ELF -- the Linux program file format
# ---------------------------------------------------------------------------

def is_elf(path):
    try:
        with open(path, "rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def elf_sections(path):
    """List the named blocks ("sections") an ELF file is divided into.

    Each entry gives the section's name, where it starts in the file, and how
    big it is. The rest of this module uses it to find specific sections such
    as the list of required libraries.
    """
    with open(path, "rb") as handle:
        header = handle.read(64)
        if header[:4] != b"\x7fELF" or header[4] != 2:
            raise ValueError(f"{path}: not ELF64")
        e_shoff, = struct.unpack_from("<Q", header, 0x28)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", header, 0x3A)
        if e_shnum == 0 or e_shoff == 0:
            return []

        handle.seek(e_shoff)
        raw = handle.read(e_shentsize * e_shnum)
        sections = []
        for i in range(e_shnum):
            off = i * e_shentsize
            name_off, sh_type, _flags, _addr, sh_offset, sh_size = struct.unpack_from(
                "<IIQQQQ", raw, off)
            sections.append({"name_off": name_off, "type": sh_type,
                             "offset": sh_offset, "size": sh_size})

        shstr = sections[e_shstrndx]
        handle.seek(shstr["offset"])
        strtab = handle.read(shstr["size"])
        for section in sections:
            end = strtab.find(b"\0", section["name_off"])
            section["name"] = strtab[section["name_off"]:end].decode("ascii", "replace")
        return sections


def _section(sections, name):
    return next((s for s in sections if s["name"] == name), None)


def elf_dynamic(path, sections=None):
    """Read a Linux binary's library requirements and its search path.

    Returns two lists:

      * the names of the shared libraries this binary needs in order to start,
        e.g. "libamdhip64.so.7". If one of them is not on the user's machine,
        the program fails to launch.
      * the directories the loader should look in for them. "$ORIGIN" is a
        placeholder meaning "the folder this binary is sitting in", which is
        what a portable archive wants. Anything else is an absolute path, and
        an absolute path that only existed on the build machine is a bug.
    """
    sections = sections if sections is not None else elf_sections(path)
    dynamic = _section(sections, ".dynamic")
    dynstr = _section(sections, ".dynstr")
    if not dynamic or not dynstr:
        return [], []

    with open(path, "rb") as handle:
        handle.seek(dynstr["offset"])
        strs = handle.read(dynstr["size"])
        handle.seek(dynamic["offset"])
        blob = handle.read(dynamic["size"])

    needed, runpath = [], []
    for off in range(0, len(blob) - 15, 16):
        tag, val = struct.unpack_from("<qQ", blob, off)
        if tag == 0:  # DT_NULL
            break
        if tag in (1, 15, 29):  # NEEDED, RPATH, RUNPATH
            end = strs.find(b"\0", val)
            if end < 0:
                continue
            text = strs[val:end].decode("ascii", "replace")
            (needed if tag == 1 else runpath).append(text)
    return needed, runpath


_GLIBC_RE = re.compile(rb"GLIBC_(\d+\.\d+(?:\.\d+)?)")


def elf_glibc_floor(path, sections=None):
    """Work out the oldest Linux version a binary will still run on.

    Every Linux program is built against the system C library, "glibc", and
    records the newest glibc feature it relied on. Newer Linux distributions
    ship newer glibc. If a program needs a newer glibc than the machine it
    lands on provides, it refuses to start with an error like
    "version `GLIBC_2.38' not found".

    Nothing warns about this when the program is built -- it only shows up on
    the user's machine -- so we read the number out of the file and compare it
    against the oldest distribution we intend to support.

    Returns the version as a tuple like (2, 38), or None if nothing was found.
    """
    sections = sections if sections is not None else elf_sections(path)
    dynstr = _section(sections, ".dynstr")
    if not dynstr:
        return None
    with open(path, "rb") as handle:
        handle.seek(dynstr["offset"])
        blob = handle.read(dynstr["size"])
    versions = {tuple(int(p) for p in m.decode().split("."))
                for m in _GLIBC_RE.findall(blob)}
    return max(versions) if versions else None


# ---------------------------------------------------------------------------
# PE -- the Windows program file format
# ---------------------------------------------------------------------------

def is_pe(path):
    try:
        with open(path, "rb") as handle:
            if handle.read(2) != b"MZ":
                return False
            handle.seek(0x3C)
            pe_off, = struct.unpack("<I", handle.read(4))
            handle.seek(pe_off)
            return handle.read(4) == b"PE\0\0"
    except (OSError, struct.error):
        return False


def pe_sections(path):
    """List the named blocks ("sections") a Windows binary is divided into.

    Worth knowing: Windows truncates section names to eight characters once a
    file has been linked, so a section Linux would call ".hip_fatbin" appears
    here as ".hip_fat". That is why nothing in this module looks a section up
    by name when it can avoid it.
    """
    with open(path, "rb") as handle:
        handle.seek(0x3C)
        pe_off, = struct.unpack("<I", handle.read(4))
        handle.seek(pe_off + 4)
        coff = handle.read(20)
        nsections, = struct.unpack_from("<H", coff, 2)
        opt_size, = struct.unpack_from("<H", coff, 16)
        handle.seek(pe_off + 24 + opt_size)
        out = []
        for _ in range(nsections):
            raw = handle.read(40)
            if len(raw) < 40:
                break
            name = raw[:8].rstrip(b"\0").decode("ascii", "replace")
            vsize, _vaddr, rawsize, _rawptr = struct.unpack_from("<IIII", raw, 8)
            out.append((name, vsize, rawsize))
        return out


def pe_imports(path):
    """Read the list of DLLs a Windows binary needs in order to start.

    The Windows equivalent of the library list in elf_dynamic(). If one of
    these DLLs is missing on the user's machine, the program fails to load.

    Returns an empty list rather than raising if the file cannot be parsed:
    this feeds a report, and an unreadable file should show up as a finding,
    not as a crash that stops every other check from running.
    """
    with open(path, "rb") as handle:
        data = handle.read()

    try:
        pe_off, = struct.unpack_from("<I", data, 0x3C)
        nsections, = struct.unpack_from("<H", data, pe_off + 6)
        opt_size, = struct.unpack_from("<H", data, pe_off + 20)
        opt_off = pe_off + 24
        magic, = struct.unpack_from("<H", data, opt_off)
        # PE32+ (0x20b) puts the data directories 16 bytes further along.
        dd_off = opt_off + (112 if magic == 0x20B else 96)
        import_rva, import_size = struct.unpack_from("<II", data, dd_off + 8)
        if not import_rva or not import_size:
            return []

        sections = []
        sec_off = opt_off + opt_size
        for i in range(nsections):
            raw = data[sec_off + i * 40: sec_off + i * 40 + 40]
            if len(raw) < 40:
                break
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", raw, 8)
            sections.append((vaddr, max(vsize, rawsize), rawptr, rawsize))

        def rva_to_off(rva):
            for vaddr, vsize, rawptr, rawsize in sections:
                if vaddr <= rva < vaddr + vsize:
                    delta = rva - vaddr
                    return rawptr + delta if delta < rawsize else None
            return None

        names = []
        table = rva_to_off(import_rva)
        if table is None:
            return []
        while True:
            entry = data[table: table + 20]
            if len(entry) < 20 or entry == b"\0" * 20:
                break
            name_rva, = struct.unpack_from("<I", entry, 12)
            name_off = rva_to_off(name_rva)
            if name_off is not None:
                end = data.find(b"\0", name_off)
                names.append(data[name_off:end].decode("ascii", "replace"))
            table += 20
        return names
    except (struct.error, IndexError, ValueError):
        return []
