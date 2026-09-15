# Agent guidelines

Development guide for coding agents working on `job-template-run`. For what this
project is (the CRD/controller concept, installation, and example usage), read
[README.md](README.md) first; this file only covers how to develop on it.

## What this repo is, in one line

A Kubernetes controller (Python) plus a Helm chart that creates and manages
Kubernetes `Job`s from reusable templates, overriding only the parameters that
differ per run.

## Toolchain

- Python (see `pyproject.toml` `requires-python`); `uv` for dependencies
  (`uv run`, `uv.lock`).
- Lint/format: `ruff`. Types: `ty`. Unit tests: `pytest`.
- Kubernetes e2e: `kind` (local cluster), `helm` (chart in `charts/`), and
  `chainsaw` (declarative e2e tests under `tests/`).
- Controller entrypoint: `controller.py`.

## Common commands

See the [Makefile](Makefile) for the full set. Key targets:

| Command | Purpose |
| --- | --- |
| `make build` | Build the controller image and load it into kind. |
| `make lint` | `ty check` the Python and `helm lint` the chart. |
| `make test` | Run `pytest tests/test_controller.py` then `chainsaw test tests/`. |
| `make kind` / `make kind-down` | Create / delete the local kind cluster. |
| `make helm-install` / `make helm-uninstall` | Install / remove the chart. |
| `make dev-e2e` | Full e2e from inside the Cellbytes devcontainer (rewrites kubeconfig to kind's in-network address). |
| `make all` | kind + build + helm-install + test. |

Inside the unified Cellbytes devcontainer, use `make dev-e2e`: the container
cannot reach kind's host-published API port but is on the `kind` docker network,
and the target rewrites the kubeconfig accordingly. `make all` assumes a host
with direct access to kind.

## Python language server for coding agents

Coding agents get ty diagnostics and navigation for Python in-session, from a
Claude Code plugin this repo carries under `.claude/`. The devcontainer
registers it in `postCreateCommand`; to do it by hand:

```sh
claude plugin marketplace add <repo root>/.claude
claude plugin install ty-lsp@cellbytes-job-template-run
```

`.claude/lsp/ty-router.py` is a vendored copy owned by the `dev-env`
repo - read its README for what the router does and why. Change it there and
re-run that repo's `make sync-lsp-plugins`; do not edit the copy here.

## Conventions

- Only use ASCII characters in code and comments. No em-dashes, en-dashes,
  unicode arrows, or other special characters.
- Prefer a functional style; avoid classes.
- Lint and typecheck both the Python (`ruff`, `ty`) and the chart
  (`helm lint`) when you touch either side.

## Before finishing a change

Ensure no lint or type errors and the tests pass:

```sh
uv run ruff check && uv run ruff format --check && make lint && make test
```

## Commit messages

Commits follow [conventional commits](https://www.conventionalcommits.org/), so
that commitizen can derive the version bump and the changelog entry from them.

Docs-only commits (nothing but Markdown or other documentation changed) carry
`[skip ci]` in the commit message body. There is nothing for CI to verify, and
the run would otherwise cut a release and bump the version for a change that
ships no new behaviour.

GitHub reads `[skip ci]` from the tip commit of a push and skips that whole
push, not just the one commit. Only use it when every commit being pushed is
docs-only: a docs commit stacked on top of code commits silently skips CI for
the code as well.
