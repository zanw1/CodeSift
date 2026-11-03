# Rust review

Use --lang rust for a selected package/workspace. Read patterns.md for unused
code, redundant implementations and gate effectiveness.

## Active-path review

Trace actual dispatch and callers through Result/Option conversion, async
cancellation, ownership and synchronization. Measure clone/allocation costs;
do not replace correct ownership with unsafe code to reduce an unmeasured cost.

Establish the durability contract before replacing sync_all with sync_data.
A safe-looking Arc/Mutex can be unnecessary or required by the surrounding API;
inspect actual sharing and synchronization.

## Unused code and dependencies

rustc dead_code diagnoses unused, unexported items. Public exports, macro expansion,
trait dispatch, derive-generated behavior, proc macros, build scripts, FFI and
registration tables need additional inspection. Search supported cfg, target and
feature combinations; mutually exclusive features cannot all be tested together.

Use existing cargo check/test configurations and compiler diagnostics. When
available, cargo-udeps can add dependency-usage evidence, with its toolchain and
false-positive limitations recorded. No local caller does not prove a public
crate API is unused.

Cargo metadata / the resolved lockfile provide better identity and source evidence
than regex matching. Account for renamed packages, path/git dependencies, registry
overrides, workspace inheritance and target/build/dev sections. The manifest helper
does not compute transitive usage; counts are not a bloat verdict.

## Gates

Inspect cfg branches, disabled backends, swallowed errors, ignored Results,
unbounded retries and flags whose value never reaches behavior. A compile-time
check can be valuable despite having no runtime branch.
A passing cargo test can execute zero tests; inspect counts, including doctests
and ignored tests, rather than only exit status.

## Validation

The helper's automatic runner uses cargo test --locked --offline and a temporary
target directory. Prepare dependencies/lockfiles through the project's normal
workflow beforehand; missing prerequisites remain an incomplete/failed check.
Supply an explicit --command for project-specific target/feature selections.

Profile optimized builds and separate build/startup cost from the workload.
Never infer production performance from debug builds alone.

Primary sources, checked 2026-11-03:
[rustc dead_code](https://doc.rust-lang.org/rustc/lints/listing/warn-by-default.html#dead-code),
[cargo check](https://doc.rust-lang.org/cargo/commands/cargo-check.html).
