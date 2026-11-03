#!/usr/bin/env python3
"""Run offline semantic regressions with automatically cleaned temporary fixtures."""

import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent / "lib"))
import check_runner
from dependencies import read_dependencies
from detect_lang import detect_lang
import verify_packages

SCRIPTS = Path(__file__).resolve().parent

HARNESS = '''"""Produce controlled test reports without accessing the network."""
import json
import os
from pathlib import Path
import sys

mode, report = sys.argv[1], Path(sys.argv[2])
cases = {
    "pass": '<testsuite tests="1"><testcase name="one"/></testsuite>',
    "failure": '<testsuite tests="1"><testcase name="one"><failure message="wrong"/></testcase></testsuite>',
    "false_success": '<testsuite tests="1"><testcase name="one"><failure/></testcase></testsuite>',
    "empty": '<testsuite tests="0"/>',
    "skip": '<testsuite tests="1"><testcase name="one"><skipped/></testcase></testsuite>',
    "mismatch": '<testsuite tests="2"><testcase name="one"/></testsuite>',
    "invalid": '<broken',
    "empty_error": '<testsuite tests="0"/>',
    "hidden_error": '<testsuite tests="1" errors="1"><testcase name="one"/></testsuite>',
}
report.write_text(cases.get(mode, cases["pass"]))
if mode in {"scale", "wrong_scale", "boolean_scale"}:
    scale = int(os.environ["TEST_SCALE"])
    observed = scale if mode == "scale" else (True if mode == "boolean_scale" else scale + 1)
    Path(os.environ["PCR_METRICS_PATH"]).write_text(json.dumps({"observed_scale": observed}))
sys.exit(3 if mode == "empty_error" else (1 if mode == "failure" else 0))
'''


class HelperRegressionTests(unittest.TestCase):
    """Check observable outcomes and failure causes rather than accepting arbitrary nonzero exits."""

    def setUp(self):
        """Create an isolated directory for each test and register unconditional cleanup."""
        self.temporary = tempfile.TemporaryDirectory(prefix="pcr regression ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        (self.root / "tmp").mkdir()
        self.env = dict(
            os.environ,
            PYTHONDONTWRITEBYTECODE="1",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
            TMPDIR=str(self.root / "tmp"),
            CARGO_NET_OFFLINE="true",
            CARGO_HOME=str(self.root / "cargo-home"),
            npm_config_cache=str(self.root / "npm-cache"),
        )
        self.harness = self.write("case.py", HARNESS)

    def write(self, relative, text):
        """Create required fixtures only inside the directory owned by this test."""
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def invoke(self, script, *arguments):
        """Run a public helper from the fixture project and parse its JSON report."""
        executable = ["bash"] if script.endswith(".sh") else [sys.executable]
        command = executable + [
            str(SCRIPTS / script),
            "--root",
            str(self.root),
            *arguments,
        ]
        process = subprocess.run(
            command,
            env=self.env,
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=50,
        )
        try:
            report = json.loads(process.stdout)
        except ValueError:
            self.fail(
                f"expected JSON, got rc={process.returncode}: {process.stdout}\n{process.stderr}"
            )
        return process.returncode, report

    def command_for_case(self, mode):
        """Build a direct command while preserving spaces in fixture paths."""
        return shlex.join([sys.executable, str(self.harness), mode, "{report}"])

    def correctness(self, mode, *extra):
        """Run a fixture that produces a fresh JUnit report."""
        return self.invoke(
            "run_correctness_test.sh",
            "--command",
            self.command_for_case(mode),
            "--json",
            *extra,
        )

    def bench(self, project="true", baseline="true", *extra):
        """Exercise both benchmark commands and their failure states using real processes."""
        return self.invoke(
            "run_benchmark_comparison.sh",
            "--project-cmd",
            project,
            "--baseline-cmd",
            baseline,
            "--runs",
            "2",
            "--warmup",
            "0",
            "--json",
            *extra,
        )

    def test_benchmark_failed_baseline_is_failure(self):
        """A failed baseline must remain a failure regardless of successful project samples."""
        code, report = self.bench("true", "false")
        self.assertEqual(
            (code, report["status"], report["complete"]), (1, "failed", False)
        )
        self.assertEqual(report["samples"][-1]["exit_code"], 1)

    def test_benchmark_requires_both_sides(self):
        """A baseline alone cannot produce a completed comparison."""
        code, report = self.invoke(
            "run_benchmark_comparison.sh", "--baseline-cmd", "true", "--json"
        )
        self.assertEqual((code, report["status"]), (2, "incomplete"))

    def test_benchmark_failed_project_is_failure(self):
        """A failed project command must stop the comparison."""
        code, report = self.bench("false", "true")
        self.assertEqual((code, report["status"]), (1, "failed"))

    def test_plain_printf_is_valid(self):
        """The letter n in a normal command must not be rejected as a shell metacharacter."""
        code, report = self.bench("printf ok", "printf ok")
        self.assertEqual(
            (code, report["status"], report["complete"]), (0, "measured", True)
        )
        self.assertEqual(len(report["summary"]["project"]["samples_seconds"]), 2)

    def test_metacharacters_are_literal_without_shell(self):
        """Direct argv execution must not interpret command separators inside arguments."""
        code, report = self.bench("printf '%s' '; touch injected'", "true")
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "injected").exists())
        self.assertEqual(report["samples"][0]["output"], "; touch injected")

    def test_trusted_shell_propagates_pipeline_failure(self):
        """Trusted shell execution must preserve failures within a pipeline."""
        code, report = self.bench("false | cat", "true", "--trust-project-cmd")
        self.assertEqual((code, report["status"]), (1, "failed"))

    def test_benchmark_warmup_failure_is_not_ignored(self):
        """A failed warmup must prevent measurement from starting."""
        code, report = self.bench("false", "true", "--warmup", "1")
        self.assertEqual((code, report["status"]), (1, "failed"))
        self.assertEqual(report["samples"], [])

    def test_benchmark_timeout_stops_children(self):
        """A timeout must terminate child processes before they can perform delayed writes."""
        child = "import time; from pathlib import Path; time.sleep(2); Path('late').write_text('bad')"
        parent = f"import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',{child!r}]); time.sleep(10)"
        command = shlex.join([sys.executable, "-c", parent])
        code, report = self.bench(command, "true", "--timeout", "1")
        self.assertEqual((code, report["status"]), (2, "timed_out"))
        time.sleep(2.1)
        self.assertFalse((self.root / "late").exists())

    def test_real_junit_pass_and_cleanup(self):
        """An executed passing case must pass and leave no temporary report behind."""
        code, report = self.correctness("pass")
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (0, "passed", 1)
        )
        self.assertFalse(Path(report["execution"]["command"][-1]).exists())
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_junit_failure_and_cleanup(self):
        """A failed test must remain failed while its temporary artifacts are cleaned."""
        code, report = self.correctness("failure")
        self.assertEqual(
            (code, report["status"], report["tests"]["failed"]), (1, "failed", 1)
        )
        self.assertEqual(list((self.root / "tmp").iterdir()), [])

    def test_junit_failure_overrides_exit_zero(self):
        """A failing JUnit report must override a misleading zero exit code."""
        code, report = self.correctness("false_success")
        self.assertEqual((code, report["status"]), (1, "failed"))

    def test_empty_junit_is_no_tests(self):
        """An empty test report must not count as executed test evidence."""
        code, report = self.correctness("empty")
        self.assertEqual(
            (code, report["status"], report["complete"]), (2, "no_tests", False)
        )

    def test_all_skipped_is_no_tests(self):
        """An entirely skipped suite must not count as executed tests."""
        code, report = self.correctness("skip")
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (2, "no_tests", 0)
        )

    def test_allow_no_tests_does_not_claim_pass(self):
        """Allowing no tests may change the exit code but not the evidence state."""
        code, report = self.correctness("empty", "--allow-no-tests")
        self.assertEqual(
            (code, report["status"], report["complete"]), (0, "no_tests", False)
        )

    def test_allow_no_tests_does_not_hide_missing_tool(self):
        """Allowing no tests must not suppress a missing-tool error."""
        code, report = self.invoke(
            "run_correctness_test.sh",
            "--command",
            "/missing/pcr/tool",
            "--allow-no-tests",
            "--json",
        )
        self.assertEqual((code, report["status"]), (2, "error"))

    def test_missing_adapter_is_not_an_allowed_empty_suite(self):
        """A missing adapter remains incomplete rather than an approved empty suite."""
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "c", "--allow-no-tests", "--json"
        )
        self.assertEqual(
            (code, report["status"], report["complete"]), (2, "incomplete", False)
        )

    def test_empty_report_does_not_waive_other_errors(self):
        """An empty report must not let allow-no-tests suppress unrelated command failures."""
        code, report = self.correctness("empty_error", "--allow-no-tests")
        self.assertEqual((code, report["status"]), (1, "failed"))

    def test_summary_error_cannot_hide_behind_passing_cases(self):
        """Inconsistent error summaries and testcase evidence must not pass."""
        code, report = self.correctness("hidden_error")
        self.assertEqual((code, report["status"]), (2, "incomplete"))

    def test_truncated_text_requires_complete_report(self):
        """A summary in truncated output must not establish complete test totals."""
        code_text = "print('x'*300000); print('Tests: 1 passed, 1 total')"
        command = shlex.join([sys.executable, "-c", code_text])
        code, report = self.invoke(
            "run_correctness_test.sh", "--command", command, "--json"
        )
        self.assertEqual((code, report["status"]), (2, "incomplete"))
        self.assertTrue(report["execution"]["output_truncated"])

    def test_exit_zero_without_test_evidence_is_incomplete(self):
        """A successful no-op command must not count as test evidence."""
        code, report = self.invoke(
            "run_correctness_test.sh", "--command", "true", "--json"
        )
        self.assertEqual((code, report["status"]), (2, "incomplete"))

    def test_invalid_junit_is_incomplete(self):
        """Malformed XML must not be interpreted as zero failures."""
        code, report = self.correctness("invalid")
        self.assertEqual((code, report["status"]), (2, "incomplete"))

    def test_inconsistent_junit_is_incomplete(self):
        """Reject reports whose declared totals disagree with testcase evidence."""
        code, report = self.correctness("mismatch")
        self.assertEqual((code, report["status"]), (2, "incomplete"))

    def test_scale_requires_observation(self):
        """Exporting a scale variable must not establish that the requested workload ran."""
        code, report = self.correctness("pass", "--scale", "17")
        self.assertEqual(
            (code, report["status"], report["scale_verified"]), (2, "incomplete", False)
        )

    def test_matching_scale_observation_passes(self):
        """A matching scale observation must complete the requested scale check."""
        code, report = self.correctness("scale", "--scale", "17")
        self.assertEqual(
            (code, report["status"], report["scale_verified"]), (0, "passed", True)
        )

    def test_wrong_or_boolean_scale_is_rejected(self):
        """Reject incorrect scales and booleans that compare equal to integer values."""
        for mode in ("wrong_scale", "boolean_scale"):
            with self.subTest(mode=mode):
                code, report = self.correctness(mode, "--scale", "1")
                self.assertEqual((code, report["status"]), (2, "incomplete"))

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest unavailable")
    def test_real_pytest_empty_suite(self):
        """Distinguish an empty real pytest suite from an ordinary test failure."""
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "python", "--json"
        )
        self.assertEqual((code, report["status"]), (2, "no_tests"))

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest unavailable")
    def test_real_pytest_passes_one_case(self):
        """Verify report handling and executed-case counts with real pytest."""
        self.write(
            "test_example.py",
            'def test_sum():\n    """Verify a simple observable behavior."""\n    assert sum([1, 2]) == 3\n',
        )
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "python", "--json"
        )
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (0, "passed", 1)
        )

    @unittest.skipUnless(shutil.which("ctest"), "ctest unavailable")
    def test_real_ctest_empty_suite(self):
        """An existing CTest configuration with no cases must not pass."""
        self.write("build/CTestTestfile.cmake", "# No tests intentionally.\n")
        code, report = self.invoke("run_correctness_test.sh", "--lang", "c", "--json")
        self.assertEqual((code, report["status"]), (2, "no_tests"))

    @unittest.skipUnless(shutil.which("ctest"), "ctest unavailable")
    def test_real_ctest_passes_one_case(self):
        """One real CTest case must produce usable JUnit evidence."""
        self.write(
            "build/CTestTestfile.cmake",
            f'add_test(one "{sys.executable}" "-c" "assert sum([1,2]) == 3")\n',
        )
        code, report = self.invoke("run_correctness_test.sh", "--lang", "c", "--json")
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (0, "passed", 1)
        )

    @unittest.skipUnless(
        shutil.which("cargo") and shutil.which("rustc"), "Rust toolchain unavailable"
    )
    def test_real_rust_counts_zero_and_one(self):
        """Real Rust suites with zero and one test must produce different verdicts."""
        self.write(
            "Cargo.toml",
            '[package]\nname="pcr_fixture"\nversion="0.1.0"\nedition="2021"\n',
        )
        self.write(
            "src/lib.rs", "/// A fixture with no tests.\npub fn value() -> u32 { 1 }\n"
        )
        setup = subprocess.run(
            ["cargo", "generate-lockfile", "--offline"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=20,
        )
        self.assertEqual(setup.returncode, 0, setup.stderr)
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "rust", "--json"
        )
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (2, "no_tests", 0)
        )
        self.write(
            "src/lib.rs",
            "/// A real fixture test.\n#[test]\nfn sum_is_correct() { assert_eq!(1 + 2, 3); }\n",
        )
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "rust", "--json"
        )
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (0, "passed", 1)
        )

    @unittest.skipUnless(
        shutil.which("npm") and shutil.which("node"), "Node/npm unavailable"
    )
    def test_real_node_counts_zero_and_one(self):
        """Check empty and nonempty real Node suites through the npm entry point."""
        self.write("package.json", '{"scripts":{"test":"node --test"}}')
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "javascript", "--json"
        )
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (2, "no_tests", 0)
        )
        self.write(
            "one.test.cjs",
            "const test = require('node:test');\nconst assert = require('node:assert/strict');\n// Verify one real assertion.\ntest('sum', () => { assert.equal(1 + 2, 3); });\n",
        )
        code, report = self.invoke(
            "run_correctness_test.sh", "--lang", "javascript", "--json"
        )
        self.assertEqual(
            (code, report["status"], report["tests"]["executed"]), (0, "passed", 1)
        )

    def test_dependency_cap_enforced(self):
        """Exceeding an explicit cap must fail; meeting the cap must complete."""
        self.write("requirements.txt", "requests\nflask\n")
        for cap, expected in (("1", 1), ("2", 0)):
            with self.subTest(cap=cap):
                code, report = self.invoke(
                    "dependency_audit.py",
                    "--lang",
                    "python",
                    "--max-deps",
                    cap,
                    "--json",
                )
                self.assertEqual(code, expected)
                self.assertEqual(report["policy"]["exceeded"], expected == 1)

    def test_malformed_manifest_not_zero_success(self):
        """An unreadable manifest must expose a parsing error."""
        self.write("package.json", "{broken")
        code, report = self.invoke(
            "dependency_audit.py", "--lang", "javascript", "--json"
        )
        self.assertEqual((code, report["status"]), (2, "incomplete"))
        self.assertTrue(report["errors"])

    def test_invalid_dependency_shape_is_not_character_list(self):
        """A malformed string dependency list must not become individual package characters."""
        self.write("pyproject.toml", '[project]\ndependencies="requests"\n')
        code, report = self.invoke("dependency_audit.py", "--lang", "python", "--json")
        self.assertEqual((code, report["status"]), (2, "incomplete"))
        self.assertTrue(report["errors"])

    def test_missing_workspace_member_is_visible(self):
        """An unmatched workspace member must expose a coverage gap."""
        self.write("Cargo.toml", '[workspace]\nmembers=["missing/*"]\n')
        code, report = self.invoke("dependency_audit.py", "--lang", "rust", "--json")
        self.assertEqual((code, report["status"]), (2, "incomplete"))
        self.assertTrue(report["warnings"])

    def test_python_names_are_canonicalized_for_counts(self):
        """Equivalent package-name separators must not cause a false cap violation."""
        self.write("requirements.txt", "my_pkg\nmy-pkg\n")
        code, report = self.invoke(
            "dependency_audit.py", "--lang", "python", "--max-deps", "1", "--json"
        )
        self.assertEqual((code, report["count"]), (0, 1))

    def test_rust_alias_and_path_sources(self):
        """Resolve Cargo aliases and avoid public registry queries for path dependencies."""
        self.write(
            "Cargo.toml",
            '[dependencies]\nalias={package="serde",version="1"}\nlocal={path="../local"}\n',
        )
        inventory = read_dependencies(self.root, "rust")
        self.assertEqual(
            [(d["name"], d["source"]) for d in inventory["direct"]],
            [("serde", "registry"), ("local", "path")],
        )
        with patch.object(
            verify_packages, "check_registry", return_value=("registry_match", "exists")
        ) as checker:
            report = verify_packages.verify_inventory(
                inventory, self.root, public_registry=True
            )
        checker.assert_called_once_with("serde", "rust", 10)
        self.assertEqual(report["results"][1]["status"], "not_applicable")

    def test_workspace_inheritance_and_targets(self):
        """Include workspace inheritance and target-specific declarations in the inventory."""
        self.write(
            "Cargo.toml",
            '[workspace]\nmembers=["member"]\n[workspace.dependencies]\nalias={package="serde",version="1"}\n',
        )
        self.write(
            "member/Cargo.toml",
            '[dependencies]\nalias={workspace=true}\n[target."cfg(unix)".build-dependencies]\ncc="1"\n',
        )
        inventory = read_dependencies(self.root, "rust")
        self.assertEqual({d["name"] for d in inventory["direct"]}, {"serde", "cc"})
        self.assertFalse(inventory["errors"])

    def test_setup_py_is_not_executed(self):
        """Extract literal requirements without executing setup.py side effects."""
        self.write(
            "setup.py",
            "from pathlib import Path\nPath('executed').write_text('bad')\nsetup(install_requires=['requests>=2'])\n",
        )
        inventory = read_dependencies(self.root, "python")
        self.assertEqual(inventory["direct"][0]["name"], "requests")
        self.assertFalse((self.root / "executed").exists())

    def test_explicit_empty_setup_dependencies_are_valid(self):
        """Distinguish explicitly empty dependencies from unresolved declarations."""
        self.write("setup.py", "setup(install_requires=[])\n")
        code, report = self.invoke("dependency_audit.py", "--lang", "python", "--json")
        self.assertEqual((code, report["count"], report["complete"]), (0, 0, True))

    def test_poetry_configured_source_is_not_assumed_public(self):
        """Configured Poetry sources must not be replaced with assumed public PyPI queries."""
        self.write(
            "pyproject.toml",
            '[tool.poetry.dependencies]\ninternal="1"\n[[tool.poetry.source]]\nname="internal"\nurl="https://registry.invalid"\n',
        )
        inventory = read_dependencies(self.root, "python")
        with patch.object(
            verify_packages,
            "check_registry",
            side_effect=AssertionError("network called"),
        ):
            report = verify_packages.verify_inventory(
                inventory, self.root, public_registry=True
            )
        self.assertEqual(report["results"][0]["status"], "unresolved")

    def test_native_manifest_names_do_not_prove_installation(self):
        """Native declaration counts must not imply registry matches or installed libraries."""
        self.write("vcpkg.json", '{"dependencies":["zlib",{"name":"fmt"}]}')
        self.write(
            "conanfile.txt", "[requires]\nopenssl/3.0\n[generators]\nCMakeDeps\n"
        )
        inventory = read_dependencies(self.root, "cpp")
        self.assertEqual(inventory["count"], 3)
        with patch.object(
            verify_packages,
            "check_registry",
            side_effect=AssertionError("network called"),
        ):
            report = verify_packages.verify_inventory(
                inventory, self.root, public_registry=True
            )
        self.assertEqual(report["status"], "incomplete")
        self.assertTrue(
            all(item["status"] == "unresolved" for item in report["results"])
        )

    def test_npm_alias_and_workspace(self):
        """Resolve npm aliases and avoid public registry queries for workspace dependencies."""
        self.write(
            "package.json",
            json.dumps(
                {
                    "dependencies": {
                        "alias": "npm:@scope/real@^2",
                        "internal": "workspace:*",
                    }
                }
            ),
        )
        inventory = read_dependencies(self.root, "javascript")
        self.assertEqual(
            [(d["name"], d["source"]) for d in inventory["direct"]],
            [("@scope/real", "registry"), ("internal", "workspace")],
        )

    def test_private_registry_is_unresolved(self):
        """Private registry declarations must remain unresolved rather than publicly disproven."""
        self.write("package.json", '{"dependencies":{"@org/private":"1"}}')
        self.write(".npmrc", "@org:registry=https://registry.invalid/\n")
        inventory = read_dependencies(self.root, "javascript")
        with patch.object(
            verify_packages,
            "check_registry",
            side_effect=AssertionError("network called"),
        ):
            report = verify_packages.verify_inventory(
                inventory, self.root, public_registry=True
            )
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["results"][0]["status"], "unresolved")

    def test_offline_mode_does_not_query(self):
        """Offline checks must remain unverified and make no network calls."""
        self.write("requirements.txt", "requests\n")
        inventory = read_dependencies(self.root, "python")
        with patch.object(
            verify_packages,
            "check_registry",
            side_effect=AssertionError("network called"),
        ):
            report = verify_packages.verify_inventory(
                inventory, self.root, offline=True, public_registry=True
            )
        self.assertEqual(
            (report["status"], report["checks_performed"]), ("incomplete", 0)
        )

    def test_network_error_is_not_missing_package(self):
        """Network or server failures must not become missing-package findings."""
        self.write("requirements.txt", "requests\n")
        inventory = read_dependencies(self.root, "python")
        with patch.object(
            verify_packages, "check_registry", return_value=("error", "HTTP 503")
        ):
            report = verify_packages.verify_inventory(
                inventory, self.root, public_registry=True
            )
        self.assertEqual(report["results"][0]["status"], "error")
        self.assertEqual(report["status"], "incomplete")

    def test_registry_404_is_only_registry_absence(self):
        """A public HTTP 404 must retain its registry-specific interpretation."""
        import urllib.error

        error = urllib.error.HTTPError(
            "https://example.invalid", 404, "missing", {}, None
        )
        with patch("urllib.request.urlopen", side_effect=error):
            status, reason = verify_packages.check_registry("missing", "python")
        self.assertEqual(status, "not_found")
        self.assertIn("not proof", reason)

    def test_registry_budget_is_visible(self):
        """Exhausting the request budget must leave remaining declarations visible."""
        self.write("requirements.txt", "requests\nflask\n")
        inventory = read_dependencies(self.root, "python")
        with patch.object(
            verify_packages, "check_registry", return_value=("registry_match", "exists")
        ):
            report = verify_packages.verify_inventory(
                inventory, self.root, max_checks=1, public_registry=True
            )
        self.assertEqual(
            (report["status"], report["checks_performed"]), ("incomplete", 1)
        )
        self.assertEqual(len(report["results"]), 2)

    def test_mixed_manifest_not_arbitrarily_selected(self):
        """Mixed root manifests must require an explicit package or language selection."""
        self.write("Cargo.toml", "[package]\nname='x'\n")
        self.write("package.json", "{}")
        self.assertEqual(detect_lang(self.root), "mixed")
        code, report = self.invoke("dependency_audit.py", "--json")
        self.assertEqual((code, report["status"]), (2, "incomplete"))

    def test_cmake_default_languages(self):
        """Distinguish default CMake languages from an explicit C-only project."""
        self.harness.unlink()
        self.write("CMakeLists.txt", "project(example)\n")
        self.assertEqual(detect_lang(self.root), "cpp")
        self.write("CMakeLists.txt", "project(example LANGUAGES C)\n")
        self.assertEqual(detect_lang(self.root), "c")

    def test_facts_paths_with_spaces_and_same_line_deps(self):
        """Handle paths with spaces and same-line dependency arrays correctly."""
        self.write(
            "pyproject.toml",
            '[project]\nname="demo"\ndependencies=["requests","flask"]\n',
        )
        self.write("directory with spaces/app.py", "x = 1\ny = 2\n")
        code, report = self.invoke("collect_facts.sh", "--output", "json")
        self.assertEqual(code, 0)
        self.assertEqual(report["dependency_inventory"]["count"], 2)
        self.assertEqual(report["codebase"]["source_files"], 2)

    def test_file_limit_is_incomplete(self):
        """A scan limit must not imply complete project coverage."""
        # Add a second file to make the limit genuinely truncate.
        self.write("another.py", "x = 1\n")
        code, report = self.invoke(
            "collect_facts.sh", "--max-files", "1", "--output", "json"
        )
        self.assertEqual(
            (code, report["status"], report["complete"]), (2, "incomplete", False)
        )

    def test_ordered_duplicate_blocks(self):
        """Match ordered blocks rather than unordered collections of shared lines."""
        self.harness.unlink()
        self.write("a.py", "alpha=1\nbeta=2\ngamma=3\n")
        self.write("b.py", "alpha=1\nbeta=2\ngamma=3\n")
        self.write("c.py", "gamma=3\nbeta=2\nalpha=1\n")
        code, report = self.invoke(
            "detect_duplicates.sh", "--lang", "python", "--threshold", "3", "--json"
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(report["candidates"]), 1)
        self.assertEqual(
            {loc["file"] for loc in report["candidates"][0]["locations"]},
            {"a.py", "b.py"},
        )

    def test_braces_are_not_duplicate_evidence(self):
        """Punctuation-only lines must not create duplicate-implementation candidates."""
        self.write("a.c", "{\n}\n{\n}\n")
        self.write("b.c", "{\n}\n{\n}\n")
        code, report = self.invoke(
            "detect_duplicates.sh", "--lang", "c", "--threshold", "2", "--json"
        )
        self.assertEqual((code, report["candidates"]), (0, []))

    def test_duplicate_result_limit_is_visible(self):
        """Truncated duplicate results must not claim complete coverage."""
        for name in ("a.py", "b.py"):
            self.write(name, "alpha=1\nbeta=2\ngamma=3\ndelta=4\n")
        code, report = self.invoke(
            "detect_duplicates.sh",
            "--lang",
            "python",
            "--threshold",
            "3",
            "--max-results",
            "1",
            "--json",
        )
        self.assertEqual((code, report["status"]), (2, "incomplete"))
        self.assertEqual(len(report["candidates"]), 1)

    def test_invalid_limits_are_rejected_before_execution(self):
        """Reject invalid limits before they can skip checks or remove execution bounds."""
        for flag, value in (("--runs", "0"), ("--warmup", "-1"), ("--timeout", "0")):
            with self.subTest(flag=flag):
                result = subprocess.run(
                    ["bash", str(SCRIPTS / "run_benchmark_comparison.sh"), flag, value],
                    cwd=self.root,
                    env=self.env,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertEqual(result.returncode, 2)
                self.assertIn("must be", result.stderr)

    def test_jest_and_node_counts(self):
        """Known summaries must distinguish executed cases from entirely skipped suites."""
        for output, executed in (
            ("Tests: 2 passed, 1 skipped, 3 total", 2),
            ("# tests 3\n# pass 2\n# fail 0\n# skipped 1\n", 2),
            ("Tests  3 skipped (3)", 0),
        ):
            with self.subTest(output=output):
                self.assertEqual(
                    check_runner.summary_counts(output)["executed"], executed
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
