---
name: codesift
description: >
  Review code for plausible-looking systemic defects: unreachable fast paths, broken
  integration or function contracts, redundant or unused code, ineffective validation/CI
  gates, and unsupported performance claims. Use for AI-generated changes, targeted
  code health or coding-standard audits, and requests to find zombie code or meaningless
  gates. Present findings and a concrete remediation plan; wait for user approval
  before editing.
---

# CodeSift

Find code that looks locally reasonable but fails its actual system contract.
Focus on reachable behavior and evidence. AI authorship is context, not evidence
of a defect. A review may legitimately produce no findings.

## Required review-and-approval workflow

1. **Investigate first.** Do not modify tracked or untracked project files during
   investigation. Use read-only inspection and isolated temporary reproductions
   that do not alter the reviewed project or live external state.
2. **Present findings and a remediation plan.** For each actionable item, state
   the evidence, impact, root cause or unresolved assumptions, affected files,
   proposed correction, and validation method. Separate confirmed defects,
   hypotheses, and optional improvements. Give items stable identifiers when
   the user may approve a subset.
3. **Wait for explicit user approval.** Approval must refer to the presented plan
   or selected items. A general request to review, clean up, or fix code does not
   approve an unseen remediation plan. Silence is not approval.
4. **Implement the approved scope.** Do not request approval again for work
   already covered by that plan. A follow-up instruction to start that plan is
   sufficient. Present material scope or approach expansions separately and wait
   for approval of those additions before implementing them.
5. **Validate and report.** Run focused checks first, then the affected checks
   warranted by the completed change. Report modifications, actual results and
   remaining limits. Preserve failure evidence and remove temporary artifacts.

## Language policy

Keep this skill's instructions, references, metadata, helper comments, docstrings,
built-in messages and bundled fixtures in English. Follow the user's requested
language for conversation. Preserve the reviewed project's language conventions,
business text and original evidence; do not translate them to satisfy this
skill-internal policy.

## Scope and execution

Choose the smallest scope that answers the request:

| Request | Review boundary |
|---|---|
| Change / PR | Base and head revisions, changed behavior, affected callers and contracts |
| Module / symptom | Named entry points, dependencies and implicated paths |
| Deep repository audit | Production, library, worker and build roots; architecture and cross-module boundaries |

Record revision or source snapshot, included/excluded paths, assumptions and
unresolved coverage. Do not silently turn a local review into a repository audit.
Do not claim complete coverage from a truncated search, missing index or unsupported language.

Start with the project's instructions, manifests, tests and documented contracts.
Distinguish requirements stated by the user/specification from intent inferred
from names. Investigate disagreements before proposing a fix.

Use available structural tools first. With codebase-memory, check index coverage
for every relied-on file/scope, search symbols, trace incoming/outgoing references
and read exact source. For gaps, inspect source and configuration directly.
Text search is a candidate finder. Neither a failed text search nor zero graph
degree proves unreachability or absence of external consumers.

A review request authorizes investigation. Project edits require approval of the
presented remediation plan. That approval covers only the described changes and
checks, not unrelated refactoring or unmentioned dependency/live-environment changes.
Inspect test/build hooks before execution; use a disposable workspace for commands
with project side effects. Helpers clean their own temporary reports, not arbitrary
files written by project commands. Preserve existing user artifacts.

## Evidence and impact are separate

Use these evidence states:

- **reproduced:** observed the relevant behavior with a recorded command/input and output.
- **confirmed_static:** source establishes the trigger, reachable path and consequence;
  record unresolved runtime assumptions.
- **hypothesis:** an unresolved candidate, including pattern/tool matches.

Severity describes impact **if the finding holds**, not how easy it was to run a
test: Critical / High / Medium / Low. Assess correctness, integrity, security,
availability, affected workload and recovery. A rare catastrophic loss may be
severe; a large slowdown outside important workloads may not be.
Do not assign a severity from a fixed performance ratio, line count or dependency count.

Choose evidence by claim:

| Claim | Evidence needed |
|---|---|
| Correctness / contract | Expected behavior source, concrete trigger, relevant caller-to-effect path, counterexample or static proof |
| Security boundary | Actor/capability, trust boundary, reachable operation and missing/bypassed enforcement; safe reproduction when feasible |
| Performance | Equivalent workload/results, environment/build mode, repeated project and baseline measurements, profiling where causal attribution matters |
| Unused code | Root inventory, bounded reachability/reference search, dynamic/external-consumer checks, build/config variants and deletion validation when authorized |
| Redundancy / gate | Duplicated responsibility or protected invariant, consumers, contrasting pass/fail cases and observable consequences |

A benchmark is not required for non-performance findings. Build success and
registry name matches do not establish runtime behavior or supply-chain safety.
An unexecuted or incomplete check stays visible; it is not a pass.
Hypotheses do not independently justify merge blocking or code deletion.

## Investigation procedure

1. **Establish context.** Confirm scope, roots, contracts, relevant build/configuration
   variants and existing checks. Use facts collection only if it adds information.
2. **Trace affected behavior.** Follow the paths relevant to the request. Check
   dispatch, data/error flow, defaults, resource ownership and persistence.
   A read-only library need not invent a write path to satisfy a checklist.
3. **Inspect systemic risks.** Apply relevant dimensions below; do not run every
   tool or every language guide for every change.
4. **Challenge candidates.** Look for counterevidence: intentional tradeoffs,
   external consumers, alternative active paths and existing enforcement.
5. **Validate findings and prepare the plan.** Use read-only or isolated checks
   to resolve claims. Benchmark only performance claims and state what remains
   unverified. Consolidate shared root causes, present the proposed corrections,
   and wait for approval before modifying the reviewed project.

## Review dimensions

| Dimension | What to establish |
|---|---|
| Algorithmic dispatch | Whether intended input classes reach the appropriate implementation; a fast path need not serve every valid input |
| Resource cost | Actual allocation/copy/lock/I/O contributions on important paths; do not multiply guessed slowdown factors |
| Contracts and integration | Callers, serialization, errors, defaults, cancellation, retries and state transitions agree across boundaries |
| Hidden assumptions and intent | Input/scale/concurrency/deployment assumptions are supported; negative requirements are actually enforced |
| Problem proportionality | Complexity buys a concrete requirement; counts alone do not establish overengineering |
| Functions and coding standards | Focused responsibilities, explicit public contracts, clear names, useful comments and project-consistent language idioms |
| Dependencies | Actual identity/source/version, active configuration, usage and duplicated capability |
| Tests | Assertions distinguish correct and incorrect behavior; skipped/empty/mocked checks do not stand in for execution |
| Redundant implementations | Same responsibility or repeated work with no distinct contract; consider coupling before extracting an abstraction |
| Unused / zombie code | Unreachable modules, cycles, old branches, unused exports/configuration/assets and test-only implementations |
| Gates | Validation, CI conditions, feature flags and runtime guards change the intended decision and have useful failure behavior |

For function design, coding conventions, redundancy, unused code or gate work,
read [references/patterns.md](references/patterns.md). Apply its cross-language
criteria alongside explicit project rules. These dimensions belong in a deep
repository audit; they are not automatic deletion or formatting rules.

## Finding format

Use a concise human-readable report by default. Include only applicable fields:

- Title, impact severity and evidence state.
- Source location, trigger and relevant entry-to-effect chain.
- Expected behavior and its source; actual behavior and consequence.
- Command/output or static reasoning; alternatives ruled out and remaining uncertainty.
- Root cause or unresolved assumptions, affected files, the smallest coherent
  correction that addresses the cause, and a verification case.

Separate confirmed findings, hypotheses and optional improvements. For dead-code
audits, report candidate and confirmed counts separately, with scanned scope.
For gates, distinguish **effective**, **redundant**, **ineffective**, **obsolete**
and **unresolved**. Never label a safeguard meaningless merely because it has
not failed recently.

Conclude with coverage and validation limits. Do not invent numerical confidence,
measurements, file locations or proof from illustrative examples.

## Helpers and targeted references

Resolve this skill directory from the loaded SKILL.md. Invoke helpers by their
absolute path and set the reviewed package using --root; do not assume the
target repository contains these scripts. Python 3.11+ is required.

See [scripts/README.md](scripts/README.md) for command contracts, exit codes,
temporary artifacts and test-evidence limits.

| Helper | Role |
|---|---|
| collect_facts.sh | Bounded file/build inventory and entry candidates |
| detect_duplicates.sh | Ordered line-block candidates, not semantic clone verdicts |
| dependency_audit.py | Shared manifest parsing; optional explicit dependency-count policy |
| verify_packages.py | Source-aware public registry name checks; offline mode available |
| run_correctness_test.sh | Existing test runner, fresh results, executed-test count and optional scale evidence |
| run_benchmark_comparison.sh | Two explicit commands, repeated alternating wall-time measurements |
| smoke_test.sh | Offline semantic regression tests for this skill's helpers |

Load only the relevant guide:
[C](references/c.md), [C++](references/cpp.md), [Rust](references/rust.md),
[Python](references/python.md), [JavaScript/TypeScript](references/javascript.md),
[database/storage](references/database-systems.md).

The helpers do not prove whole-program reachability or decide whether a business
gate is useful. Use project-aware analysis and the evidence procedure above.
