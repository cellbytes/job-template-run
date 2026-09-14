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
