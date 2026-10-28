#!/usr/bin/env python3
"""
Shared reporting for the qualification suite.

Every tier emits a JSON *fragment* with an identical schema; `aggregate.py`
merges the fragments for one archive into a single qualification record that is
attached to the GitHub release.

Adapted from lemonade-sdk/llama.cpp (.github/qualify-rocm/report.py). The schema
is the same shape so the two repositories' records read alike; the build
metadata differs because this repository publishes one archive per GPU family
with ROCm bundled inside, rather than one archive per ROCm version.

  - Every test has a stable `id` ("T0.3", "T1.2") that never changes meaning.
    An id is never reused for a different check, here or in llama.cpp.
  - `status` is always one of the STATUS_* values below.
  - Numbers live under `details` as numbers, never strings.
  - `gating` says whether a FAIL blocks the release.

This module is stdlib-only: Tier 1 runs on self-hosted machines where nothing
beyond Python is guaranteed.
"""

import datetime
import json
import os
import time
import traceback

SCHEMA_VERSION = 1

STATUS_PASS = "pass"
STATUS_FAIL = "fail"
STATUS_WARN = "warn"
STATUS_SKIP = "skip"

_SEVERITY = {STATUS_SKIP: 0, STATUS_PASS: 1, STATUS_WARN: 2, STATUS_FAIL: 3}

TIER_NAMES = {
    "tier0": "Static archive verification",
    "tier1": "GPU functional inference",
}


def now_iso():
    """Current UTC time as an ISO-8601 string with a trailing 'Z'."""
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def build_meta(
    artifact,
    os_name=None,
    gfx_family=None,
    gpu_targets=None,
    rocm_version=None,
    upstream_sha=None,
    release_tag=None,
    hardware_validated=False,
    hardware_gfx_target=None,
    run_id=None,
    run_attempt=None,
    runner_env=None,
):
    """Per-archive metadata block shared by every fragment.

    `artifact` is the archive filename, which is the only identifier a user
    filing a bug report will have in front of them.
    """
    build_id = artifact or "local"
    if run_id:
        build_id = f"{build_id}@{run_id}"
    return {
        "build_id": build_id,
        "artifact": artifact,
        "os": os_name,
        # The family this archive serves (e.g. gfx110X) and the exact gfx
        # targets it was compiled for.
        "gfx_family": gfx_family,
        "gpu_targets": gpu_targets,
        # The full TheRock nightly version bundled inside, e.g. 10.3.0a20261027.
        "rocm_version": rocm_version,
        # The full upstream llama.cpp commit. Pinned once per run, so every
        # archive in a release reports the same one.
        "upstream_sha": upstream_sha,
        "release_tag": release_tag,
        "hardware_validated": bool(hardware_validated),
        # The gfx target of the GPU this archive was actually run on, if any.
        "hardware_gfx_target": hardware_gfx_target,
        "run_id": run_id,
        "run_attempt": run_attempt,
        # Tier 1 only: kernel, driver and ROCm state of the machine it ran on,
        # so "it broke on my laptop" can be compared with the last good run.
        "runner_env": runner_env,
    }


class TierReport:
    """Collects test results for one tier and serializes a JSON fragment."""

    def __init__(self, tier, meta):
        if tier not in TIER_NAMES:
            raise ValueError(f"unknown tier {tier!r}")
        self.tier = tier
        self.meta = meta
        self.tests = []
        self._started = time.monotonic()
        self._generated_at = now_iso()

    def add(self, test_id, name, status, gating=True, duration_ms=None,
            error=None, details=None):
        """Record a single test result."""
        if status not in _SEVERITY:
            raise ValueError(f"invalid status {status!r}")
        self.tests.append({
            "id": test_id,
            "name": name,
            "tier": self.tier,
            "status": status,
            "gating": bool(gating),
            "duration_ms": duration_ms,
            "error": error,
            "details": details or {},
        })
        return status

    def run(self, test_id, name, fn, gating=True):
        """Run a check function, timing it and trapping exceptions.

        `fn` returns a status string, or (status, error), or
        (status, error, details). A raised exception is recorded as a FAIL with
        the traceback in details, so one broken check never aborts the tier --
        we always want the full picture of a bad build in one run.
        """
        start = time.monotonic()
        try:
            result = fn()
            if isinstance(result, str):
                status, error, details = result, None, None
            elif len(result) == 2:
                (status, error), details = result, None
            else:
                status, error, details = result
        except Exception as exc:  # noqa: BLE001 - capture everything
            status = STATUS_FAIL
            error = f"{type(exc).__name__}: {exc}"
            details = {"traceback": traceback.format_exc()}
        duration_ms = int((time.monotonic() - start) * 1000)
        return self.add(test_id, name, status, gating=gating,
                        duration_ms=duration_ms, error=error, details=details)

    def counts(self):
        out = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_WARN: 0, STATUS_SKIP: 0,
               "total": len(self.tests)}
        for test in self.tests:
            out[test["status"]] += 1
        return out

    def rollup_status(self):
        """Tier status = worst severity among *gating* tests."""
        worst = STATUS_PASS
        for test in self.tests:
            if not test["gating"]:
                continue
            if _SEVERITY[test["status"]] > _SEVERITY[worst]:
                worst = test["status"]
        return worst

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "tier_fragment",
            "tier": self.tier,
            "tier_name": TIER_NAMES[self.tier],
            "status": self.rollup_status(),
            "generated_at": self._generated_at,
            "duration_ms": int((time.monotonic() - self._started) * 1000),
            "build": self.meta,
            "counts": self.counts(),
            "tests": self.tests,
        }

    def write(self, path):
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)
            handle.write("\n")

    def print_summary(self):
        """Human-readable summary to stdout (the CI log)."""
        rollup = self.rollup_status()
        print(f"\n=== {self.tier} ({TIER_NAMES[self.tier]}): {rollup.upper()} ===")
        for test in self.tests:
            gate = "" if test["gating"] else " (non-gating)"
            line = f"  [{test['status'].upper()}] {test['id']} {test['name']}{gate}"
            if test["error"]:
                line += f" -- {test['error']}"
            print(line)
        counts = self.counts()
        print(f"  totals: {counts[STATUS_PASS]} pass, {counts[STATUS_FAIL]} fail, "
              f"{counts[STATUS_WARN]} warn, {counts[STATUS_SKIP]} skip")


def merge_fragments(fragments, extra_meta=None):
    """Merge per-tier fragments for one archive into one record."""
    fragments = sorted(fragments, key=lambda frag: frag.get("tier", ""))
    meta = {}
    for frag in fragments:
        for key, value in frag.get("build", {}).items():
            # Never let a tier that did not touch hardware clear the flag a
            # tier that did has already set.
            if key == "hardware_validated":
                meta[key] = bool(meta.get(key)) or bool(value)
            elif value is not None or key not in meta:
                meta[key] = value
    if extra_meta:
        meta.update({k: v for k, v in extra_meta.items() if v is not None})

    tiers = {}
    for frag in fragments:
        tier = frag["tier"]
        bucket = tiers.setdefault(tier, {
            "status": STATUS_SKIP,
            "tier_name": frag.get("tier_name"),
            "counts": {},
            "tests": [],
        })
        seen = {t.get("id") for t in bucket["tests"]}
        for test in frag.get("tests", []):
            # Identical ids from a re-run would double-count; keep the first.
            if test.get("id") in seen:
                continue
            bucket["tests"].append(test)
            seen.add(test.get("id"))

    overall = STATUS_PASS
    totals = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_WARN: 0, STATUS_SKIP: 0, "total": 0}
    for tier, bucket in tiers.items():
        counts = {STATUS_PASS: 0, STATUS_FAIL: 0, STATUS_WARN: 0, STATUS_SKIP: 0,
                  "total": len(bucket["tests"])}
        status = STATUS_PASS
        for test in bucket["tests"]:
            counts[test["status"]] += 1
            if test.get("gating") and _SEVERITY[test["status"]] > _SEVERITY[status]:
                status = test["status"]
        bucket["counts"] = counts
        bucket["status"] = status
        if _SEVERITY[status] > _SEVERITY[overall]:
            overall = status
        for key, value in counts.items():
            totals[key] = totals.get(key, 0) + value

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "qualification_record",
        "generated_at": now_iso(),
        "build": meta,
        "overall": overall,
        "published": False,
        "counts": totals,
        "tiers": tiers,
    }
