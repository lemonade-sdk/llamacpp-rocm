#!/usr/bin/env python3
"""
The GPU families this repository publishes, and what each archive contains.

One archive is published per family per operating system. A family is either a
single gfx target (gfx1151) or a group of targets that share one archive
(gfx110X = gfx1100, gfx1101, gfx1102 and gfx1103).

This file is the one place that says which gfx targets a family's archive must
contain and which TheRock tarball it is built from. The build jobs compile
exactly the list given here and the checks audit against the same list, so the
two can never drift apart.
"""

# family -> (gfx targets compiled into the archive, TheRock tarball target)
FAMILIES = {
    "gfx1151": ("gfx1151", "gfx1151"),
    "gfx1150": ("gfx1150", "gfx1150"),
    "gfx120X": ("gfx1200;gfx1201", "gfx120X-all"),
    "gfx110X": ("gfx1100;gfx1101;gfx1102;gfx1103", "gfx110X-all"),
    # Discrete RDNA2 only. The RDNA2 APUs (gfx1033, gfx1035, gfx1036) are not
    # compiled in, so a machine with one of those has no code in this archive.
    "gfx103X": ("gfx1030;gfx1031;gfx1032;gfx1034", "gfx103X-all"),
    "gfx90a": ("gfx90a", "gfx90a"),
    "gfx908": ("gfx908", "gfx908"),
}

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


def split_targets(text):
    """Split a ';', ',' or whitespace separated list into clean names."""
    out = []
    for part in text.replace(";", ",").split(","):
        for name in part.split():
            name = name.strip().strip('"')
            if name:
                out.append(name)
    return out
