#!/usr/bin/env python3
"""
Publish the qualification records from one release run to the dashboard feed on the `qualification-data` branch.

Reads the records `aggregate.py` wrote (one qualification-<archive>.json per archive) and upserts them into --data-dir, which is the `qualification/` folder of a checkout of that branch:

  index.json                         one compact row per archive per run, newest first
  reports/<build_id>.json            the full record, for drill-down
  latest/<os>-rocm-<family>.json     the newest full record for that archive

build_id is "<run_id>-<os>-rocm-<gfx_family>".
Publishing the same run again (a re-run) replaces its rows and files rather than duplicating them, so this script is safe to run any number of times.

Every archive the run was meant to build gets a row.
An archive with no record (its build failed / no check ever reported on it) gets a placeholder row and a placeholder report saying so. Therefore, a failing build shows up in the feed instead of silently leaving a gap.

A test the record does not contain is reported as either:
  - "not_applicable": it is never run for this archive -- Tier 1 on every family except gfx1151 (the one family with test hardware), and T0.11 on a run that created no release; or
  - "missing": it should have run and did not.
Neither is folded into "failed"; `qualified` is the verdict.

The contract this writes is FEED.md, copied to the branch as SCHEMA.md.

Usage:
    python3 publish_qualification.py --records-dir ./qualification \
        --data-dir ./data/qualification --operating-systems windows,ubuntu \
        --gfx-targets gfx1151,gfx110X --jobs-file jobs.jsonl --published true \
        --rocm-version 10.3.0a20261102 --upstream-sha <sha>
"""

import argparse
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import families  # noqa: E402
import report  # noqa: E402

SCHEMA_VERSION = 1

# Every test the suite runs, in the order the README lists them.
# A test absent from a record shows up as "missing", so the dashboard always has the same columns to draw.
# A test id not listed here is still published, never dropped.
TIER_TESTS = {
    "tier0": ["T0.1", "T0.2", "T0.3", "T0.5", "T0.6", "T0.7",
              "T0.8", "T0.9", "T0.11", "T0.13"],
    "tier1": ["T1.1", "T1.2", "T1.3", "T1.4", "T1.5", "T1.6"],
}

STATUS_MISSING = "missing"
STATUS_NOT_APPLICABLE = "not_applicable"
FINAL_CHECK = "T0.11"

# build_result is GitHub's conclusion for the archive's build job, in the feed's own vocabulary.
# Any other conclusion (failure, timed_out, startup_failure, ...) is "fail".
BUILD_PASS = "pass"
BUILD_FAIL = "fail"
BUILD_UNKNOWN = "unknown"
BUILD_RESULTS = {"success": BUILD_PASS, "cancelled": "cancelled", "skipped": "skipped"}

# Why a placeholder row has no record, by its build_result.
PLACEHOLDER_REASONS = {
    BUILD_FAIL: "build failed",
    "cancelled": "build cancelled",
    "skipped": "build skipped",
}
PLACEHOLDER_NO_REPORT = "no report"

# build_id becomes a filename on the data branch, so it may only contain characters that cannot escape the folder it is written to.
_SAFE_ID = re.compile(r"^[A-Za-z0-9._-]+$")

# "build-windows (gfx110X, gfx1100;gfx1101;gfx1102;gfx1103, gfx110X-all)": GitHub names a matrix job after its matrix values, the family first.
# Long names are cut short with "...", so only the first value is read.
_BUILD_JOB = re.compile(r"^build-(?P<os>[a-z]+) \((?P<first>[^,)]+)")


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_arg(text):
    return str(text).strip().lower() == "true"


def _test_order(test_id):
    """Sort "T0.10" after "T0.9", and unknown ids after known ones."""
    match = re.match(r"^T(\d+)\.(\d+)$", test_id or "")
    if match:
        return (int(match.group(1)), int(match.group(2)), "")
    return (sys.maxsize, sys.maxsize, test_id or "")


def load_records(records_dir):
    """Return the qualification records in the folder aggregate.py wrote."""
    records = []
    for path in sorted(glob.glob(os.path.join(records_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"::warning::skipping unreadable record {path}: {exc}")
            continue
        if data.get("kind") == "qualification_record":
            records.append(data)
    return records


def load_build_results(jobs_file):
    """Map (os, family) -> conclusion of its build job.

    jobs_file holds one {"name": ..., "conclusion": ...} object per line, as
    `gh api .../jobs --jq '.jobs[] | {name, conclusion}'` prints them.
    """
    results = {}
    if not jobs_file:
        return results
    try:
        with open(jobs_file, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError as exc:
        print(f"::warning::could not read {jobs_file}: {exc}; build_result will be {BUILD_UNKNOWN!r}")
        return results
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            job = json.loads(line)
        except ValueError:
            print(f"::warning::skipping unreadable job line {line!r}")
            continue
        match = _BUILD_JOB.match(job.get("name") or "")
        if not match:
            continue
        family = match.group("first").strip()
        conclusion = job.get("conclusion")
        results[(match.group("os"), family)] = (
            BUILD_RESULTS.get(conclusion, BUILD_FAIL) if conclusion else BUILD_UNKNOWN)
    return results


def make_build_id(run_id, os_name, family):
    if not (run_id and os_name and family):
        raise SystemExit(
            f"::error::cannot form a build_id from run_id={run_id!r} os={os_name!r} "
            f"gfx_family={family!r}")
    build_id = f"{run_id}-{os_name}-rocm-{family}"
    if not _SAFE_ID.match(build_id):
        raise SystemExit(f"::error::refusing unsafe build_id {build_id!r}")
    return build_id


def tier_applies(record, tier):
    """Tier 1 only applies to the family with test hardware, unless it ran anyway."""
    if tier in record.get("tiers", {}) or tier != "tier1":
        return True
    return record.get("build", {}).get("gfx_family") == families.HARDWARE_VALIDATED_FAMILY


def tier_status(record, tier):
    """pass | warn | fail for a tier that reported, otherwise missing or not_applicable."""
    reported = record.get("tiers", {}).get(tier)
    if reported:
        return reported.get("status")
    return STATUS_MISSING if tier_applies(record, tier) else STATUS_NOT_APPLICABLE


def absent_status(record, tier, test_id):
    """What a test the record does not contain is reported as."""
    if not tier_applies(record, tier):
        return STATUS_NOT_APPLICABLE
    # T0.11 only runs in the release job, which assigns the release tag.
    if test_id == FINAL_CHECK and not record.get("build", {}).get("release_tag"):
        return STATUS_NOT_APPLICABLE
    return STATUS_MISSING


def collect_results(record):
    """Per-test status across the known suite, plus any test it does not list."""
    by_id = {}
    for tier in record.get("tiers", {}).values():
        for test in tier.get("tests", []):
            by_id.setdefault(test.get("id"), test)

    results = {test_id: by_id[test_id]["status"] if test_id in by_id
               else absent_status(record, tier, test_id)
               for tier, ids in TIER_TESTS.items() for test_id in ids}
    for test_id in sorted((t for t in by_id if t not in results), key=_test_order):
        results[test_id] = by_id[test_id]["status"]

    blocking = sorted((test_id for test_id, test in by_id.items()
                       if test.get("gating") and test.get("status") == "fail"),
                      key=_test_order)
    return results, blocking


def make_placeholder(os_name, family, build_result, run_id, run_attempt,
                     rocm_version, upstream_sha, generated_at):
    """A record-shaped stand-in for an archive that has no record."""
    targets = families.split_targets(families.gpu_targets(family))
    build = report.build_meta(
        artifact=f"llama-{os_name}-rocm-{family}-x64.zip",
        os_name=os_name,
        gfx_family=family,
        gpu_targets=targets,
        rocm_version=rocm_version,
        upstream_sha=upstream_sha,
        run_id=run_id,
        run_attempt=str(run_attempt) if run_attempt is not None else None,
    )
    return {
        "schema_version": report.SCHEMA_VERSION,
        "kind": "qualification_placeholder",
        "generated_at": generated_at,
        "build": build,
        "overall": report.STATUS_FAIL,
        "published": False,
        "counts": {report.STATUS_PASS: 0, report.STATUS_FAIL: 0,
                   report.STATUS_WARN: 0, report.STATUS_SKIP: 0, "total": 0},
        "tiers": {},
        "qualified": False,
        "blocked_by": [PLACEHOLDER_REASONS.get(build_result, PLACEHOLDER_NO_REPORT)],
        "build_result": build_result,
        # No archive, so no gfx target can be present in it.
        "gfx": {
            "requested": targets,
            "present_in_binary": [],
            "missing_from_binary": targets,
            "hardware_validated": [],
        },
    }


def build_entry(record, build_id, build_result, published, run_url, event, run_attempt):
    """Derive the compact, dashboard-facing index row from a full record."""
    build = record.get("build", {})
    results, blocking = collect_results(record)
    statuses = list(results.values())
    # The record's own flag is set before the release is created, so it is
    # only trusted when the release job also finished successfully.
    published = bool(published and record.get("published") is True)

    # A tag is only shown when its release exists: a tag the release job
    # assigned but never created is handed out again by the next run.
    artifact = build.get("artifact")
    if not published:
        artifact = f"llama-{build.get('os')}-rocm-{build.get('gfx_family')}-x64.zip"

    gfx = record.get("gfx", {})
    return {
        "build_id": build_id,
        "tag": build.get("release_tag") if published else None,
        "upstream_sha": build.get("upstream_sha"),
        "artifact": artifact,
        "os": build.get("os"),
        "gfx_family": build.get("gfx_family"),
        "rocm_version": build.get("rocm_version"),
        "gfx_target": build.get("hardware_gfx_target"),
        "hardware_validated": bool(build.get("hardware_validated")),
        "gfx": {
            "requested": len(gfx.get("requested", [])),
            "present": len(gfx.get("present_in_binary", [])),
            "missing": gfx.get("missing_from_binary", []),
        },
        "generated_at": record.get("generated_at"),
        "run_id": build.get("run_id"),
        # The attempt that published this row, the same for every row of the run.
        "run_attempt": run_attempt if run_attempt is not None else _int(build.get("run_attempt")),
        "run_url": run_url,
        "event": event,
        "build_result": build_result,
        "tests": {
            "passed": statuses.count("pass"),
            "failed": statuses.count("fail"),
            "warned": statuses.count("warn"),
            "skipped": statuses.count("skip"),
            "missing": statuses.count(STATUS_MISSING),
            "not_applicable": statuses.count(STATUS_NOT_APPLICABLE),
            "total": len(statuses),
        },
        "qualified": bool(record.get("qualified")),
        "published": published,
        "overall": record.get("overall"),
        "tier0": tier_status(record, "tier0"),
        "tier1": tier_status(record, "tier1"),
        "blocked_by": record.get("blocked_by", []),
        "blocking_tests": blocking,
        "results": results,
        "report_url": f"reports/{build_id}.json",
    }


def write_json(path, data):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
        handle.write("\n")


def _run_order(run_id):
    """GitHub run ids only grow, so they order runs by when they started.
    generated_at cannot: a re-run writes its records later than a newer run did.
    """
    number = _int(run_id)
    return number if number is not None else -1


def update_latest(path, record):
    """Write the record unless the file already holds one from a newer run.

    Re-running an old run must not roll the status tile back to an older result. A re-run of the same run replaces it.
    """
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as handle:
                current = json.load(handle).get("build", {}).get("run_id")
        except (OSError, ValueError, AttributeError):
            current = None
        if _run_order(current) > _run_order(record.get("build", {}).get("run_id")):
            return False
    write_json(path, record)
    return True


def upsert_index(index_path, repo, entries):
    """Replace-or-append each entry by build_id, newest first."""
    if os.path.exists(index_path):
        with open(index_path, encoding="utf-8") as handle:
            index = json.load(handle)
    else:
        index = {}

    replaced = {entry["build_id"] for entry in entries}
    builds = [b for b in index.get("builds", []) if b.get("build_id") not in replaced]
    builds.extend(entries)
    # Newest run first; the archives within one run in a fixed, readable order.
    builds.sort(key=lambda b: b.get("build_id") or "")
    builds.sort(key=lambda b: _run_order(b.get("run_id")), reverse=True)

    return {
        "schema_version": SCHEMA_VERSION,
        "repo": repo or index.get("repo"),
        "updated_at": max((b.get("generated_at") or "" for b in builds), default="") or None,
        "builds": builds,
    }


def main():
    parser = argparse.ArgumentParser(description="Publish qualification records to the dashboard feed")
    parser.add_argument("--records-dir", required=True,
                        help="folder holding the qualification-*.json records aggregate.py wrote")
    parser.add_argument("--data-dir", required=True,
                        help="the qualification/ folder of the qualification-data branch")
    parser.add_argument("--operating-systems", required=True,
                        help="the operating systems this run built, comma-separated")
    parser.add_argument("--gfx-targets", required=True,
                        help="the families this run built, comma-separated")
    parser.add_argument("--jobs-file",
                        help="this run's jobs, one {name, conclusion} JSON object per line")
    parser.add_argument("--published", default="false",
                        help="'true' if this run's release job created the release")
    parser.add_argument("--rocm-version", help="the ROCm nightly this run pinned")
    parser.add_argument("--upstream-sha", help="the llama.cpp commit this run pinned")
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME"),
                        help="what triggered the run, e.g. schedule or workflow_dispatch")
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"),
                        help="owner/name, used to build run_url")
    parser.add_argument("--server-url", default=os.environ.get("GITHUB_SERVER_URL", "https://github.com"))
    parser.add_argument("--run-id", default=os.environ.get("GITHUB_RUN_ID"))
    parser.add_argument("--run-attempt", default=os.environ.get("GITHUB_RUN_ATTEMPT"))
    args = parser.parse_args()

    oses = families.split_targets(args.operating_systems)
    targets = families.split_targets(args.gfx_targets)
    expected = [(o, f) for o in oses for f in targets]
    published = _bool_arg(args.published)
    run_attempt = _int(args.run_attempt)

    records = load_records(args.records_dir)
    build_results = load_build_results(args.jobs_file)
    if args.jobs_file and not build_results:
        print(f"::warning::no build jobs found in {args.jobs_file}; build_result will be {BUILD_UNKNOWN!r}")

    # Placeholders share the run's timestamp, so every row of a run shows the same time.
    run_time = max((r.get("generated_at") or "" for r in records), default="") or report.now_iso()

    covered = set()
    items = []  # (record, build_id, is_placeholder)
    for record in records:
        build = record.get("build", {})
        key = (build.get("os"), build.get("gfx_family"))
        covered.add(key)
        build_id = make_build_id(build.get("run_id") or args.run_id, *key)
        items.append((record, build_id, False))
    for os_name, family in expected:
        if (os_name, family) in covered:
            continue
        build_result = build_results.get((os_name, family), BUILD_UNKNOWN)
        record = make_placeholder(os_name, family, build_result, args.run_id, run_attempt,
                                  args.rocm_version, args.upstream_sha, run_time)
        items.append((record, make_build_id(args.run_id, os_name, family), True))

    if not items:
        raise SystemExit(f"::error::no qualification records in {args.records_dir} and no "
                         "expected archives; nothing to publish")

    os.makedirs(os.path.join(args.data_dir, "reports"), exist_ok=True)
    os.makedirs(os.path.join(args.data_dir, "latest"), exist_ok=True)

    entries = []
    for record, build_id, is_placeholder in items:
        build = record["build"]
        run_url = (f"{args.server_url}/{args.repo}/actions/runs/{build.get('run_id') or args.run_id}"
                   if args.repo else None)
        build_result = build_results.get((build.get("os"), build.get("gfx_family")), BUILD_UNKNOWN)
        entry = build_entry(record, build_id, build_result, published and not is_placeholder,
                            run_url, args.event, run_attempt)
        entries.append(entry)

        write_json(os.path.join(args.data_dir, "reports", f"{build_id}.json"), record)
        latest = os.path.join(args.data_dir, "latest",
                              f"{build['os']}-rocm-{build['gfx_family']}.json")
        moved = update_latest(latest, record)

        tests = entry["tests"]
        print(f"{build_id}: {tests['passed']}/{tests['total']} passed, "
              f"{tests['failed']} failed, {tests['missing']} missing, "
              f"build={build_result} qualified={entry['qualified']} published={entry['published']}"
              f"{' (placeholder: ' + ', '.join(entry['blocked_by']) + ')' if is_placeholder else ''}"
              f"{'' if moved else ' (latest/ kept: it already holds a newer run)'}")

    index_path = os.path.join(args.data_dir, "index.json")
    index = upsert_index(index_path, args.repo, entries)
    write_json(index_path, index)
    print(f"Published {len(entries)} row(s); {len(index['builds'])} build(s) in the index")


if __name__ == "__main__":
    main()
