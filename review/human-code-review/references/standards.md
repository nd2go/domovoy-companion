# Review references

Use the rules for the technology version in the project. The project configuration controls formatting and enabled lint checks.
A new framework release does not require an upgrade during review. Check official sources when a rule can have changed.

## English

[ASD-STE100](https://www.asd-ste100.org/) is Simplified Technical English. Issue 9 was released on 15 January 2025.
Use the [official standard](https://www.asd-ste100.org/assets/files/ASD-STE100_ISSUE9.pdf), including its dictionary.
Get a current copy from the [official downloads page](https://www.asd-ste100.org/STE_downloads.html) if the direct link changes.
Do not copy the manual or dictionary into this skill. The summary in SKILL.md does not replace the standard.

Use a project glossary for permitted technical nouns and verbs. Record the term, meaning, category, and source or maintainer decision.
Product names and identifiers must retain their exact form. Technical terms must not become an exception for all unfamiliar words.

## Technology

- Python: [PEP 8 comments](https://peps.python.org/pep-0008/#comments) and [PEP 257 docstrings](https://peps.python.org/pep-0257/).
  Keep useful public interfaces documented. Follow the project's Ruff and type-check settings.
- Go: [Go doc comments](https://go.dev/doc/comment) and [Go formatting](https://go.dev/doc/effective_go#formatting).
  Preserve exported API comments and generated API annotations. Use the project's Taskfile and lint commands.
- TypeScript: [narrowing](https://www.typescriptlang.org/docs/handbook/2/narrowing.html) and the project's strictness settings.
  Check null values, unions, and error paths without unsafe casts.
- Svelte: [effect lifecycle](https://svelte.dev/docs/svelte/$effect).
  Check cleanup and repeated work. Use documentation for the Svelte version in the project.
- Vue and Nuxt: [Vue performance guidance](https://vuejs.org/guide/best-practices/performance.html).
  Check reactive work, list rendering, and resource cleanup. Use the project's Nuxt and lint configuration.
- Docker: [resource constraints](https://docs.docker.com/engine/containers/resource_constraints/).
  Check the actual daemon, architecture, container limits, and host access when relevant.

These sources guide review. They do not establish a universal community rule for every design choice.
