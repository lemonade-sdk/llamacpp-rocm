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

A release run builds twenty archives — Windows and Linux for each of the GPU
families gfx1151, gfx1152, gfx1153, gfx1150, gfx117X, gfx120X, gfx110X, gfx103X,
gfx90a and gfx908 (see `families.py`) — and each is qualified on its own. Each
one gets its own row, because an archive is what a user downloads and what a bug
report names. Rows from the same run share `run_id`; group on it to show a run
as a whole.

Every archive the run was meant to build gets a row, including one whose build
failed (see [Placeholder rows](#placeholder-rows)). A run therefore always has
one row per family per operating system — twenty today.

## `build_id` and re-runs

```
build_id = "<run_id>-<os>-rocm-<gfx_family>"     e.g. 41449736969-windows-rocm-gfx1151
```

Unique per archive per run. Publishing is an upsert keyed on `build_id`.
A re-run of a run keeps its `run_id`, so it replaces that run's rows and its
`reports/` files instead of adding new ones; the latest attempt's values win.
`run_attempt` records which attempt published the rows.
A new run has a new `run_id` and adds new rows.

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

## Example `index.json`

```jsonc
{
  "schema_version": 1,
  "repo": "lemonade-sdk/llamacpp-rocm",
  "updated_at": "2026-11-03T13:21:07Z",       // the most recent generated_at of any row
  "builds": [                                   // newest run (highest run_id) first
    // ... the other rows of run 41500000001 ...

    // An archive that was built and checked, and failed a Tier 0 check.
    {
      "build_id": "41500000001-windows-rocm-gfx110X",
      "tag": null,                              // no release was created by this run
      "upstream_sha": "e1f7a2c9b04d5d3c6a8f0b2e4d6c8a0b1c3d5e7f",
      "artifact": "llama-windows-rocm-gfx110X-x64.zip",   // the build job's name for it: no tag
      "os": "windows",
      "gfx_family": "gfx110X",
      "rocm_version": "10.3.0a20261103",
      "rocm_newest_nightly": "10.3.0a20261104", // newer than rocm_version: this run fell back
      "rocm_held_back_by": ["ubuntu/gfx1150", "windows/gfx1150"],   // lacked 10.3.0a20261104
      "gfx_target": null,                       // Tier 1 did not run on this archive
      "hardware_validated": false,
      "gfx": { "requested": 4, "present": 4, "missing": [] },
      "generated_at": "2026-11-03T13:21:07Z",
      "run_id": "41500000001",
      "run_attempt": 1,
      "run_url": "https://github.com/lemonade-sdk/llamacpp-rocm/actions/runs/41500000001",
      "event": "schedule",
      "build_result": "pass",
      "tests": { "passed": 6, "failed": 1, "warned": 0, "skipped": 2, "missing": 0, "not_applicable": 7, "total": 16 },
      "qualified": false,                       // a gating Tier 0 check failed
      "published": false,
      "overall": "fail",
      "tier0": "fail",
      "tier1": "not_applicable",                // Tier 1 only runs on gfx1151
      "blocked_by": ["tier0=fail"],
      "blocking_tests": ["T0.3"],
      "results": {
        "T0.1": "pass", "T0.2": "pass", "T0.3": "fail", "T0.5": "pass",
        "T0.6": "skip", "T0.7": "skip", "T0.8": "pass", "T0.9": "pass",
        "T0.11": "not_applicable", "T0.13": "pass",   // T0.11 only runs when a release is made
        "T1.1": "not_applicable", "T1.2": "not_applicable", "T1.3": "not_applicable",
        "T1.4": "not_applicable", "T1.5": "not_applicable", "T1.6": "not_applicable"
      },
      "report_url": "reports/41500000001-windows-rocm-gfx110X.json"
    },

    // A placeholder: this archive's build failed, so there was nothing to check.
    {
      "build_id": "41500000001-ubuntu-rocm-gfx908",
      "tag": null,
      "upstream_sha": "e1f7a2c9b04d5d3c6a8f0b2e4d6c8a0b1c3d5e7f",
      "artifact": "llama-ubuntu-rocm-gfx908-x64.zip",
      "os": "ubuntu",
      "gfx_family": "gfx908",
      "rocm_version": "10.3.0a20261103",
      "rocm_newest_nightly": "10.3.0a20261104", // the same for every row of a run
      "rocm_held_back_by": ["ubuntu/gfx1150", "windows/gfx1150"],
      "gfx_target": null,
      "hardware_validated": false,
      "gfx": { "requested": 1, "present": 0, "missing": ["gfx908"] },   // no archive, so nothing is present
      "generated_at": "2026-11-03T13:21:07Z",
      "run_id": "41500000001",
      "run_attempt": 1,
      "run_url": "https://github.com/lemonade-sdk/llamacpp-rocm/actions/runs/41500000001",
      "event": "schedule",
      "build_result": "fail",                   // the build job failed
      "tests": { "passed": 0, "failed": 0, "warned": 0, "skipped": 0, "missing": 9, "not_applicable": 7, "total": 16 },
      "qualified": false,
      "published": false,
      "overall": "fail",
      "tier0": "missing",                       // should have run, but there was no archive
      "tier1": "not_applicable",
      "blocked_by": ["build failed"],
      "blocking_tests": [],
      "results": {
        "T0.1": "missing", "T0.2": "missing", "T0.3": "missing", "T0.5": "missing",
        "T0.6": "missing", "T0.7": "missing", "T0.8": "missing", "T0.9": "missing",
        "T0.11": "not_applicable", "T0.13": "missing",
        "T1.1": "not_applicable", "T1.2": "not_applicable", "T1.3": "not_applicable",
        "T1.4": "not_applicable", "T1.5": "not_applicable", "T1.6": "not_applicable"
      },
      "report_url": "reports/41500000001-ubuntu-rocm-gfx908.json"
    },

    // ... the rows of earlier runs ...

    // A fully passing gfx1151 archive from a run that created release b1362.
    {
      "build_id": "41449736969-windows-rocm-gfx1151",
      "tag": "b1362",                           // this repository's release tag; set only when that release exists
      "upstream_sha": "cd8427d018c07eefab6c110f7f115c31ec4c6157",
      "artifact": "llama-b1362-windows-rocm-gfx1151-x64.zip",
      "os": "windows",                          // windows | ubuntu
      "gfx_family": "gfx1151",                  // the GPU family this archive serves
      "rocm_version": "10.3.0a20261102",        // the ROCm nightly bundled inside
      "rocm_newest_nightly": "10.3.0a20261102", // the newest nightly any family had; null when the run pinned a version
      "rocm_held_back_by": [],                  // "<os>/<family>" lacking that newest nightly; empty: no fallback
      "gfx_target": "gfx1151",                  // the one gfx target Tier 1 ran on; null when Tier 1 did not run
      "hardware_validated": true,               // Tier 1 ran on a real GPU (see tier1 for how it went)
      "gfx": { "requested": 1, "present": 1, "missing": [] },
      "generated_at": "2026-11-02T14:19:43Z",   // ISO-8601 UTC, when this row's record was written
      "run_id": "41449736969",
      "run_attempt": 1,                         // the attempt that published this row
      "run_url": "https://github.com/lemonade-sdk/llamacpp-rocm/actions/runs/41449736969",
      "event": "schedule",                      // schedule | workflow_dispatch
      "build_result": "pass",                   // pass | fail | cancelled | skipped | unknown
      "tests": { "passed": 14, "failed": 0, "warned": 0, "skipped": 2, "missing": 0, "not_applicable": 0, "total": 16 },
      "qualified": true,                        // passed every gating check required of it: Tier 0 (and T0.11 when
                                                // a release was made), plus Tier 1 for gfx1151. Not whether it shipped.
      "published": true,                        // shipped in this run's release
      "overall": "pass",                        // pass | warn | fail; fail exactly when qualified is false
      "tier0": "pass",                          // pass | warn | fail | missing
      "tier1": "pass",                          // pass | warn | fail | missing | not_applicable
      "blocked_by": [],                         // why it did not qualify, e.g. ["tier0=fail"]
      "blocking_tests": [],                     // the gating tests that failed, e.g. ["T0.3"]
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

A passing archive of any other family looks like the gfx1151 row, except that:
- `gfx_target` is `null`,
- `hardware_validated` is `false`,
- `tier1` and T1.1–T1.6 are `not_applicable`, and
- `tests.not_applicable` is 6.

### Field notes

- **`results`** has one entry per test: T0.1, T0.2, T0.3, T0.5, T0.6, T0.7,
  T0.8, T0.9, T0.11, T0.13 and T1.1–T1.6, as listed in [README.md](README.md).
  A test id added to the suite later appears here as soon as it runs, so the
  failure-frequency and heatmap views can be built from `index.json` alone.
  The values:
  - `pass`: the check ran and passed.
  - `fail`: the check ran and found a problem. It blocks the archive only if
    the test is gating; T1.4 and T1.5 never block.
  - `warn`: the check ran and found something worth recording that does not
    block, e.g. no `.kpack` kernel packs (T0.13) or programs without the
    executable bit (T0.11).
  - `skip`: the check ran but could not apply, e.g. T0.6 and T0.7 on Windows
    (Linux-only checks), or T1.2–T1.6 when the server never started (T1.1
    then fails).
  - `missing`: the check should have run for this archive and did not. This
    always means something went wrong.
  - `not_applicable`: the check is never run for this archive: T1.1–T1.6 on
    every family except gfx1151 (the one family with test hardware), and T0.11
    on a run that created no release.
- **`tests`** counts the values in `results`, so
  `passed + failed + warned + skipped + missing + not_applicable == total`
  always holds. Use `qualified`, not the counts, to decide pass or fail.
- **`qualified`** is the verdict for this one archive: it passed every gating
  check required of it. That is Tier 0, plus T0.11 when the run created a
  release, plus Tier 1 for gfx1151. A `warn` does not block, and non-gating
  tests never affect it. It does not say whether the archive shipped
  (`published`) or ran on a GPU (`tier1`, `hardware_validated`).
- **`overall`** is `fail` exactly when `qualified` is `false`. Otherwise it is
  the worst result of the gating tests that ran: `warn` if any of them warned,
  else `pass`.
- **`tier0` / `tier1`** are each tier's result: `pass`, `warn` or `fail` when
  the tier ran; `missing` when it should have run and did not; and, for
  `tier1`, `not_applicable` on every family except gfx1151.
- **`published`** says whether this archive shipped. The release is all or
  nothing: it is created only when every archive of the run qualifies. So an
  archive can have `qualified: true` and `published: false` because another
  archive failed. Within a run, `published` is the same on every row. It is
  `true` only when the run's `create-release` job finished successfully. The
  record in `reports/` carries its own `published` flag, written just before
  the release is created; when the two disagree, trust this one.
- **`tag`** is this repository's release tag (`b1362`), which is not the
  llama.cpp build number. It is set only when `published` is `true`. Otherwise
  it is `null`, even if the release job had picked a tag (that tag was never
  created, and the next run reuses it), and `artifact` is the name the build
  job gave the archive (`llama-windows-rocm-gfx1151-x64.zip`), without a tag.
- **`build_result`** is how the archive's build job ended: `pass`, `fail`
  (including a timeout), `cancelled`, `skipped`, or `unknown` when the run's
  job list could not be read.
- **`run_attempt`** is the attempt of the run that published the row. It is the
  same on every row of a run.
- **`generated_at`** is when the row's record was written. A re-run writes it
  again, later than any run that started after it, so order runs by `run_id`
  (rows are already newest-run-first) rather than by `generated_at`.
- **`blocked_by`** gives the reasons the archive did not qualify. It is empty
  when it qualified. The values:
  - `tier0=fail`: a gating Tier 0 check failed (a T0.11 failure counts here);
  - `tier0 missing`: no Tier 0 report;
  - `T0.11 missing`: the release job did not run T0.11 on it;
  - `tier1=fail`: a gating Tier 1 check failed;
  - `tier1 missing`: gfx1151 had no Tier 1 report;
  - on placeholder rows only: `build failed`, `build cancelled`, `build skipped`
    or `no report`.
- **`blocking_tests`** lists the gating tests that failed; empty when none did.
- **`gfx`** summarises the check that every gfx target of the family was
  compiled into the GPU backend (T0.3): how many were requested, how many are
  present, and the names of any that are missing.
- **`gfx_target`** is the one gfx target Tier 1 actually ran on, and `null` when
  Tier 1 did not run. Today that is gfx1151 on the gfx1151 rows and `null`
  everywhere else. **`hardware_validated`** is `true` when Tier 1 ran on a real
  GPU, whether or not it then passed; `tier1` gives the outcome. Every target
  in `gfx.requested` other than `gfx_target` was only checked for presence,
  never run. Don't present those as tested.
- **`rocm_version`** is the full TheRock nightly version bundled inside the
  archive. One nightly is pinned per run, so every row of a run has the same
  value.
- **`rocm_newest_nightly`** and **`rocm_held_back_by`** say whether the run fell
  back to an older ROCm nightly. With `rocm_version: latest` the run uses the
  newest nightly published for every family it builds; when the newest one is
  missing for some family, it uses the newest they all share instead.
  `rocm_newest_nightly` is the newest nightly any family had, and
  `rocm_held_back_by` lists the `"<os>/<family>"` pairs that lacked it, e.g.
  `["windows/gfx1150"]`. Without a fallback, `rocm_newest_nightly` equals
  `rocm_version` and `rocm_held_back_by` is `[]`. A run that pinned a version
  looks for no newer one: `rocm_newest_nightly` is `null` and
  `rocm_held_back_by` is `[]`. Both are the same on every row of a run. Rows
  published before these fields existed do not have them; treat that as
  unknown, not as "no fallback".
- **`report_url`** is relative to the base URL; it holds every check's
  `status`, `gating`, `error` and `details`.

### Placeholder rows

An archive with no record at all gets a placeholder row. This happens when its
build failed, so there was nothing to check, or when no check reported on it.
In a placeholder row:

- every test that applies is `missing` (the others stay `not_applicable`),
  `tier0` is `missing`, `qualified` and `published` are `false`, and `overall`
  is `fail`;
- `blocked_by` follows `build_result`: `build failed`, `build cancelled`,
  `build skipped`, or `no report` when the build passed or its result is
  `unknown`;
- `gfx.present` is `0` and every requested target is in `gfx.missing`, because
  there is no binary;
- `tag` and `gfx_target` are `null`, and `hardware_validated` is `false`.

The second row of the [`index.json`](#indexjson) example is a placeholder.

## `reports/<build_id>.json`

The `qualification-<archive>.json` record exactly as `aggregate.py` wrote it (`kind: "qualification_record"`):
- the `build` metadata,
- `overall`, `qualified`,
- `blocked_by`, `published`,
- raw `counts`,
- the `gfx` audit,
- the `versions` it was built from,
- the `bundle` file list, and
- `tiers` with every test.

When the run created a release, the same content is attached to it. Its own `build.build_id` (`<artifact>@<run_id>`) differs from the feed's `build_id`; use the feed's.

For a placeholder row it is a placeholder (`kind: "qualification_placeholder"`) with the same top-level fields:
- `build` metadata,
- `overall: "fail"`,
- `qualified: false`,
- `published: false`,
- `blocked_by`,
- `build_result` (in the feed's vocabulary),
- the `gfx` block,
- zero `counts` and
- empty `tiers`.

## `latest/<os>-rocm-<gfx_family>.json`

The newest full record for one archive (for example
`latest/windows-rocm-gfx1151.json`), for status tiles that want the current state
without scanning the index. A placeholder is written here too, so a tile shows
a failing build instead of the last good one. It is replaced only by a record
from the same run or a newer one (a higher `run_id`), so re-running an old run
cannot move it backwards.

## Differences from lemonade-sdk/llama.cpp

That feed has one archive per ROCm version, with ROCm installed separately by the user. This one has one archive per GPU family, with ROCm bundled inside.

Everything else in the row means the same:
- `published`,
- `build_result`,
- `tier0`, `tier1`, `not_applicable`,
- placeholder rows,
- `overall` being `fail` exactly when `qualified` is `false`,
- `run_attempt` being the publishing attempt, and
- runs ordered by `run_id`.

| Field | this repository | lemonade-sdk/llama.cpp |
|---|---|---|
| `schema_version` | `1` | `2` (same row shape) |
| `build_id` | `<run_id>-<os>-rocm-<gfx_family>` | `<run_id>-<os>-rocm-<rocm_version>` |
| rows per run | 20 | 6 |
| `tag` | this repository's release tag; `null` unless `published` | llama.cpp build number; set on every row |
| `artifact` | the untagged CI name unless `published` | always the release name |
| `os` | `windows` \| `ubuntu` | `win` \| `ubuntu` |
| `rocm_version` | full nightly, e.g. `10.3.0a20261102`, same for the whole run | `7.14` \| `10.0` \| `10.1`, one per archive |
| `rocm_newest_nightly`, `rocm_held_back_by` | whether the run fell back to an older nightly, same for the whole run | not present |
| `gfx_family` | the GPU family the archive serves | — |
| `version_qualified` | not present (a release here is all or nothing) | both archives of a ROCm version exist and qualified |
| `tier1` | Tier 1 runs only on gfx1151 (`not_applicable` elsewhere); `missing` blocks | Tier 1 runs on every archive; `missing` does not block |
| `not_applicable` | T1.x outside gfx1151; T0.11 on a run with no release | T0.10, T0.11 on an archive dropped before the release checks; T0.10 is `skip` on Linux |
| `blocked_by` | also `tier1 missing`, `T0.11 missing` | no `tier1 missing`, no `T0.11 missing` |
| `results` keys | T0.1–T0.3, T0.5–T0.9, T0.11, T0.13, T1.1–T1.6 | T0.1–T0.8, T0.10–T0.12, T1.1–T1.5 (T0.9 retired; older rows still carry T0.9) |
| placeholder `gfx` | requested targets, all in `missing` | all zero |
| `latest/` file | `<os>-rocm-<gfx_family>.json` | `<os>-rocm-<rocm_version>.json` |

## Versioning and growth

Bump `schema_version` on any breaking change to `index.json` or the row shape.
Adding a field is not breaking. The dashboard should read `schema_version` and
show a notice, rather than break, on a version it does not know.

`index.json` grows by twenty rows per nightly, about seven thousand a year.
That is comfortable to poll for a long time, especially compressed. If it ever
is not, older rows will move into per-month files under a new `schema_version`.
