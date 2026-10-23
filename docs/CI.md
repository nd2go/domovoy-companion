# CI for the Domovoy fork

[Domovoy CI task](https://github.com/nd2go/domovoy/issues/19).
The `CI` workflow runs for every pull request and every push to `main`, including
changes to documentation. It uses locked dependencies and the existing upstream
checks. No path filter can leave a required check pending.

| Check | Commands | Scope |
| --- | --- | --- |
| Python checks | `uv sync --locked --all-groups`; `uv run --no-sync pytest --junitxml=reports/python.xml`; `uv run --no-sync ruff check .`; `uv run --no-sync ty check --exit-zero-on-warning`; `uv run --no-sync vulture --min-confidence 70 --sort-by-size` | Unit and mocked integration tests, lint, types and unused code |
| Frontend checks | In `frontend/`: `npm ci`; `npm run check`; `npm run lint`; `npm run format:check`; `npm run build`; `npx playwright install --with-deps chromium`; `npm run test:browser -- --reporter=line,html` | Svelte checks, lint, formatting, build and mocked Chromium browser tests |
| Homebox live checks | `uv sync --locked --all-groups`; the explicit `pytest -m live` selection below | A disposable Docker Homebox with synthetic users and photos; no paid AI service |
| Required | Summarizes the three checks and accepts only three `success` results | Failure, cancellation, missing results and unexpected skips all fail |

Python checks use Python 3.14 and uv 0.12.5. Frontend checks use Node 22. Live
checks require a local Docker daemon. Match CI's selected tests when reproducing:

```sh
uv run --no-sync pytest -m live --junitxml=reports/homebox-live.xml \
  tests/test_homebox_client.py \
  tests/test_homebox_auth_live.py \
  tests/test_homebox_upstream_live.py \
  tests/test_inventory_schema_live.py \
  tests/test_companion_auth_live.py \
  tests/test_label_print_live.py \
  tests/test_mcp_tools.py
```

The live job sets `HBC_LLM_API_KEY=ci-sentinel-no-network` and
`HBC_LLM_MODEL=gpt-5-mini`. These are test configuration, not real credentials;
the selection excludes tests requiring a model provider. Printer tests use
synthetic commands and do not print physical labels. This workflow does not run
Codex, update QA, publish images or deploy. The inherited publishing job is
restricted to `Duelion/homebox-companion`, so it cannot publish from this fork.

Open the pull request's **Checks** tab for each failing command and its log.
**Required → Job summary** links the workflow run and shows each job's result.
When available, failed Python/live jobs retain their JUnit XML, and failed browser
tests retain the HTML report, traces and screenshots. Artifacts expire after
seven days. Upload paths allow only these reports, never environment files,
application state, credentials or private photos. Setup failures may have no
report; their step logs still explain the failure.

Every check has a timeout, uses read-only repository permissions and does not
persist checkout credentials. A newer push cancels the older run for the same
pull request or branch. Require the final `Required` job in repository branch
protection where available. Its workflow is `CI`; the exact context shown in
Checks must be selected in GitHub settings. A job named `Required` alone does not
enforce a merge restriction. Confirm protection in the repository before
claiming it is enforced, and do not merge until the current head is green and
the task's actual acceptance checks are complete. CI results do not verify a
physical phone, printer, real Codex recognition or deployment.
