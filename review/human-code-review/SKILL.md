---
name: human-code-review
description: Review code changes or a requested code audit in separate passes for correctness, edge cases, useful tests, resource use, and clear English. Use for code review, not routine implementation or a security audit.
---

# Human Code Review

Review the code for people who must use and maintain it. Report concrete defects and necessary changes.

## Set the scope

Read the full task, acceptance criteria, architecture, and applicable AGENTS.md files. Record the base and head commits or audit commit.
Read the changed code, callers, data contracts, and relevant tests. For a full audit, list the covered paths and exclusions.
Use the project's commands, pinned tool versions, and lint settings. Read [the standards reference](references/standards.md) for the relevant technology.
Check current official guidance when a technology rule is uncertain. Do not add upgrades or broad refactors to satisfy a style preference.

## Review in separate passes

Complete each pass. Re-read the relevant code with that pass's purpose. A repeated scan with the same purpose is insufficient.

1. **Logic and contracts.** Trace inputs through state changes and outputs. Check errors, partial success, data ownership, and external side effects.
2. **Edge cases.** Check empty, missing, malformed, large, and boundary inputs. Check duplicate delivery, concurrent actions, cancellation, timeout, and restart where applicable.
3. **Tests.** Check whether tests prove observable behavior and important failure paths. Look for weak assertions, excessive mocks, flaky timing, and duplicate coverage.
4. **CPU and memory.** Trace loops, queries, copies, image buffers, caches, queues, streams, timers, subscriptions, and cleanup. Check bounds and object lifetimes.
5. **Readability and language.** Check names, control flow, necessary comments, and English prose. Apply the STE check below.

For a suspected defect, state the trigger and expected result. Confirm it with a focused test or a source path that demonstrates failure.
When changes arrive, review the new diff and affected paths again. Check whether each finding is resolved.

Do not delete a test because it is long or slow. Identify the behavior it protects first.
Remove or combine tests only when the remaining checks preserve that protection. Avoid tests that only repeat implementation details.
Prefer the smallest clear implementation. Measure performance when a result depends on workload or runtime behavior.
Report unmeasured costs as risks. Do not claim minimum CPU use, minimum memory use, or a speed improvement without evidence.

## Keep necessary comments

Keep explanations of non-obvious constraints, decisions, units, contracts, and external limits. Keep required public API documentation.
Preserve licenses, attribution, generated markers, build directives, lint directives, migration directives, and tool annotations.
Check a directive's purpose before changing it.

Remove comments that repeat a name or the next statement. Remove repeated prose, decorative banners, obsolete history, and empty assurances.
Shorten long text without losing the reason for a decision. Move a tutorial to documentation when readers still need it.
Do not infer author identity or quality from writing style. Do not set comment ratios or deletion quotas.
During a read-only review, report the cleanup. Edit code only when implementation is authorized.

## Check English against ASD-STE100

Use English for code comments, docstrings, new developer documentation, and review findings.
Use ASD-STE100 Issue 9 for this prose. Read the official rules and dictionary linked in the standards reference.
Use the approved meaning and part of speech. Check domain terms against an applicable technical category and the project glossary.
Use one term for one concept. Define a new technical term when its meaning is unclear.

Use at most 20 words for an instruction sentence and 25 for a descriptive sentence, counted under the standard.
Use direct instructions, clear conditions, and short paragraphs with one topic. Remove vague phrases and unnecessary emphasis.
Check grammar, terminology, and meaning as well as sentence length. A readability score or an AI rewrite does not prove conformity.

Preserve exact identifiers, commands, protocol values, quoted source text, and test data. Keep translations in their required locale.
Do not translate such literals to satisfy an English check. Report any unavoidable prose exception and its reason.
If the standard, dictionary, or required term decision is unavailable, report the STE check as incomplete.
Do not claim full conformity until the rules, dictionary, and terminology checks are complete. Do not claim ASD certification.
The language of the conversation remains the user's choice.

## Report the result

Give each actionable finding a priority, file location, trigger, impact, and proposed correction. Keep inline comments limited to necessary findings.
Combine repeated findings with the same cause. Omit praise, speculative warnings, and style comments without a clear benefit.
Separate defects from optional cleanup. For a large cleanup, propose one bounded task with concrete examples and acceptance checks.
State the commands run, results, pass coverage, and remaining limits. State when no actionable defect was found.
Do not imply that a static audit proves runtime behavior or the absence of all bugs.
