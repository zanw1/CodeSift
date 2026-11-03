"""Run tests and benchmarks with explicit failures, checked evidence, and owned-artifact cleanup."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import signal
import statistics
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET

from detect_lang import (
    add_project_arguments,
    positive_int,
    print_report,
    select_language,
)


def command_argv(command: str, trusted_shell: bool = False) -> list[str]:
    """Execute argv directly unless trusted shell syntax is explicitly enabled."""
    args = (
        ["/bin/bash", "-o", "pipefail", "-c", command]
        if trusted_shell
        else shlex.split(command)
    )
    if not args or not args[0]:
        raise ValueError("command must not be empty")
    return args


def run_command(argv: list[str], root: Path, env: dict, timeout: int) -> dict:
    """Run a command with a timeout, terminate its process group, and clean temporary output."""
    started = time.perf_counter()
    result = {"command": argv, "exit_code": None, "timed_out": False, "error": None}
    with tempfile.TemporaryFile() as output:
        try:
            process = subprocess.Popen(
                argv,
                cwd=root,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as error:
            result["error"] = f"{type(error).__name__}: {error}"
        else:
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                result["timed_out"] = True
                # A child may outlive its parent; signal the whole task-owned group.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            except BaseException:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            result["exit_code"] = process.returncode
        result["duration_seconds"] = time.perf_counter() - started
        size = output.tell()
        output.seek(max(0, size - 262144))
        result["output"] = output.read().decode(errors="replace")
        result["output_truncated"] = size > 262144
    return result


def junit_counts(path: Path) -> dict:
    """Count JUnit testcase evidence and reject malformed or inconsistent summaries."""
    root = ET.parse(path).getroot()
    if root.tag.rsplit("}", 1)[-1] not in {"testsuite", "testsuites"}:
        raise ValueError("not a JUnit report")
    cases = [node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "testcase"]
    counts = {"total": len(cases), "executed": 0, "failed": 0, "skipped": 0}
    for case in cases:
        tags = {child.tag.rsplit("}", 1)[-1] for child in case}
        skipped = "skipped" in tags or case.attrib.get("status") in {
            "notrun",
            "disabled",
        }
        counts["skipped"] += int(skipped)
        counts["executed"] += int(not skipped)
        counts["failed"] += int(bool(tags & {"failure", "error"}))
    for suite in root.iter():
        if suite.tag.rsplit("}", 1)[-1] not in {"testsuite", "testsuites"}:
            continue
        local = [
            case for case in suite.iter() if case.tag.rsplit("}", 1)[-1] == "testcase"
        ]
        if "tests" in suite.attrib and int(suite.attrib["tests"]) != len(local):
            raise ValueError("JUnit suite count disagrees with testcase nodes")
        if counts["failed"] == 0 and any(
            int(suite.attrib.get(key, "0")) > 0 for key in ("failures", "errors")
        ):
            raise ValueError("JUnit declares failures without failure/error nodes")
    return counts


def summary_counts(output: str) -> dict | None:
    """Parse known test summaries; exit zero or generic success text is insufficient evidence."""
    output = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", output)
    rust = re.findall(
        r"^test result: (?:ok|FAILED)\. (\d+) passed; (\d+) failed; (\d+) ignored;",
        output,
        re.M,
    )
    if rust:
        passed, failed, skipped = (sum(int(row[i]) for row in rust) for i in range(3))
        return {
            "total": passed + failed + skipped,
            "executed": passed + failed,
            "failed": failed,
            "skipped": skipped,
        }
    unittest = re.search(r"^Ran (\d+) tests? in [\d.]+s", output, re.M)
    if unittest:
        total = int(unittest.group(1))
        skipped = sum(map(int, re.findall(r"skipped=(\d+)", output)))
        failed = sum(map(int, re.findall(r"(?:failures|errors)=(\d+)", output)))
        return {
            "total": total,
            "executed": max(0, total - skipped),
            "failed": failed,
            "skipped": skipped,
        }
    tap = {
        key: int(value)
        for key, value in re.findall(
            r"^# (tests|pass|fail|skipped|todo) (\d+)\s*$", output, re.M
        )
    }
    if "tests" in tap:
        skipped = tap.get("skipped", 0) + tap.get("todo", 0)
        return {
            "total": tap["tests"],
            "executed": max(0, tap["tests"] - skipped),
            "failed": tap.get("fail", 0),
            "skipped": skipped,
        }
    for line in output.splitlines():
        if re.match(r"^\s*Tests:?\s+", line):
            passed = sum(map(int, re.findall(r"(\d+) passed", line)))
            failed = sum(map(int, re.findall(r"(\d+) failed", line)))
            skipped = sum(map(int, re.findall(r"(\d+) (?:skipped|todo|pending)", line)))
            if passed or failed or skipped:
                return {
                    "total": passed + failed + skipped,
                    "executed": passed + failed,
                    "failed": failed,
                    "skipped": skipped,
                }
    return None


def automatic_test_command(
    root: Path, lang: str, report: Path, temp: Path
) -> list[str] | None:
    """Select an existing runner without installing tools, configuring builds, or guessing test scripts."""
    if lang == "python" and importlib.util.find_spec("pytest"):
        return [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "--junitxml",
            str(report),
            "--basetemp",
            str(temp / "pytest"),
            "-o",
            f"cache_dir={temp / 'pytest-cache'}",
        ]
    if lang == "rust" and (root / "Cargo.toml").is_file():
        return [
            "cargo",
            "test",
            "--locked",
            "--offline",
            "--target-dir",
            str(temp / "cargo-target"),
            "--",
            "--nocapture",
        ]
    if lang == "javascript" and (root / "package.json").is_file():
        scripts = json.loads((root / "package.json").read_text()).get("scripts", {})
        if scripts.get("test"):
            return ["npm", "test"]
    if lang in {"c", "cpp"}:
        for name in ("build", "cmake-build-debug", "cmake-build-release", "out"):
            if (root / name / "CTestTestfile.cmake").is_file():
                return [
                    "ctest",
                    "--test-dir",
                    str(root / name),
                    "--output-on-failure",
                    "--no-tests=error",
                    "--output-junit",
                    str(report),
                ]
    return None


def assess_test_execution(
    execution: dict, report_path: Path, argv: list[str]
) -> tuple[dict, int]:
    """Assess execution and fresh reports so empty evidence or broken wrappers cannot pass."""
    result = {"status": "incomplete", "complete": False, "tests": None}
    if execution["error"] or execution["timed_out"]:
        result["status"] = "error" if execution["error"] else "timed_out"
        return result, 2
    has_junit = report_path.is_file()
    if execution["output_truncated"] and not has_junit:
        result["reason"] = (
            "text output truncated; complete test totals require fresh JUnit evidence"
        )
        return result, 2
    try:
        counts = (
            junit_counts(report_path)
            if has_junit
            else summary_counts(execution["output"])
        )
    except (ValueError, OSError, ET.ParseError) as error:
        result["reason"] = f"invalid test evidence: {error}"
        return result, 2
    result["tests"] = counts
    if counts is not None and counts["failed"] > 0:
        result.update(status="failed", complete=True)
        return result, 1
    no_tests = counts is not None and counts["executed"] == 0
    no_tests = no_tests or (
        not counts
        and bool(
            re.search(
                r"No tests were found|no tests ran|collected 0 items",
                execution["output"],
                re.I,
            )
        )
    )
    if no_tests:
        known_empty_exit = (
            execution["exit_code"] == 0
            or (execution["exit_code"] == 5 and "pytest" in argv)
            or (
                Path(argv[0]).name == "ctest"
                and execution["exit_code"] == 8
                and "No tests were found" in execution["output"]
            )
        )
        if not known_empty_exit:
            result.update(
                status="failed",
                reason="empty evidence does not waive this command failure",
            )
            return result, 1
        result.update(
            status="no_tests",
            reason="zero tests executed (all skipped also counts as zero)",
        )
        return result, 2
    if execution["exit_code"] != 0:
        result.update(status="failed", reason="test command failed; inspect output")
        return result, 1
    if counts is None:
        result["reason"] = (
            "exit 0 without recognized test count; provide fresh JUnit via {report}"
        )
        return result, 2
    result.update(status="passed", complete=True)
    return result, 0


def correctness(args: argparse.Namespace, root: Path) -> tuple[dict, int]:
    """Coordinate test adapters, temporary artifacts, and scale observations with separate verdict logic."""
    lang = select_language(root, args.lang)
    result = {
        "kind": "correctness",
        "language": lang,
        "status": "incomplete",
        "complete": False,
        "tests": None,
        "scale_requested": args.scale,
        "scale_verified": False,
    }
    with tempfile.TemporaryDirectory(prefix="pcr-test-") as directory:
        temp = Path(directory)
        report_path, metrics = temp / "junit.xml", temp / "metrics.json"
        env = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            CI="true",
            PCR_METRICS_PATH=str(metrics),
            npm_config_cache=str(temp / "npm-cache"),
            npm_config_update_notifier="false",
        )
        if args.scale is not None:
            env["TEST_SCALE"] = str(args.scale)
        if args.command:
            argv = command_argv(args.command)
            # Substitute after splitting so paths containing spaces stay one argument.
            argv = [
                value.replace("{report}", str(report_path)).replace(
                    "{metrics}", str(metrics)
                )
                for value in argv
            ]
        else:
            argv = automatic_test_command(root, lang, report_path, temp)
        if argv is None:
            result["reason"] = (
                "no supported existing runner; provide --command with fresh JUnit {report}"
            )
            return result, 2
        execution = run_command(argv, root, env, args.timeout)
        result["execution"] = execution
        verdict, exit_code = assess_test_execution(execution, report_path, argv)
        result.update(verdict)
        if result["status"] == "no_tests" and args.allow_no_tests:
            return result, 0
        if result["status"] == "passed" and args.scale is not None:
            try:
                data = json.loads(metrics.read_text())
                result["scale_verified"] = (
                    type(data.get("observed_scale")) is int
                    and data["observed_scale"] == args.scale
                )
            except (OSError, ValueError, AttributeError):
                result["scale_verified"] = False
            if not result["scale_verified"]:
                result.update(
                    status="incomplete",
                    complete=False,
                    reason="tests passed but harness did not report matching observed_scale via PCR_METRICS_PATH",
                )
                return result, 2
        return result, exit_code


def benchmark(args: argparse.Namespace, root: Path) -> tuple[dict, int]:
    """Alternate two explicit commands and stop on any failed warmup or measured run."""
    result = {
        "kind": "benchmark",
        "status": "incomplete",
        "complete": False,
        "language": select_language(root, args.lang),
        "measurement": "whole-command wall time",
        "workload": args.workload,
        "comparability": "must be checked against workload, output and environment by reviewer",
        "runs": args.runs,
        "warmup": args.warmup,
        "samples": [],
        "warmup_results": [],
    }
    if not args.project_cmd or not args.baseline_cmd:
        result["reason"] = "both --project-cmd and --baseline-cmd are required"
        return result, 2
    commands = {
        "project": command_argv(args.project_cmd, args.trust_project_cmd),
        "baseline": command_argv(args.baseline_cmd, args.trust_baseline_cmd),
    }
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", CI="true")
    for index in range(args.warmup + args.runs):
        order = ("project", "baseline") if index % 2 == 0 else ("baseline", "project")
        destination = (
            result["warmup_results"] if index < args.warmup else result["samples"]
        )
        for name in order:
            execution = run_command(commands[name], root, env, args.timeout)
            execution["side"] = name
            destination.append(execution)
            if (
                execution["error"]
                or execution["timed_out"]
                or execution["exit_code"] != 0
            ):
                result["status"] = (
                    "timed_out"
                    if execution["timed_out"]
                    else ("error" if execution["error"] else "failed")
                )
                result["reason"] = f"{name} command did not complete successfully"
                return result, 1 if result["status"] == "failed" else 2
    result["summary"] = {}
    for name in commands:
        values = [
            sample["duration_seconds"]
            for sample in result["samples"]
            if sample["side"] == name
        ]
        result["summary"][name] = {
            "median_seconds": statistics.median(values),
            "min_seconds": min(values),
            "max_seconds": max(values),
            "samples_seconds": values,
        }
    baseline = result["summary"]["baseline"]["median_seconds"]
    result["ratio_project_over_baseline"] = (
        result["summary"]["project"]["median_seconds"] / baseline
        if baseline > 0
        else None
    )
    result.update(status="measured", complete=True)
    return result, 0


def nonnegative_int(value: str) -> int:
    """Allow zero warmups while rejecting negative counts that would skip execution."""
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be non-negative")
    return number


def main() -> int:
    """Provide a consistent CLI and emit one report with explicit completion and failure states."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    test = subparsers.add_parser("correctness")
    bench = subparsers.add_parser("benchmark")
    for command in (test, bench):
        add_project_arguments(command)
        command.add_argument("--json", action="store_true")
        command.add_argument("--timeout", type=positive_int, default=30)
    test.add_argument(
        "--command",
        help="argv string; {report}/{metrics} expand to fresh temporary paths",
    )
    test.add_argument("--scale", type=positive_int)
    test.add_argument("--allow-no-tests", action="store_true")
    bench.add_argument("--project-cmd")
    bench.add_argument("--baseline-cmd")
    bench.add_argument("--trust-project-cmd", action="store_true")
    bench.add_argument("--trust-baseline-cmd", action="store_true")
    bench.add_argument("--runs", type=positive_int, default=5)
    bench.add_argument("--warmup", type=nonnegative_int, default=1)
    bench.add_argument(
        "--workload", default="unspecified; comparability not established"
    )
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir():
        parser.error("--root must be an existing directory")
    try:
        report, exit_code = (
            correctness(args, root)
            if args.mode == "correctness"
            else benchmark(args, root)
        )
    except (ValueError, OSError) as error:
        report, exit_code = (
            {"status": "error", "complete": False, "reason": str(error)},
            2,
        )
    print_report(report, args.json)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
