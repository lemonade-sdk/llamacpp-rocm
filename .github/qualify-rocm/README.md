# Release qualification

Verification of the archives this repository publishes. Driven from
`.github/workflows/build-llamacpp-rocm.yml` (every build) and
`.github/workflows/test-llamacpp-rocm.yml` (re-testing a published release), but every script here can be run by hand against a downloaded release asset.

Adapted from the suite in [lemonade-sdk/llama.cpp](https://github.com/lemonade-sdk/llama.cpp/tree/lemonade/.github/qualify-rocm). Check ids mean the same thing in both repositories, and an id is never reused for a different check.

## Why this exists

A broken ROCm build does not look broken. When ROCm cannot use the GPU, llama.cpp logs a line, finds zero GPUs and carries on using the CPU: the server starts, answers every request and returns correct answers, just far more slowly. A gfx target the compiler quietly left out, or a library left out of the box, behaves the same way on the user's machine and nowhere else.

## How these archives differ from lemonade-sdk/llama.cpp's

| | lemonade-sdk/llama.cpp | this repository |
|---|---|---|
| Archives | one fat binary per ROCm version, covering every GPU | one archive per GPU family per OS (14 by default) |
| ROCm | installed separately by the user | bundled inside every archive |
| The question that matters | will the user's ROCm have what this needs? | is everything this needs inside the box? |

So the checks that look for ROCm *outside* the archive there (T0.12, and installing a user-style ROCm for Tier 1) are replaced here by checks that nothing comes from outside (T0.5, T1.6).

## Tiers

| Tier | Runs on | Blocks the release? |
|---|---|---|
| Tier 0 — static checks | hosted runners, one job per archive, on the archive's own OS | yes |
| Tier 1 — GPU inference | the Strix Halo (gfx1151) machines, Windows and Linux | yes |

Both run on every trigger, pull requests included. The one exception is that Tier 1 is skipped for a pull request from a fork, which must not reach the self-hosted machines.

The release is all or nothing. If any archive fails to build or fails a gating check (Tier 0/1 qualification test), nothing is published, and the nightly-failure alert opens an issue instead.

A manual run only publishes a release when it builds every gfx family in `families.py` on both Windows and Linux. A manual run of fewer families still builds and runs every check, but creates no release.

gfx1151 is always built, even by a manual run that leaves it out, so every release has been run on real hardware.

### Tier 0 — `tier0_static.py`, `check_final.py`

Runs against the zip the build job created, which is the exact file that gets published.

| ID | Check | Gating |
|---|---|---|
| T0.1 | the tools, ggml/llama libraries, run-time-loaded ROCm pieces, kernel folder and LICENSE are present | yes |
| T0.2 | `ggml-hip` exists and is big enough to hold GPU code for every target in the family | yes |
| T0.3 | `ggml-hip` really contains code for every gfx target of the family | yes |
| T0.5 | every library any binary needs is in the archive or part of the base OS; no ROCm library from outside | yes |
| T0.6 | the archive needs no newer glibc than Ubuntu 22.04 provides | yes |
| T0.7 | every Linux binary looks for libraries only in its own folder (`$ORIGIN`) | yes |
| T0.8 | `llama-server` still accepts the flags Lemonade and Tier 1 use | yes |
| T0.9 | built from the llama.cpp commit and ROCm nightly this run pinned, and the binary reports that commit | yes |
| T0.11 | the published archive is byte-for-byte the tested one; Linux programs keep their executable bit (warning only) | yes |
| T0.13 | rocBLAS has precompiled kernels for every gfx target of the family | yes |

Not carried over from lemonade-sdk/llama.cpp: 
- T0.4 (CPU backend variants; this repository builds one CPU backend),
- T0.10 (Windows CPU-backend merge; there is no merge),
- T0.12 (resolution against a user-installed ROCm; ROCm is bundled).

T0.11 runs inside the release job, which is the only place it can be answered.

### Tier 1 — `tier1_gpu.py`

Unzips the gfx1151 archive into an empty folder, starts `llama-server` from it with nothing else on the library search path, loads a small model and talks to it over HTTP.

| ID | Check | Gating |
|---|---|---|
| T1.1 | the server starts and reports itself healthy | yes |
| T1.2 | the GPU actually did the work | yes |
| T1.3 | the reply is non-empty, valid text of a sensible length, with a proper stop reason | yes |
| T1.4 | the same question asked twice with randomness off gives the same answer | no |
| T1.5 | the model reasoned, answered and stopped cleanly | no |
| T1.6 | every ROCm library inside the running server was loaded from the archive | yes |

**T1.2 does not trust the obvious line.** `offloaded 29/29 layers to GPU` is printed from what `-ngl` asked for and appears unchanged on a machine with no GPU. T1.2 instead requires both `using device ROCm0 (...)` and a **non-zero** `ROCm0 model buffer size` — llama.cpp first prints a `0.00 MiB` line from a dry
run before anything is allocated. That is why the server runs with `-lv 4`; without it none of these lines are printed.

**T1.6 catches what static checks cannot.** It reads the running server's loaded-library list (`/proc/<pid>/maps` on Linux, the process module list on Windows). If a bundled library were missing or broken and the machine had its
own ROCm, the server would quietly use that one and every other check would pass; on a user's clean machine it would then fail.

T1.4 and T1.5 describe how one model behaves on one day rather than whether the build is correct, so they are recorded but never block a release.

The machine's kernel, GPU driver version and any system ROCm are recorded with the results, so a later "it broke on my machine" can be compared with the last configuration that passed.

Only gfx1151 has test hardware. Every other archive's record says `hardware_validated: []` rather than implying it was run.

## The build changes the checks depend on

- **One commit, one ROCm per run** (`resolve_build_inputs.py`). The llama.cpp
  commit and ROCm nightly are resolved once in `prepare-matrix` and every leg
  builds exactly those. If the newest nightly is missing for some family, the
  newest version every family has is used and the log says which held it back.
  The release notes have always named a single commit, and Lemonade reads that
  line to decide which model architectures it supports.
- **The tested zip is the published zip** (`package.py`). The build job creates
  the final archive and records its SHA-256; nothing re-packs it later. This
  also keeps the Linux executable bit, which the artifact upload used to drop.
- **One family list** (`families.py`). Build and checks read the same family ->
  gfx target mapping.

## Records attached to each release

- `qualification-llama-<tag>-<os>-rocm-<family>-x64.json` per archive: the gfx
  targets requested, found in the binary and validated on hardware; the exact
  ROCm nightly and llama.cpp commit (and the commit the binary reports); the full
  file list with sizes; the runner environment for Tier 1; and every check's
  status, gating flag, error and details.
- `qualification-summary.json`: the release decision.
- `checksums.txt`: SHA-256 of every archive.

## Running by hand

Everything works against a downloaded release asset; nothing is installed.

```bash
# Static checks (add --run-binaries on the archive's own OS to include T0.8)
python3 .github/qualify-rocm/tier0_static.py \
    --archive llama-b1356-ubuntu-rocm-gfx1151-x64.zip \
    --os ubuntu --gfx-target gfx1151 --output tier0.json

# GPU checks on a machine with a gfx1151 GPU (downloads the test model)
python3 .github/qualify-rocm/tier1_gpu.py \
    --archive llama-b1356-windows-rocm-gfx1151-x64.zip --output tier1.json
```

## Out of scope

- **Performance.** Tracked downstream in Lemonade (`benchmark-regression.yml`) against these same archives.
- **Answer quality.** T1.3 covers "the output is well-formed", which catches a backend producing garbage. Judging whether an answer is *good* needs a different kind of test.
