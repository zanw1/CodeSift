# Evidence helper contracts

Python 3.11+ and Bash are required. Resolve PCR_SKILL to this skill's absolute
directory and REVIEW_ROOT to the selected package. No helper installs tools.
Pass --root explicitly, or run from the reviewed package root.

The review-and-approval workflow in SKILL.md governs how the agent uses these
helpers. Before plan approval, use read-only helpers or isolated reproductions;
do not let a project command write into the reviewed working tree.

```bash
bash "$PCR_SKILL/scripts/collect_facts.sh" --root "$REVIEW_ROOT" --output json
bash "$PCR_SKILL/scripts/detect_duplicates.sh" --root "$REVIEW_ROOT" --lang python --json
python3 "$PCR_SKILL/scripts/dependency_audit.py" --root "$REVIEW_ROOT" --json
python3 "$PCR_SKILL/scripts/verify_packages.py" --root "$REVIEW_ROOT" --offline --json
bash "$PCR_SKILL/scripts/run_correctness_test.sh" --root "$REVIEW_ROOT" --lang python --json
```

--lang accepts auto, c, cpp, rust, python, javascript. Auto reports mixed
projects rather than silently choosing one root manifest. For execution or
dependency inspection, select a package and language. Native C/C++ sources alone
are treated as cpp when C++ sources exist.

## Results and exit codes

| Exit | Meaning |
|---|---|
| 0 | Requested operation completed; inspect status and evidence scope |
| 1 | Observed command/test failure, explicit dependency cap exceeded, or name absent from the specified public registry |
| 2 | Invalid invocation, missing prerequisite, incomplete/unknown evidence, timeout, parse/scan/network error |

Test success requires status=passed and complete=true. Benchmark completion uses
status=measured; it establishes command timings, not workload equivalence.
A file inventory with complete=true covers its stated scan policy, not all
framework entry points or nested dependency analysis.

--allow-no-tests is an explicit compatibility escape: exit 0 but status=no_tests,
complete=false. It must never satisfy a correctness gate. No other error is waived.

Scripts using --json emit one structured report. Argument-parser errors go to
stderr with exit 2. A failed command's output and exit code remain in the report.
The runner retains the last 256 KiB of command output and marks truncation;
use fresh JUnit evidence if textual summaries would be incomplete.

## Correctness

Automatic adapters use existing local runners only:

| Language | Adapter and limits |
|---|---|
| Python | Current helper interpreter's pytest; fresh temporary JUnit, basetemp and cache |
| Rust | cargo test --locked --offline; temporary target; existing lockfile/dependencies required |
| JS/TS | Existing npm test script, CI=true; recognized Jest/Vitest/Node summary required |
| C/C++ | Existing CTest directory, CTest 3.21+, --no-tests=error and fresh JUnit |

No automatic configure/build, direct execution of test_*.py, or npx tool download.
Select the project's interpreter/build through an explicit command when needed:

```bash
bash "$PCR_SKILL/scripts/run_correctness_test.sh" --root "$REVIEW_ROOT" \
  --command '.venv/bin/python -m pytest --junitxml {report}' --json
```

The command is parsed with shlex and executed without a shell. {report} and
{metrics} expand after argument parsing, so paths with spaces remain intact.
For complex scripts, invoke a reviewed script as the command.

Zero executed tests, all skipped cases, missing/malformed reports and unrecognized
success output are incomplete evidence. JUnit failures override a misleading exit 0.

--scale N exports TEST_SCALE and PCR_METRICS_PATH. To complete a scale request,
the harness must process the requested size and write {"observed_scale": N} to
PCR_METRICS_PATH (also available as {metrics}). The helper checks the value;
the reviewer checks that the harness measures actual processed input. No matching
observation means incomplete even if ordinary assertions passed.

## Comparative benchmark

Both commands are mandatory and are timed as whole processes. Prepare/build
outside timing unless startup/build time is the research question.

```bash
bash "$PCR_SKILL/scripts/run_benchmark_comparison.sh" --root "$REVIEW_ROOT" \
  --project-cmd './build/app --fixture data.json' \
  --baseline-cmd './build/reference --fixture data.json' \
  --workload 'same fixture and validated output' --runs 5 --warmup 1 --json
```

Runs alternate project/baseline order. Every warmup and sample must succeed.
Missing either side, failed commands and timeouts cannot become measured.
Reports include individual timings, medians, spread and the ratio.
Comparability, output correctness, profiling and algorithmic complexity still need review.

Default commands are argv strings, not shell programs. Literal metacharacters
are arguments. For reviewed shell syntax only, --trust-project-cmd and
--trust-baseline-cmd explicitly run that side through Bash with pipefail.
These flags are execution authorization, not input sanitizers.

## Dependency and registry limits

Shared parsing lives in lib/dependencies.py. Inventory retains names, source,
group, manifest and specifier. It does not execute builds, install dependencies,
compute usage or claim a transitive dependency count.

Supported inputs: Cargo dependencies/aliases/targets/workspace inheritance;
Python declarative project/optional/dev groups and requirements plus literal
setup.py; npm declarations/aliases; vcpkg and conanfile.txt declarations.
Dynamic definitions, includes, overrides, private registries and native executable
build logic produce explicit coverage limits. Select nested JS workspaces separately.

--max-deps N is an opt-in policy chosen by the caller. Exceeding it returns 1.
No universal cap or arbitrary reference-tool ratio is imposed.

Registry results use registry_match, not verified. A 404 is not_found in that
registry, not proof of hallucination; network/server errors stay unresolved.
Path/git/URL/workspace sources are not queried as public package names.
Private/configured registry overrides remain unresolved; --public-registry can
confirm use of the public default despite inherited registry configuration.
The helper never sends registry credentials. Queries reveal package names to
the selected public registry; use --offline when that is unsuitable.

## Read-only scans and redundancy

File scanning prunes documented generated/vendor/build directories and does not
follow symlinks. --max-files bounds work and reports truncation as incomplete.
This is not .gitignore-aware whole-repository coverage.

Duplicate detection indexes ordered blocks of nontrivial lines once, avoiding
quadratic repeated file reads and process creation. Tuple keys compare complete
normalized blocks directly; no cryptographic digest substitutes for equality.
--threshold sets block size;
--max-results bounds output. Matches are review candidates, not a duplication
percentage, merge gate or instruction to extract an abstraction.

Whole-program dead-code and gate judgments use patterns.md and project-aware
tools; these helpers deliberately make no universal reachability claim.

## Cleanup and regression tests

Helpers own temporary reports/logs and remove them on normal completion or
exceptions. Timeouts terminate the command process group. Project commands can
write their own artifacts: inspect them and use a disposable checkout when needed.
Do not overwrite global /tmp filenames or delete pre-existing user artifacts.

Run bash "$PCR_SKILL/scripts/smoke_test.sh". Fixtures and child caches live inside
automatically removed temporary directories; no registry network calls are made.
The wrapper delegates to smoke_test.py so static analysis can inspect the tests.
Tests assert actual outcomes, counts and failure causes rather than arbitrary
nonzero exit codes. Optional real-tool tests are reported as skipped if unavailable.

## Changes from the previous helper interface

- Use explicit --lang/--root flags; legacy positional arguments are removed.
- Benchmarks require both --project-cmd and --baseline-cmd; automatic unrelated
  benchmark-suite discovery is removed.
- The ineffective --reference-tool label is removed. Dependency caps now affect exit status.
- Registry JSON uses per-package statuses, not verified/hallucinated lists.
- lib/detect_lang.py is the single language/scanning implementation.
  The duplicate shell library has been removed; public .sh command paths remain.

Primary runner semantics:
[pytest](https://docs.pytest.org/en/stable/reference/exit-codes.html),
[CTest](https://cmake.org/cmake/help/latest/manual/ctest.1.html).
