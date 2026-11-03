# Qualification feed — published contract

Every release run publishes its qualification records to the
**`qualification-data`** branch of this repository, in a fixed layout an
external dashboard can poll. This file is that contract. A copy is kept on the
branch as `qualification/SCHEMA.md`, refreshed on every publish, so the data and
its description never drift apart. Treat it as versioned (`schema_version`).

The layout and field names follow the
[lemonade-sdk/llama.cpp feed](https://github.com/lemonade-sdk/llama.cpp/blob/qualification-data/qualification/SCHEMA.md),
so one dashboard can read both. Where this repository's archives really differ,
the values differ too; [Differences from lemonade-sdk/llama.cpp](#differences-from-lemonade-sdkllamacpp)
lists every one.

## Why a branch and not the releases

The records are already attached to each release, but that copy cannot serve a dashboard:

- A release only exists when every archive passed. The dashboard needs every run, including the ones that published nothing.
- Reading history out of releases means one API call per release, and the Releases API allows only 60 unauthenticated requests an hour.

A file on a branch is one plain HTTP GET with no authentication and no rate limit. The branch is only the transport; the contract below would hold unchanged if the files moved to GitHub Pages, a bucket or a database.

## Layout

```
qualification/
  index.json                          # the feed: one compact row per archive per run
  reports/<build_id>.json             # the full record for that row (drill-down)
  latest/<os>-rocm-<gfx_family>.json  # the newest full record for each archive
  SCHEMA.md                           # a copy of this file
```

Public raw base URL (no authentication, about 5 minutes of CDN caching):

```
https://raw.githubusercontent.com/lemonade-sdk/llamacpp-rocm/qualification-data/qualification/
```

The dashboard's one regular fetch is `.../qualification/index.json`.

## One row per archive

A release run builds fourteen archives — Windows and Linux for each of the GPU
families gfx1151, gfx1150, gfx120X, gfx110X, gfx103X, gfx90a and gfx908 (see
`families.py`) — and each is qualified on its own. Each one gets its own row,
because an archive is what a user downloads and what a bug report names. Rows
from the same run share `run_id`; group on it to show a run as a whole.

Every archive the run was meant to build gets a row, including one whose build
failed (see [Placeholder rows](#placeholder-rows)). A run therefore always has
one row per family per operating system — fourteen today.

## `build_id` and re-runs

```
build_id = "<run_id>-<os>-rocm-<gfx_family>"     e.g. 41449736969-windows-rocm-gfx1151
```

Unique per archive per run. Publishing is an upsert keyed on `build_id`:
re-running a run replaces that run's rows and files instead of adding new ones.
`run_attempt` records which attempt the row came from.

## Which runs appear

Runs of `.github/workflows/build-llamacpp-rocm.yml` that would create a release:
the scheduled nightly, and manual runs with `create_release` ticked that build
every family on both Windows and Linux — the same runs the `create-release` job
accepts. Pass or fail, every one of them is published.

Not in the feed:

- **Pull requests**, manual runs without `create_release`, and manual runs that
  build only some of the families.
- **Re-tests of a published release** (`test-llamacpp-rocm.yml`).
- **A run that was cancelled.**
- **A run whose `prepare-matrix` job failed**, for example because no ROCm
  nightly could be found. Nothing was pinned or built. The nightly-failure
  alert reports these.

## `index.json`

```jsonc
{
  "schema_version": 1,
  "repo": "lemonade-sdk/llamacpp-rocm",
  "updated_at": "2026-11-02T14:19:43Z",       // generated_at of the newest row
  "builds": [                                   // newest run first
    {
      "build_id": "41449736969-windows-rocm-gfx1151",
      "tag": "b1362",                           // this repository's release tag, or null if the run released nothing
      "upstream_sha": "cd8427d018c07eefab6c110f7f115c31ec4c6157",
      "artifact": "llama-b1362-windows-rocm-gfx1151-x64.zip",
      "os": "windows",                          // windows | ubuntu
      "gfx_family": "gfx1151",                  // the GPU family this archive serves
      "rocm_version": "10.3.0a20261102",        // the ROCm nightly bundled inside
      "gfx_target": "gfx1151",                  // the target tested on real hardware, or null
      "hardware_validated": true,               // did Tier 1 run on a GPU
      "gfx": { "requested": 1, "present": 1, "missing": [] },
      "generated_at": "2026-11-02T14:19:43Z",   // ISO-8601 UTC, the time axis
      "run_id": "41449736969",
      "run_attempt": 1,
      "run_url": "https://github.com/lemonade-sdk/llamacpp-rocm/actions/runs/41449736969",
      "event": "schedule",                      // schedule | workflow_dispatch
      "build_result": "success",                // how this archive's build job ended
      "tests": { "passed": 14, "failed": 0, "warned": 0, "skipped": 2, "missing": 0, "total": 16 },
      "qualified": true,                        // this archive passed its checks
      "published": true,                        // this archive shipped in this run's release
      "overall": "pass",                        // pass | warn | fail
      "blocked_by": [],                         // e.g. ["tier0=fail"]
      "blocking_tests": [],                     // e.g. ["T0.3"]
      "results": {
        "T0.1": "pass", "T0.2": "pass", "T0.3": "pass", "T0.5": "pass",
        "T0.6": "skip", "T0.7": "skip", "T0.8": "pass", "T0.9": "pass",
        "T0.11": "pass", "T0.13": "pass",
        "T1.1": "pass", "T1.2": "pass", "T1.3": "pass", "T1.4": "pass",
        "T1.5": "pass", "T1.6": "pass"
      },
      "report_url": "reports/41449736969-windows-rocm-gfx1151.json"
    }
  ]
}
```

### Field notes

- **`results`** has one entry per test: T0.1, T0.2, T0.3, T0.5, T0.6, T0.7,
  T0.8, T0.9, T0.11, T0.13 and T1.1–T1.6, as listed in [README.md](README.md).
  Values are `pass | fail | warn | skip | missing`. A test that did not run at
  all is `missing`. A test id added to the suite later appears here as soon as
  it runs, so the failure-frequency and heatmap views can be built from
  `index.json` alone.
- **`tests`** counts the values in `results`, so
  `passed + failed + warned + skipped + missing == total` always holds.
- **`missing` is not `failed`.** Tier 1 runs only on the gfx1151 archives, the
  one family with test hardware. Every other archive has all six T1 tests
  `missing` and `hardware_validated: false`, and still qualifies. Showing that
  as a failure would paint a shipped archive red. Use `qualified`, not the
  counts, to decide pass or fail. The Windows archives also report T0.6 and
  T0.7 as `skip`: both are Linux-only checks.
- **`qualified`** is the verdict for this one archive: Tier 0 ran and did not
  fail; T0.11 passed (only when the run created a release, see below); and, for
  gfx1151, Tier 1 ran and did not fail. A `warn` does not block, and tests marked
  non-gating in README.md never affect it.
- **`published`** says whether this archive shipped. The release is all or
  nothing: it is created only when every archive of the run qualifies. So an archive
  can have `qualified: true` and `published: false` because another archive
  failed. Within a run, `published` is the same on every row. It is `true` only
  when the run's `create-release` job finished successfully. The record in
  `reports/` carries its own `published` flag, written just before the release
  is created; when the two disagree, trust this one.
- **`tag`** is this repository's release tag (`b1362`), which is not the
  llama.cpp build number. It only exists when the run created a release; on
  every other run it is `null`, and `artifact` is the name the build job gave
  the archive (`llama-windows-rocm-gfx1151-x64.zip`), without a tag.
- **T0.11** (the published archive is byte-for-byte the tested one) runs only
  inside the release job. On a run that created no release it is `missing`, and
  it does not count against `qualified`.
- **`build_result`** is how the archive's build job ended, as GitHub reports it:
  `success`, `failure`, `cancelled`, `skipped`, `timed_out`, …, or `unknown`
  when the job list could not be read.
- **`blocked_by`** gives the reasons the archive did not qualify: `tier0=fail`,
  `tier0 missing`, `T0.11 missing`, `tier1=fail`, `tier1 missing`, and on
  placeholder rows `build failed`, `build cancelled`, `build skipped` or
  `no report`. **`blocking_tests`** lists the gating tests that failed. Both
  are empty when the archive qualified.
- **`gfx`** summarises the check that every gfx target of the family was
  compiled into the GPU backend (T0.3): how many were requested, how many are
  present, and the names of any that are missing.
- **`gfx_target` / `hardware_validated`** say which single target was actually
  tested on a GPU. Every other target in `gfx.requested` was only checked for
  presence, never run. Don't present those as tested.
- **`rocm_version`** is the full TheRock nightly version bundled inside the
  archive. One nightly is pinned per run, so every row of a run has the same
  value.
- **`report_url`** is relative to the base URL; it holds every check's
  `status`, `gating`, `error` and `details`.

### Placeholder rows

An archive with no record at all gets a placeholder row. This happens when its
build failed, so there was nothing to check, or when no check reported on it.
In a placeholder row:

- every test is `missing`, `qualified` and `published` are `false`, and
  `overall` is `fail`;
- `blocked_by` says why: `build failed` (also for a build that timed out),
  `build cancelled`, `build skipped`, or `no report` when the build succeeded or
  its result is unknown;
- `gfx.present` is `0` and every requested target is in `gfx.missing`, because
  there is no binary;
- `tag`, `gfx_target` are `null` and `hardware_validated` is `false`.

## `reports/<build_id>.json`

The `qualification-<archive>.json` record exactly as `aggregate.py` wrote it
(`kind: "qualification_record"`): the `build` metadata, `overall`, `qualified`,
`blocked_by`, `published`, raw `counts`, the `gfx` audit, the `versions` it was
built from, the `bundle` file list, and `tiers` with every test. When the run
created a release, the same content is attached to it. Its own `build.build_id`
(`<artifact>@<run_id>`) differs from the feed's `build_id`; use the feed's.

For a placeholder row it is a placeholder (`kind: "qualification_placeholder"`)
with the same top-level fields: `build` metadata, `overall: "fail"`,
`qualified: false`, `published: false`, `blocked_by`, `build_result`, the `gfx`
block, zero `counts` and empty `tiers`.

## `latest/<os>-rocm-<gfx_family>.json`

The newest full record for one archive (for example
`latest/windows-rocm-gfx1151.json`), for status tiles that want the current state
without scanning the index. A placeholder is written here too, so a tile shows
a failing build instead of the last good one. It is replaced only by a newer
record, so re-running an old run cannot move it backwards.

## Differences from lemonade-sdk/llama.cpp

That feed has one archive per ROCm version; this one has one archive per GPU
family, with ROCm bundled inside. Everything else in the row means the same.

| Field | lemonade-sdk/llama.cpp | this repository |
|---|---|---|
| `build_id` | `<run_id>-<os>-rocm-<rocm_version>` | `<run_id>-<os>-rocm-<gfx_family>` |
| `tag` | upstream llama.cpp build number | this repository's release tag, or `null` |
| `os` | `win` \| `ubuntu` | `windows` \| `ubuntu` |
| `rocm_version` | `7.14` \| `10.0` \| `10.1`, one per archive | full nightly, e.g. `10.3.0a20261102`, same for the whole run |
| `gfx_family` | — | the family the archive serves |
| `version_qualified` | both archives of a ROCm version qualified | not present; see `published` |
| `published` | — | the archive shipped in this run's release |
| `build_result` | — | how the archive's build job ended |
| `results` keys | T0.1–T0.12, T1.1–T1.5 | T0.1–T0.3, T0.5–T0.9, T0.11, T0.13, T1.1–T1.6 |
| failed build | no row | a placeholder row |
| `latest/` file | `<os>-rocm-<rocm_version>.json` | `<os>-rocm-<gfx_family>.json` |

## Versioning and growth

Bump `schema_version` on any breaking change to `index.json` or the row shape.
Adding a field is not breaking. The dashboard should read `schema_version` and
show a notice, rather than break, on a version it does not know.

`index.json` grows by fourteen rows per nightly, about five thousand a year.
That is comfortable to poll for a long time, especially compressed. If it ever
is not, older rows will move into per-month files under a new `schema_version`.
