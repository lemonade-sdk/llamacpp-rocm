#!/usr/bin/env python3
"""
The GPU families this repository publishes, and what each archive contains.

One archive is published per family per operating system. A family is either a
single gfx target (gfx1151) or a group of targets that share one archive
(gfx110X = gfx1100, gfx1101, gfx1102 and gfx1103).

This file is the one place that says which gfx targets a family's archive must
contain and which TheRock tarball it is built from. The build jobs compile
exactly the list given here and the checks audit against the same list, so the
two can never drift apart. It also names the products each family serves, for
the download table in the release notes (release_notes.py).
"""

# family -> (gfx targets compiled into the archive, TheRock tarball target)
FAMILIES = {
    "gfx1151": ("gfx1151", "gfx1151"),
    "gfx1150": ("gfx1150", "gfx1150"),
    "gfx117X": ("gfx1170;gfx1171", "gfx117X-all"),
    "gfx120X": ("gfx1200;gfx1201", "gfx120X-all"),
    "gfx110X": ("gfx1100;gfx1101;gfx1102;gfx1103", "gfx110X-all"),
    # Discrete RDNA2 only. The RDNA2 APUs (gfx1033, gfx1035, gfx1036) are not
    # compiled in, so a machine with one of those has no code in this archive.
    "gfx103X": ("gfx1030;gfx1031;gfx1032;gfx1034", "gfx103X-all"),
    "gfx90a": ("gfx90a", "gfx90a"),
    "gfx908": ("gfx908", "gfx908"),
}

# family -> the products it serves, as shown in the release notes.
#
# Only products an official source places on one of the family's targets are named:
# AMD's GPU hardware specifications (https://rocm.docs.amd.com/en/latest/reference/gpu-arch-specs.html) or
# the processor table in LLVM's AMDGPUUsage (https://github.com/llvm/llvm-project/blob/main/llvm/docs/AMDGPUUsage.md).
# 
# Neither is complete -- neither names a gfx1032 or gfx1034 product, for
# instance -- so the release notes also list each family's targets, and say
# support is decided by target.
# CPU names are examples: an APU is matched by the Radeon graphics it reports.
SUPPORTED_GPUS = {
    "gfx1151": "Radeon 8065S / 8060S / 8050S / 8040S (e.g. Ryzen AI Max+ 395, Ryzen AI Max 390)",
    "gfx1150": "Radeon 890M / 880M (e.g. Ryzen AI 9 HX 370, Ryzen AI 9 365)",
    "gfx117X": "No products listed by AMD yet",
    "gfx120X": ("Radeon RX 9070 XT / 9070 GRE / 9070, RX 9060 XT / 9060, RX 9050; "
                "Radeon AI PRO R9700 / R9700S / R9600 / R9600D"),
    "gfx110X": ("Radeon RX 7900 XTX / XT / GRE, RX 7800 XT, RX 7700 XT / 7700, RX 7600 XT / 7600; "
                "Radeon PRO W7900 / W7800 / W7700, PRO V710; "
                "Radeon 780M / 760M / 740M (e.g. Ryzen 7 7840U, Ryzen 9 270)"),
    "gfx103X": "Radeon RX 6900 XT, RX 6800 XT / 6800, RX 6700 XT; Radeon PRO W6800, PRO V620",
    "gfx90a": "Instinct MI250X / MI250 / MI210",
    "gfx908": "Instinct MI100",
}

# Checked on import, so a family added without its products fails in
# prepare-matrix rather than in the release job after every build has run.
if set(SUPPORTED_GPUS) != set(FAMILIES):
    raise SystemExit("::error::families.py: FAMILIES and SUPPORTED_GPUS must list the same "
                     f"families (only in FAMILIES: {sorted(set(FAMILIES) - set(SUPPORTED_GPUS))}; "
                     f"only in SUPPORTED_GPUS: {sorted(set(SUPPORTED_GPUS) - set(FAMILIES))})")

# The only family the self-hosted GPU runners can execute.
HARDWARE_VALIDATED_FAMILY = "gfx1151"


def gpu_targets(family):
    """The ';'-separated gfx targets a family's archive is compiled for.

    A family not listed above is used verbatim as a single target, so a manual
    dispatch can still build one-off targets.
    """
    return FAMILIES.get(family, (family, family))[0]


def tarball_target(family):
    """The target name TheRock publishes this family's ROCm tarball under."""
    return FAMILIES.get(family, (family, family))[1]


def supported_gpus(family):
    """The products a family serves, or None for a one-off target."""
    return SUPPORTED_GPUS.get(family)


def split_targets(text):
    """Split a ';', ',' or whitespace separated list into clean names."""
    out = []
    for part in text.replace(";", ",").split(","):
        for name in part.split():
            name = name.strip().strip('"')
            if name:
                out.append(name)
    return out
