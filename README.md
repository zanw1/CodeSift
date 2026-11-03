# CodeSift

**Evidence-based code review for coding agents.**

CodeSift helps a coding agent uncover defects that survive a superficial review:
unreachable fast paths, broken contracts, redundant work, unused code, and checks
that appear to protect a system but do not affect its behavior.

It combines a reusable `SKILL.md` workflow, language-specific guidance, and small
evidence-collection helpers. The agent investigates the code, explains its
findings, proposes concrete changes, and waits for your approval before editing.

[Install](#install) · [Usage](#usage) · [Measured results](#measured-results) ·
[Review contract](SKILL.md) · [GitHub](https://github.com/zanw1/CodeSift)

## What CodeSift reviews

| Area | Questions it investigates |
|---|---|
| System behavior | Do callers, error handling, state transitions, and persistence agree across module boundaries? |
| Performance | Does the intended fast path run? Are storage reads, allocations, copies, locks, or recomputations repeated unnecessarily? |
| Dead and unused code | Can supported entry points reach this code? Is it an isolated cycle, test-only implementation, obsolete branch, or externally consumed API? |
| Ineffective gates | Can a check fail? Does failure reach a consumer that acts on it? Can empty tests, swallowed errors, or stale results appear successful? |
| Function design | Are responsibilities, return contracts, side effects, ownership, naming, and comments clear and consistent with the project? |
| Proportionality | Does each abstraction, dependency, fallback, and validation layer serve a concrete requirement? |
| Test and benchmark integrity | Do assertions come from independent contracts? Are workloads comparable, measurements real, and adverse results retained? |

CodeSift distinguishes **reproduced findings**, **statically established findings**,
and **hypotheses**. Impact severity is assessed separately from evidence strength.
An unused-symbol warning or a repeated code block is a candidate to investigate,
not automatic permission to delete or rewrite code.

## Review first, change after approval

1. **Investigate** the requested scope using source, contracts, call paths, and
   relevant checks.
2. **Explain** each actionable finding, its evidence, affected files, proposed
   correction, and validation plan.
3. **Wait for approval** of the presented plan or selected items.
4. **Implement** only the approved scope. Existing approval is not requested again;
   material additions are presented separately.
5. **Verify** the changes, report actual results and remaining limits, and clean
   temporary artifacts.

The workflow is defined in [SKILL.md](SKILL.md). Your agent remains responsible
for following it; execution permissions are controlled by the host environment.

## Measured results

Two anonymized local case studies demonstrate specific fixes identified through
this review workflow. 

### Case A: redundant reads in a JSON-backed lookup

A conditional lookup loaded the same storage twice. Reusing the already loaded
snapshot removed duplicate deserialization while preserving the returned records.

| Metric, 10,000-document first-match workload | Before | After |
|---|---:|---:|
| Median latency per operation | 7.272 ms | **3.661 ms** |
| Storage reads per conditional operation | 2 | **1** |
| Peak traced Python allocation | 8,344,712 bytes | **4,685,686 bytes** |

That workload showed a **1.99x ratio of medians**, **49.66% lower latency**, and
**43.85% lower peak traced Python allocation**. The allocation measurement is a
separate `tracemalloc` probe, not process RSS.

Across 12 JSON-backed conditional-query cells, ratios of medians ranged from
**1.86x to 1.99x**. The functional suite passed **260 tests**, including 32 new
storage-boundary regression cases that failed before the fix.

### Case B: required persistence hidden inside assertions

Required metadata writes were evaluated inside assertions. Disabling assertions
removed the writes, so records that were readable before closing became
inaccessible after reopening. Moving the writes and their error checks into
unconditional execution restored the tested behavior.

| Correctness check | Before | After |
|---|---:|---:|
| Close/reopen cases with assertions disabled | 0/10 passed | **10/10 passed** |
| Close/reopen controls with assertions enabled | 10/10 passed | **10/10 passed** |
| Metadata I/O failure with assertions disabled | Returned normally | **Failure surfaced** |

The checks covered different record counts, block sizes, insertion orders, and
deletions. No speedup is claimed from comparing against a version that omitted
required persistence work.

### Measurement conditions and limits

- Five paired runs in fresh processes, alternating which version ran first.
- Two storage backends, three dataset sizes, five operations, three warmups, and
  seven measured batches per cell: **2,100 timed batches** in total.
- Before/after values are medians of five process-level medians. Each process
  median summarizes seven batches; batches are not independent machines.
- Setup, output validation, read-call tracing, and allocation tracing were
  excluded from latency measurement.
- Hardware: Apple M4, 32 GiB RAM; macOS 27.2; Python 3.11.9. Native checks used
  Apple Clang 21 with identical optimization settings apart from the assertion flag.
- JSON files were in a warm OS cache. No CPU pinning or cold-cache claim was made.

Unaffected ID lookups were approximately unchanged. In-memory results were mixed,
including small median regressions and timing outliers; these were retained in
the underlying experiment records. These selected, single-host cases demonstrate
particular fixes, not a universal speedup or superiority over other review methods.
They do not certify whole-project correctness or establish review-time savings.

## Install

CodeSift is distributed as a standalone skill folder. The instructions below
target Codex's local skill discovery; other compatible agents should use their
own documented skill directory.

### Requirements

- Codex or another agent that supports `SKILL.md` skills.
- Git for cloning this repository.
- Python 3.11+ and Bash in a POSIX environment to run the bundled helpers.
- The reviewed project's normal tools when needed: for example pytest, Cargo,
  npm, or CTest. Helpers do not install missing tools automatically.

### User-wide installation in Codex

```bash
mkdir -p "$HOME/.agents/skills"
git clone https://github.com/zanw1/CodeSift.git "$HOME/.agents/skills/codesift"
```

The installed entry point should be:

```text
~/.agents/skills/codesift/SKILL.md
```

Codex discovers local skills in `~/.agents/skills` and repository
`.agents/skills` directories. It detects changes automatically; restart Codex if
the skill does not appear. See the [official skill documentation](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).

### Install for one repository

From a local CodeSift checkout, copy the skill package into the target project:

```bash
mkdir -p "/path/to/project/.agents/skills/codesift"
cp -R SKILL.md agents references scripts "/path/to/project/.agents/skills/codesift/"
```

Replace `/path/to/project` with your repository path. This copies the skill
resources without embedding a second Git repository. Use either a user-wide or
a project-local installation to avoid duplicate entries.

### Optional installation check

For the user-wide installation:

```bash
bash "$HOME/.agents/skills/codesift/scripts/smoke_test.sh"
```

The regression suite uses temporary fixtures and reports unavailable optional
integration tools as skipped. A skipped check is not a verified result.

## Usage

Open the project you want reviewed and mention the installed skill in Codex:

```text
$codesift Review this repository for systemic defects, redundant work,
unused code, and ineffective gates. List the evidence and proposed fixes
before changing any files.
```

For a smaller review:

```text
$codesift Review the changes on this branch. Trace affected callers and
contracts, and propose the smallest coherent fixes.
```

For a focused investigation:

```text
$codesift Investigate repeated storage reads on the lookup path.
Measure the relevant workload and explain a fix before implementing it.
```

After reviewing the findings, approve all or selected items:

```text
Approve R1 and R3. Implement those changes and run their validation plan.
```

You can also select a skill with `/skills` in Codex CLI or the IDE extension,
as described in the [official invocation guidance](https://learn.chatgpt.com/docs/build-skills#how-chatgpt-and-codex-use-skills).

## Package contents

| Path | Purpose |
|---|---|
| [SKILL.md](SKILL.md) | Review workflow, approval boundary, evidence rules, and finding format |
| [agents/openai.yaml](agents/openai.yaml) | CodeSift display metadata and default invocation prompt |
| [references/patterns.md](references/patterns.md) | Cross-language standards, dead-code analysis, redundancy, and gate effectiveness |
| `references/` | Focused C, C++, Rust, Python, JavaScript/TypeScript, and storage-system guidance |
| [scripts/README.md](scripts/README.md) | Helper commands, prerequisites, result states, and evidence limits |
| `scripts/` | Project facts, duplicate-block candidates, dependency checks, test and benchmark runners |

The review guidance is language-aware. Structural graph tools can strengthen
call-path analysis when available; direct source inspection remains necessary
for coverage gaps and dynamic behavior. Bundled helpers provide observations,
while the review connects them to the project's actual requirements.
