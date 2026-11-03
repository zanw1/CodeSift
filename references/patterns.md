# Systemic review patterns

Use these patterns to form testable candidates, not automatic findings.
The evidence/impact definitions in SKILL.md are authoritative.

## Contents

- Cross-language coding standards
- Reachability and zombie code
- Redundancy
- Gate effectiveness
- Performance reasoning
- Other systemic candidates
- Sources and case-study provenance

## Cross-language coding standards

Apply explicit user and repository rules in their stated scope. Use the criteria
below to investigate observable behavior and maintainability; do not turn them
into automatic token bans or impose one language's syntax on another.
All proposed changes still require the review-and-approval workflow in SKILL.md.

### Functions, types and comments

- Keep functions focused on one responsibility. Identify inputs, outputs,
  side effects, ownership and failure behavior. Prefer clear guard clauses over
  deeply nested branches when they improve the actual control flow.
- Make public parameter and return contracts explicit where the language supports
  useful declarations. Respect established inference for local expressions and
  callbacks; do not universally ban inferred types or idiomatic implicit returns.
- Use meaningful, consistent names and named constants for domain values.
  Prefer immutable values when mutation is unnecessary. Choose numeric types from
  precision, range, interoperability and measured resource needs, not a global
  preference for one floating-point width.
- Use function-level comments to explain purpose, non-obvious invariants and
  important error or resource contracts. Do not restate obvious operations.
  Function/file length is a review signal unless the project defines a binding
  limit; do not split coherent code solely to meet an invented line count.

### Responsibilities and proportionality

Check for existing implementations before adding code. Keep each business rule
in one owning implementation and prefer clear typed models or enums when they
represent stable domain concepts. Use declarative mappings for genuinely tabular
logic, and procedural code when ordering or side effects is clearer.

Prefer the smallest coherent change that resolves the root cause. Do not stack
fallbacks on a broken contract, or expand a local fix into an unrelated redesign.
Prefer existing files; justify a new module by a concrete responsibility rather
than adding scaffolding, documentation or examples without a task need.

### Errors, validation and data preservation

Use the project's error model: exceptions, Result-like values or explicit error
returns as appropriate. Distinguish a valid empty result from a failed operation.
Preserve useful diagnostic context without exposing secrets. Catch a failure only
to recover under a real contract, add context or translate it at a defined boundary.
Never fabricate success or silently substitute defaults to make a check pass.

Validate actual API, persistence and workload contracts. Justify added guards,
caps, retries and fallback paths in the approval plan, including their failure
behavior. Keep necessary authorization and integrity protections. Do not create
speculative security layers or a catalog of unreachable failure scenarios.
Data limits and truncation must be explicit and visible to consumers, with a
continuation or recovery path when complete data is required.

Use exact equality and ordinary data structures where they express the contract.
Do not invent cryptographic fingerprints or identity wrappers merely to replace
ordered keys or exact content comparison. Existing checksum/content-identity
contracts may justify them; ordinary hash-table use is not itself a finding.

### Resource and concurrency ownership

Define who closes resources and owns background work. Use the language's scoped
cleanup mechanisms, such as RAII, context managers, defer or try/finally, including
failure paths. Make cancellation and task completion observable. Check shared
mutable state, synchronization and driver thread-safety against actual usage.
Avoid unnecessary lock retention across slow I/O. Retries need a defined transient
failure and safe replay/idempotency contract; they must not hide persistent faults.

### Tests and scenario fidelity

Derive assertions from an external behavior contract, invariant or reproduced
bug. For behavior fixes, run a focused case and establish the expected failure
before changing production code when feasible. Record unavailable reproduction
as a limit; never call an environment failure a reproduced bug.

Verify the scenario the user requested at the relevant boundary. Backend tests
alone do not establish a frontend interaction. Keep test oracles independent of
the implementation strategy and use real integration cases where persistence or
concurrency behavior matters. Reuse the project's test framework rather than
creating a custom one without a demonstrated need.

Run the focused case and affected targets first. Run broader regression checks
when the related work is complete or unresolved impact requires them. For
documentation, translation and behavior-preserving refactors, use appropriate
existing checks; do not manufacture failing tests for unchanged behavior.

### Scope, dependencies and experiments

Define observable completion criteria and relationships between subtasks so work
does not drift into an unverifiable collection of changes. Read relevant library
documentation for the actual version and inspect implementation paths when their
semantics or capabilities matter; a superficial API match is insufficient.

Start optimization with a bottleneck hypothesis and finish with a justified
keep/revert decision. Preserve correctness, workload semantics, baseline behavior
and failed or slow measurements. Never hardcode answers, query another system's
oracle at runtime, shrink one side's workload, or edit results to create a gain.
Separate cold/warm cache and startup/steady-state measurements where relevant.
Change one explanatory variable at a time when isolating a cause; identify any
compound change in the approved plan and limit causal claims accordingly.

## Reachability and zombie code

**Question:** Can a supported production or external consumer reach this code?

1. Enumerate roots within the requested scope: binaries, API/CLI routes, public
   library exports, workers, scheduled jobs, plugins, callbacks, reflection,
   FFI, serialization hooks, migrations, build scripts and configuration/assets.
2. Separate production, test, generated, vendored and intentionally retained
   compatibility code. Tests calling a function do not establish production use.
3. Follow edges from roots. Search both incoming and outgoing references.
   Include configuration strings and registration tables as well as direct calls.
4. Examine disconnected strongly connected components: A calls B and B calls A
   can be an unreachable island despite both having callers.
5. Check build flags, platform variants, dynamic imports and external consumers.
   An exported library API with no local caller is unresolved until its contract
   and consumers are known. Zero observed coverage proves only non-observation.
6. For a deletion candidate, record scope, roots, source locations, references,
   exclusions, purpose and remaining uncertainty. If removal is authorized,
   validate relevant builds/tests, packaging and public contracts afterward.

Use existing analysis tools as supporting evidence: Knip for configured JS/TS
projects; Vulture for Python candidates; compiler unused/unreachable diagnostics
for native/Rust code. Check tool version/configuration and complete output.
Do not auto-install a tool or treat its confidence score as a proof.
Do not introduce broad whitelists solely to make the report green.

**Report separately:** confirmed unused code, unresolved candidates, code used
only by tests, and intentionally retained public/compatibility paths. Count
files/symbols and analyzed scope; avoid a universal "too much dead code" percentage.

**Illustrative cases, not reproduced project findings:**

- An unregistered handler has no reachable caller or supported external entry:
  a deletion candidate after checking framework discovery and consumers.
- A decorated callback has no direct caller but is registered at import:
  reachable; deleting it would break behavior.
- Two legacy modules call each other but no active root reaches them:
  examine them as one island instead of clearing each because it has callers.
- A dormant emergency kill switch has an operational owner and tested effect:
  retention may be justified even if recent traffic never used it.

## Redundancy

Distinguish these situations before proposing extraction or removal:

| Candidate | Evidence / decision |
|---|---|
| Copy-paste drift | Same responsibility and contract, unintended behavior differences |
| Duplicate calculation/validation | Same input and invariant, overlapping cost, no intervening mutation/trust boundary |
| Thin wrapper | Remove only if it adds no stable interface, adaptation, ownership or isolation |
| Parallel implementations | Check platform, protocol and compatibility requirements before consolidation |
| Unused abstraction/configuration | Check consumers and deletion impact using the reachability procedure |

Line/block matches nominate pairs for inspection. Small wrappers, boilerplate
and similar tests can be intentional. Prefer one owning implementation when the
contract is truly shared; do not create a generic framework to eliminate a few lines.

For performance cost, measure duplicate work on the active path. For maintenance
cost, show the duplicated decision and observed or plausible drift mechanism.

## Gate effectiveness

Here, a gate includes CI checks, preconditions, runtime validation, feature flags,
readiness checks, release criteria and algorithm-selection thresholds.

For each relevant gate, identify:

| Field | Evidence to collect |
|---|---|
| Purpose | The protected invariant or operational decision and its owner/requirement |
| Input | Data/revision/configuration actually inspected, including freshness and scope |
| Decision | Predicate, threshold source, reachable pass and fail branches |
| Enforcement | Exit/status propagation and the consumer that blocks, retries or falls back |
| Cost | Latency, resources and maintenance relative to the risk it reduces |
| Lifecycle | Supported variants, rollback/kill-switch role and retirement condition |

Use contrasting cases: a valid input passes; a known violation fails; missing,
empty, skipped, timed-out or stale evidence does not silently satisfy the gate.
Run mutations/negative cases in a disposable fixture, not production.

**Common ineffective gates:**

- A check fails but `|| true`, broad exception handling or `continue-on-error`
  masks it before the decision-making consumer.
- "Tests passed" means only exit 0, zero tests, all skipped tests or a stub command.
- A benchmark gate has no equivalent baseline, ignores failed samples, or times
  unrelated work such as one command's build versus another's steady state.
- A regex checks that a function/configuration exists, not that it is reachable
  or enforces the intended behavior.
- A quality threshold has no stated requirement and no action tied to failure.
- A check emits a report that no downstream decision reads.
- A feature/configuration flag is never read, cannot affect the outcome, or guards
  an obsolete branch with no remaining supported use.

**Do not overcorrect:** authorization, input validation at separate trust
boundaries, idempotency, rate limits, integrity checks and rollout kill switches
may be necessary despite repetition or a high pass rate. A threshold without
documentation is a question to resolve, not automatically a useless gate.

For CI, inspect the full producer-to-consumer path: triggers, path filters,
conditions, matrix jobs, dependencies, final status and repository requirements.
GitHub documents different outcomes for a skipped workflow and a skipped job;
a green-looking check alone does not establish coverage of the requested revision.

Recommend one of: keep, repair, consolidate, retire, or investigate. Provide a
counterexample and affected requirement before calling a gate ineffective.
Remove a safeguard only when its obligation remains covered or has been explicitly retired.

## Performance reasoning

For serial stages measured against the same baseline, total time is the sum of
stage times. If baseline fractions are p_i and stage slowdown factors s_i,
the normalized total is sum(p_i * s_i), with unchanged fractions included.
Same-path membership is not a justification for multiplying slowdown factors.
Nested iteration can multiply work counts; establish its actual algorithm.

Example: two 10 ms stages each become 20 ms: total 20 → 40 ms, a 2x slowdown.
A stage occupying 1% of time becoming 5x slower gives 0.99 + 0.01*5 = 1.04x.

Validate comparable outputs and workload sizes; record environment, compiler
mode, warmup, samples, median/spread and relevant concurrency/cache state.
Use multiple sizes to investigate growth; a single ratio does not prove O(n)
or O(log n). CPU profiles alone do not account for blocked I/O wall time.
Use profiling and controlled changes to support causal attribution.

Do not weaken durability, isolation or authorization to win a benchmark.

## Other systemic candidates

- **Ghost fast path:** suitable inputs never reach the intended path. Verify
  dispatch and workload; some valid inputs should correctly take a slower path.
- **Contract drift:** callers rely on old return/error/default/serialization
  semantics. Inspect the actual interface boundary and affected variants.
- **Intent gap:** behavior disagrees with a documented requirement even though
  local tests pass. Do not infer domain requirements solely from function names.
- **Circular tests:** the oracle reuses the implementation under test, or a wrong
  implementation still passes. Try a targeted counterexample/mutation.
- **Dependency confusion:** declared identity, configured registry, resolved
  version and imported API disagree. Registry existence does not prove safety.
- **State rebuild / retry overhead:** repeated work or unbounded retries on a
  reachable path. Verify the reason, resource budget and failure behavior.

## Sources and provenance

Primary sources checked on 2026-11-03:

- [Knip entry files](https://knip.dev/explanations/entry-files) and
  [graph interpretation](https://knip.dev/explanations/how-knip-works):
  root/configuration gaps can cascade into false unused-code reports.
- [Vulture documentation](https://github.com/jendrikseipp/vulture):
  Python's dynamic behavior requires reviewing candidates and specific whitelists.
- [rustc dead_code](https://doc.rust-lang.org/rustc/lints/listing/warn-by-default.html#dead-code):
  unused, unexported items; this is not a whole-system public API usage proof.
- [GitHub required-check semantics](https://docs.github.com/en/pull-requests/how-tos/merge-and-close-pull-requests/troubleshooting-required-status-checks)
  and [workflow syntax](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax):
  trace skipped/failed jobs and their consumers.
- [Feature Toggles](https://martinfowler.com/articles/feature-toggles.html):
  distinguish flag purpose and lifecycle before retirement.
- [USENIX Security 2025 package-hallucination study](https://www.usenix.org/system/files/usenixsecurity25-spracklen.pdf):
  19.7% of recommended package references in that experiment were hallucinated,
  not 19.7% of all code samples or current code. Historical motivation only.

A real case study needs a source URL, revision, workload and evidence artifact.
Never present invented file locations, timings or profiles as reproduced results.
