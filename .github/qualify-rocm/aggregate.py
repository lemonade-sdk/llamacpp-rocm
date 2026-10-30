#!/usr/bin/env python3
"""
Merge tier reports into one qualification record per archive, decide whether the
release may be published, and write the job summary.

Reports are grouped by (OS, family), because that pair identifies exactly one
release asset -- the unit a user downloads and the unit a bug report names.

The release is all or nothing. So one archive failing withholds the whole release.

An archive qualifies when:
  - Tier 0 ran and none of its gating checks failed, including T0.11 (the
    published archive is the tested archive);
  - Tier 1, where it is required, ran and none of its gating checks failed.

Tier 1 runs only on the gfx1151 archives, the one family with test hardware.
Every other record says so (`hardware_validated: []`) rather than implying it
was tested too.

Usage:
    python3 aggregate.py --fragments-dir fragments --tag b1357 \
        --records-dir qualification --operating-systems windows,ubuntu \
        --gfx-targets gfx1151,gfx110X --require-tier1 true --fail-on-not-publishable
"""

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import families  # noqa: E402
import report  # noqa: E402


def load_fragments(fragments_dir):
    fragments = []
    for path in sorted(glob.glob(os.path.join(fragments_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError) as exc:
            print(f"::warning::skipping unreadable fragment {path}: {exc}")
            continue
        if data.get("kind") == "tier_fragment":
            fragments.append(data)
    return fragments


def find_test(record, test_id):
    for tier in record.get("tiers", {}).values():
        for test in tier.get("tests", []):
            if test.get("id") == test_id:
                return test
    return None


def decide(record, tier1_required):
    """Return (qualified, reasons) for one archive's merged record."""
    reasons = []
    tiers = record.get("tiers", {})
    if "tier0" not in tiers:
        reasons.append("tier0 missing")
    elif tiers["tier0"]["status"] == report.STATUS_FAIL:
        reasons.append("tier0=fail")
    if find_test(record, "T0.11") is None:
        reasons.append("T0.11 missing")
    if "tier1" in tiers:
        if tiers["tier1"]["status"] == report.STATUS_FAIL:
            reasons.append("tier1=fail")
    elif tier1_required:
        reasons.append("tier1 missing")
    return not reasons, reasons


def summarise(record):
    """Lift the facts a reader looks for first to the top of the record."""
    audit = (find_test(record, "T0.3") or {}).get("details", {})
    tier1 = record.get("tiers", {}).get("tier1")
    validated = []
    target = record["build"].get("hardware_gfx_target")
    if tier1 and tier1["status"] != report.STATUS_FAIL and target:
        validated = [target]
    record["gfx"] = {
        "requested": audit.get("expected", []),
        "present_in_binary": audit.get("present", []),
        "missing_from_binary": audit.get("missing", []),
        "hardware_validated": validated,
    }

    provenance = (find_test(record, "T0.9") or {}).get("details", {})
    record["versions"] = {
        "rocm_nightly": record["build"].get("rocm_version"),
        "llamacpp_commit": record["build"].get("upstream_sha"),
        "binary_reports_commit": provenance.get("binary_commit"),
        "binary_reports_build": provenance.get("binary_build_number"),
    }

    # The full file list is large, so it is kept once, here, rather than inside
    # T0.1's details as well.
    contents = find_test(record, "T0.1")
    if contents and "manifest" in contents.get("details", {}):
        details = contents["details"]
        record["bundle"] = {"files": details.get("files"), "bytes": details.get("bytes"),
                            "manifest": details.pop("manifest")}


def write_step_summary(records, missing, publishable):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    lines = ["## Release qualification", "",
             f"**Release publishable: {'YES' if publishable else 'NO'}**", "",
             "| Archive | Tier 0 | Tier 1 | gfx in binary | HW validated | Qualified |",
             "|---|---|---|---|---|---|"]
    for record in records:
        tiers = record.get("tiers", {})
        gfx = record["gfx"]
        t0 = tiers.get("tier0", {}).get("status", "missing").upper()
        t1 = tiers.get("tier1", {}).get("status", "not run").upper()
        hw = ", ".join(gfx["hardware_validated"]) or "none"
        lines.append(
            f"| `{record['build'].get('artifact')}` | {t0} | {t1} | "
            f"{len(gfx['present_in_binary'])}/{len(gfx['requested'])} | {hw} | "
            f"{'yes' if record['qualified'] else 'NO'} |")
    for os_name, family in missing:
        lines.append(f"| {os_name} {family} | no report | | | | NO |")

    problems = []
    for record in records:
        for tier in sorted(record.get("tiers", {})):
            for test in record["tiers"][tier].get("tests", []):
                if test["status"] in (report.STATUS_FAIL, report.STATUS_WARN):
                    gate = "" if test.get("gating") else " _(non-gating)_"
                    problems.append(
                        f"- `{record['build'].get('artifact')}` `{test['id']}` "
                        f"**{test['status']}**{gate} — {test['name']}: "
                        f"{test.get('error') or ''}")
    if problems:
        lines += ["", "### Findings", *problems]

    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main():
    parser = argparse.ArgumentParser(description="Aggregate tier fragments into release records")
    parser.add_argument("--fragments-dir", required=True)
    parser.add_argument("--tag", required=True, help="the release tag, e.g. b1357")
    parser.add_argument("--records-dir", required=True,
                        help="write one qualification-<archive>.json per archive here")
    parser.add_argument("--operating-systems", required=True,
                        help="the operating systems this run built, comma-separated")
    parser.add_argument("--gfx-targets", required=True,
                        help="the families this run built, comma-separated")
    parser.add_argument("--require-tier1", default="false",
                        help="'true' if Tier 1 was scheduled for the gfx1151 archives")
    parser.add_argument("--fail-on-not-publishable", action="store_true")
    args = parser.parse_args()

    oses = families.split_targets(args.operating_systems)
    targets = families.split_targets(args.gfx_targets)
    expected = [(o, f) for o in oses for f in targets]
    tier1_required = {(o, families.HARDWARE_VALIDATED_FAMILY) for o in oses} \
        if args.require_tier1.lower() == "true" else set()

    grouped = {}
    for fragment in load_fragments(args.fragments_dir):
        build = fragment.get("build", {})
        grouped.setdefault((build.get("os"), build.get("gfx_family")), []).append(fragment)

    os.makedirs(args.records_dir, exist_ok=True)
    records, missing = [], []
    for os_name, family in expected:
        group = grouped.get((os_name, family))
        if not group:
            missing.append((os_name, family))
            print(f"::error::No qualification report at all for {os_name} {family}.")
            continue
        name = f"llama-{args.tag}-{os_name}-rocm-{family}-x64.zip"
        record = report.merge_fragments(group, extra_meta={"artifact": name,
                                                           "release_tag": args.tag})
        qualified, reasons = decide(record, (os_name, family) in tier1_required)
        record["qualified"] = qualified
        record["blocked_by"] = reasons
        summarise(record)
        records.append(record)
        print(f"{name}: overall={record['overall']} qualified={qualified} "
              f"blocked_by={reasons} "
              f"gfx={len(record['gfx']['present_in_binary'])}/{len(record['gfx']['requested'])} "
              f"hw_validated={record['gfx']['hardware_validated'] or 'none'}")

    publishable = not missing and all(r["qualified"] for r in records)
    for record in records:
        record["published"] = publishable
        stem = record["build"]["artifact"][: -len(".zip")]
        out = os.path.join(args.records_dir, f"qualification-{stem}.json")
        with open(out, "w", encoding="utf-8") as handle:
            json.dump(record, handle, indent=2)
            handle.write("\n")
        print(f"Wrote {out}")

    summary = {
        "tag": args.tag,
        "publishable": publishable,
        "missing_reports": [f"{o}-{f}" for o, f in missing],
        "archives": [{"artifact": r["build"].get("artifact"),
                      "qualified": r["qualified"],
                      "overall": r["overall"],
                      "blocked_by": r["blocked_by"],
                      "hardware_validated": r["gfx"]["hardware_validated"]}
                     for r in records],
    }
    with open(os.path.join(args.records_dir, "qualification-summary.json"), "w",
              encoding="utf-8") as handle:
        json.dump(summary, handle, indent=2)
        handle.write("\n")

    write_step_summary(records, missing, publishable)

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"publishable={'true' if publishable else 'false'}\n")

    if not publishable:
        print("::error::At least one archive did not qualify, so nothing is published. "
              "The step summary and the qualification-*.json records list each failing "
              "check.")
        if args.fail_on_not_publishable:
            sys.exit(1)


if __name__ == "__main__":
    main()
