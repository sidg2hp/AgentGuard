"""Compare paired trial outcomes for the same benchmark suite."""
import argparse
import json
from pathlib import Path


def _outcomes(report):
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("The report has no trials.")
    outcomes = {}
    for row in runs:
        key = (row["profile"], row["id"])
        if key in outcomes or type(row["passed"]) is not bool:
            raise ValueError("Each scenario needs one boolean outcome per profile.")
        outcomes[key] = row
    return outcomes


def compare(previous, current):
    if not previous.get("suite_sha256") or previous["suite_sha256"] != current.get("suite_sha256"):
        raise ValueError("The scenario suite fingerprints differ or are missing.")
    before, after = _outcomes(previous), _outcomes(current)
    if before.keys() != after.keys():
        raise ValueError("The profile and scenario selections differ.")
    changes = []
    for key, row in sorted(after.items()):
        was_passing = before[key]["passed"]
        if was_passing != row["passed"]:
            failing = before[key] if row["passed"] else row
            changes.append({"profile": key[0], "scenario": key[1],
                            "change": "improved" if row["passed"] else "regressed",
                            "failed_checks": [name for name, passed in failing["checks"].items() if not passed]})
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("previous", type=Path)
    parser.add_argument("current", type=Path)
    parser.add_argument("--fail-on-regression", action="store_true")
    args = parser.parse_args()
    try:
        before, after = [json.loads(path.read_text(encoding="utf-8")) for path in (args.previous, args.current)]
        changes = compare(before, after)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        parser.error(f"Cannot compare reports. {exc}")
    for row in changes:
        print(f"{row['change']:9} {row['profile']} {row['scenario']}  {', '.join(row['failed_checks'])}")
    improvements = sum(row["change"] == "improved" for row in changes)
    regressions = len(changes) - improvements
    print(f"{improvements} improved, {regressions} regressed, {len(after['runs']) - len(changes)} unchanged.")
    return int(args.fail_on_regression and regressions > 0)


if __name__ == "__main__":
    raise SystemExit(main())
