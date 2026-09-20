#!/usr/bin/env bash
# create_deb.sh — Build a .deb package from an extracted llamacpp-rocm zip
#
# Usage:
#   create_deb.sh <input_dir> <version> <gpu_target> [output_dir]
#
# Arguments:
#   input_dir   - Directory containing the extracted zip contents (flat layout)
#   version     - Package version, e.g. "b1241" or "1241"
#   gpu_target  - GPU target string, e.g. "gfx1151"
#   output_dir  - Where to write the .deb (default: current directory)
#
# Requires: patchelf, dpkg-deb, python3
#
# Installed layout:
#   /usr/bin/                    — llama-* executables and rpc-server
#   /usr/lib/llamacpp-rocm/      — shared libraries (RPATH re-patched)
#   /usr/lib/llamacpp-rocm/rocblas/    — rocBLAS GPU kernels
#   /usr/lib/llamacpp-rocm/hipblaslt/  — hipBLASLt GPU kernels

set -euo pipefail

INPUT_DIR="${1:?Usage: $0 <input_dir> <version> <gpu_target> [output_dir]}"
VERSION="${2:?}"
GPU_TARGET="${3:?}"
OUTPUT_DIR="${4:-.}"

LIB_INSTALL_PATH="/usr/lib/llamacpp-rocm"
# Include GPU target in the package name so multiple GPU variants can coexist
# on disk; Provides/Conflicts ensure only one is active at a time.
PKG_NAME="llamacpp-rocm-${GPU_TARGET}"
# Debian version must start with a digit; prefix non-numeric versions with "0~"
if [[ "${VERSION}" =~ ^[0-9] ]]; then
    DEB_VERSION="${VERSION}"
else
    DEB_VERSION="0~${VERSION}"
fi
ARCH="amd64"

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT

PKG_ROOT="${STAGE_DIR}/${PKG_NAME}_${DEB_VERSION}_${ARCH}"
BIN_DIR="${PKG_ROOT}/usr/bin"
LIB_DIR="${PKG_ROOT}${LIB_INSTALL_PATH}"
DEBIAN_DIR="${PKG_ROOT}/DEBIAN"

echo "==> Staging package: ${PKG_NAME} ${DEB_VERSION} (${GPU_TARGET}) → ${OUTPUT_DIR}"

mkdir -p "$BIN_DIR" "$LIB_DIR" "$DEBIAN_DIR"

# ---------------------------------------------------------------------------
# 1. Copy executables → /usr/bin/
# ---------------------------------------------------------------------------
echo "--> Copying executables"
for f in "$INPUT_DIR"/llama-* "$INPUT_DIR/rpc-server"; do
    [ -f "$f" ] || continue
    # Skip non-ELF files (e.g. zip archives that match llama-* glob)
    file -b "$f" | grep -q "^ELF" || continue
    install -m 755 "$f" "$BIN_DIR/"
done

# ---------------------------------------------------------------------------
# 2. Copy GPU kernel data directories (preserve tree structure)
# ---------------------------------------------------------------------------
echo "--> Copying rocblas / hipblaslt kernel data"
for kdir in rocblas hipblaslt; do
    if [ -d "$INPUT_DIR/$kdir" ]; then
        cp -r "$INPUT_DIR/$kdir" "$LIB_DIR/"
    fi
done

# ---------------------------------------------------------------------------
# 3. Install shared libraries with proper symlinks (deduplicate)
#    In the zip, libfoo.so / libfoo.so.X / libfoo.so.X.Y.Z all contain
#    identical bytes. We install only the most-specific version as a real
#    file, and create symlinks for the shorter names.
# ---------------------------------------------------------------------------
echo "--> Installing shared libraries (deduplicating soname copies)"
python3 - "$INPUT_DIR" "$LIB_DIR" <<'PYEOF'
import os, sys, re, shutil
from collections import defaultdict

src_dir, dst_dir = sys.argv[1], sys.argv[2]

entries = [f for f in os.listdir(src_dir)
           if f.startswith('lib') and '.so' in f
           and os.path.isfile(os.path.join(src_dir, f))]

# Group by the base name (everything up to and including ".so")
groups = defaultdict(list)
for name in entries:
    base = re.sub(r'(\.so)(\..*)?$', r'\1', name)   # e.g. "libfoo.so"
    groups[base].append(name)

def version_weight(name, base):
    """Return a sort key: more version components = higher weight = canonical."""
    suffix = name[len(base):]   # e.g. "" or ".3" or ".3.0.0" or ".23.0git"
    return (len(suffix.split('.')), suffix)

for base, members in sorted(groups.items()):
    members.sort(key=lambda n: version_weight(n, base), reverse=True)
    canonical = members[0]   # most-specific version (real file)
    shutil.copy2(os.path.join(src_dir, canonical), os.path.join(dst_dir, canonical))
    # Create symlinks from shorter names → canonical
    for alt in members[1:]:
        link_path = os.path.join(dst_dir, alt)
        if os.path.exists(link_path):
            os.remove(link_path)
        os.symlink(canonical, link_path)
        print(f"  symlink: {alt} -> {canonical}")

PYEOF

# ---------------------------------------------------------------------------
# 4. Re-patch RPATH on executables and shared libraries
#    Original build uses $ORIGIN (all files in one flat dir).
#    After installation, executables live in /usr/bin/ and libraries in
#    /usr/lib/llamacpp-rocm/, so we must update RPATH accordingly.
# ---------------------------------------------------------------------------
echo "--> Re-patching RPATH"

# Executables: /usr/bin/ → find libraries at /usr/lib/llamacpp-rocm/
for f in "$BIN_DIR"/llama-* "$BIN_DIR/rpc-server"; do
    [ -f "$f" ] || continue
    patchelf --set-rpath "$LIB_INSTALL_PATH" "$f" 2>/dev/null || true
done

# Libraries: they also depend on each other → same RPATH
for f in "$LIB_DIR"/lib*.so*; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    patchelf --set-rpath "$LIB_INSTALL_PATH" "$f" 2>/dev/null || true
done

# ---------------------------------------------------------------------------
# 5. Create DEBIAN/control
# ---------------------------------------------------------------------------
echo "--> Writing DEBIAN/control"

# Estimate installed size (kB)
INSTALLED_KB=$(du -sk "$PKG_ROOT/usr" | cut -f1)

cat > "$DEBIAN_DIR/control" <<CTRL
Package: ${PKG_NAME}
Version: ${DEB_VERSION}
Architecture: ${ARCH}
Maintainer: Lemonade SDK <https://github.com/lemonade-sdk>
Installed-Size: ${INSTALLED_KB}
Depends: libc6 (>= 2.34)
Provides: llamacpp-rocm
Conflicts: llamacpp-rocm
Section: science
Priority: optional
Description: llama.cpp with AMD ROCm GPU acceleration (${GPU_TARGET})
 Self-contained llama.cpp build with bundled ROCm 7 runtime libraries.
 Supports AMD GPU inference via HIP/ROCm with no separate ROCm installation
 required. GPU target: ${GPU_TARGET}.
 .
 Includes llama-server, llama-cli, llama-quantize, and all other llama.cpp
 tools, along with bundled ROCm libraries (hipBLAS, rocBLAS, HIP runtime,
 LLVM/Clang JIT) and GPU kernel data for ${GPU_TARGET}.
CTRL

# ---------------------------------------------------------------------------
# 6. Build the .deb
# ---------------------------------------------------------------------------
mkdir -p "$OUTPUT_DIR"
DEB_FILE="${OUTPUT_DIR}/${PKG_NAME}_${DEB_VERSION}_${ARCH}.deb"

echo "--> Building ${DEB_FILE}"
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$DEB_FILE"

echo "==> Done: ${DEB_FILE}"
dpkg-deb --info "$DEB_FILE"
