# Review instructions

Read the full issue and applicable local instructions before work.
For Domovoy tasks, also read the [shared project rules](https://github.com/nd2go/domovoy/blob/main/AGENTS.md) and architecture.
Do not change the task scope or dependencies during review.

## Code Review Rules

For every code review, read [Human Code Review](review/human-code-review/SKILL.md) and its relevant standards reference.
Complete separate passes for logic, edge cases, tests, CPU and memory, and readability.
Use English and ASD-STE100 Issue 9 for new code comments, docstrings, developer prose, and review findings.
Preserve exact literals and required translations. Keep necessary API documentation, constraints, licenses, and tool directives.
Report concrete findings, commands, results, and remaining limits. Do not claim STE conformity without the required checks.

The skill is stored locally so the review workflow also works without access to the private planning repository.
Its source is `nd2go/domovoy:review/human-code-review`. Keep the copies aligned when the shared policy changes.
