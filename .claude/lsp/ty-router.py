#!/usr/bin/env python3
"""Route LSP traffic to one `ty server` per Python project.

A ty server instance resolves exactly one project: the environment, the
`[tool.ty]` rule set and the include/exclude filters all come from the single
`pyproject.toml` at the root it was initialized with, and nested project files
below that root are ignored. A client that opens one root over a tree holding
several projects therefore type-checks all of them against one venv, which
mostly shows up as spurious `unresolved-import`.

Claude Code initializes an LSP server with a single workspace folder and
declares no `workspace/workspaceFolders` support, so it cannot express the
multi-project layout of the Cellbytes workspace on its own. This router sits in
between: it speaks one LSP session upstream and spawns a ty child per project
underneath, picking the child for a request from the document it names.
"""

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

PROJECT_MARKERS = ("pyproject.toml", "ty.toml")

# Requests that name no document. `workspace/symbol` is answered by every child
# and the results concatenated; the rest go to the child that answered
# `initialize`, which is the only one guaranteed to exist.
FANOUT_REQUESTS = frozenset({"workspace/symbol"})
BROADCAST_NOTIFICATIONS = frozenset({"workspace/didChangeConfiguration"})


def log(message: str) -> None:
    """Write a router diagnostic to stderr, where the LSP client collects it."""
    print(f"[ty-router] {message}", file=sys.stderr, flush=True)


def read_frame(stream: Any) -> dict[str, Any] | None:
    """Read one `Content-Length` framed JSON-RPC message, or None at EOF."""
    length = 0
    while True:
        line = stream.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        name, _, value = line.decode("ascii", "replace").partition(":")
        if name.strip().lower() == "content-length":
            length = int(value.strip())
    if length == 0:
        return None
    body = b""
    while len(body) < length:
        chunk = stream.read(length - len(body))
        if not chunk:
            return None
        body += chunk
    return json.loads(body)


def write_frame(stream: Any, lock: threading.Lock, message: dict[str, Any]) -> None:
    """Write one framed JSON-RPC message, serialized against other writers."""
    body = json.dumps(message).encode()
    with lock:
        stream.write(b"Content-Length: %d\r\n\r\n%s" % (len(body), body))
        stream.flush()


def uri_to_path(uri: str) -> Path | None:
    """Convert a `file://` URI to a local path, or None for anything else."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    return Path(unquote(parsed.path))


def find_project_root(path: Path, fallback: Path) -> Path:
    """Return the nearest ancestor of `path` that roots a Python project.

    Nearest wins so that a project nested inside another (inference under the
    Django server) claims its own files. `fallback` is the workspace root, used
    for files that sit outside any project.
    """
    for candidate in [path, *path.parents]:
        if not candidate.is_dir():
            continue
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return fallback


def resolve_ty_command(root: Path) -> list[str]:
    """Pick the ty binary for `root`, preferring the project's own venv.

    Projects pin different ty versions, and the point of the router is that a
    file is checked the way `make typecheck` in its repo checks it.
    """
    local = root / ".venv" / "bin" / "ty"
    if local.is_file():
        return [str(local), "server"]
    found = shutil.which("ty")
    if found:
        return [found, "server"]
    raise RuntimeError(f"no ty binary for project {root}")


def child_environment(root: Path) -> dict[str, str]:
    """Build the child env, pointing ty at the project's own virtualenv.

    Not every project sets `[tool.ty.environment] python`, and without a
    `VIRTUAL_ENV` those fall back to whatever interpreter ty itself runs under,
    which resolves the wrong third-party packages.
    """
    env = dict(os.environ)
    venv = root / ".venv"
    if venv.is_dir():
        env["VIRTUAL_ENV"] = str(venv)
        env["PATH"] = f"{venv / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    else:
        env.pop("VIRTUAL_ENV", None)
    return env


def project_params(params: dict[str, Any], root: Path) -> dict[str, Any]:
    """Re-aim the client's initialize params at a single project root."""
    uri = root.as_uri()
    return {
        **params,
        "rootPath": str(root),
        "rootUri": uri,
        "workspaceFolders": [{"uri": uri, "name": root.name}],
    }


def start_child(state: dict[str, Any], root: Path) -> dict[str, Any]:
    """Spawn and handshake a ty server for `root`, then serve it from cache."""
    key = str(root)
    child = state["children"].get(key)
    if child is not None:
        return child

    proc = subprocess.Popen(
        resolve_ty_command(root),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=None,
        cwd=str(root),
        env=child_environment(root),
    )
    child = {"proc": proc, "root": root, "lock": threading.Lock()}

    handshake_id = f"ty-router-initialize:{key}"
    write_frame(
        proc.stdin,
        child["lock"],
        {
            "jsonrpc": "2.0",
            "id": handshake_id,
            "method": "initialize",
            "params": project_params(state["init_params"], root),
        },
    )
    capabilities: dict[str, Any] = {}
    while True:
        message = read_frame(proc.stdout)
        if message is None:
            raise RuntimeError(f"ty server for {root} exited during initialize")
        if message.get("id") == handshake_id:
            capabilities = message.get("result", {}).get("capabilities", {})
            break
    write_frame(
        proc.stdin,
        child["lock"],
        {"jsonrpc": "2.0", "method": "initialized", "params": {}},
    )

    child["capabilities"] = capabilities
    state["children"][key] = child
    threading.Thread(target=pump_child, args=(state, child), daemon=True).start()
    log(f"started ty for {root}")
    return child


def send_to_child(child: dict[str, Any], message: dict[str, Any]) -> None:
    write_frame(child["proc"].stdin, child["lock"], message)


def send_upstream(state: dict[str, Any], message: dict[str, Any]) -> None:
    write_frame(state["stdout"], state["stdout_lock"], message)


def collect_fanout(state: dict[str, Any], message: dict[str, Any]) -> None:
    """Merge one child's answer into a pending fan-out and reply when complete."""
    with state["fanout_lock"]:
        pending = state["fanout"].get(message["id"])
        if pending is None:
            return
        result = message.get("result")
        if isinstance(result, list):
            pending["results"].extend(result)
        pending["outstanding"] -= 1
        if pending["outstanding"] > 0:
            return
        del state["fanout"][message["id"]]
    send_upstream(
        state, {"jsonrpc": "2.0", "id": message["id"], "result": pending["results"]}
    )


def pump_child(state: dict[str, Any], child: dict[str, Any]) -> None:
    """Forward everything a ty child emits upstream, renumbering its requests."""
    while True:
        message = read_frame(child["proc"].stdout)
        if message is None:
            log(f"ty for {child['root']} closed its output")
            return
        if "method" in message and "id" in message:
            with state["upstream_lock"]:
                router_id = state["next_upstream_id"]
                state["next_upstream_id"] += 1
                state["upstream"][router_id] = (child, message["id"])
            send_upstream(state, {**message, "id": router_id})
            continue
        if "method" not in message and message.get("id") in state["fanout"]:
            collect_fanout(state, message)
            continue
        send_upstream(state, message)


def routing_uri(message: dict[str, Any]) -> str | None:
    """Return the document URI a message is about, if it names one."""
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    for key in ("textDocument", "item"):
        holder = params.get(key)
        if not isinstance(holder, dict):
            continue
        uri = holder.get("uri")
        if isinstance(uri, str):
            return uri
    return None


def child_for_message(state: dict[str, Any], message: dict[str, Any]) -> dict[str, Any]:
    """Pick the ty child that owns the document a message names.

    `didOpen` is what binds a document to a project; later messages reuse that
    binding so a file keeps its server even if the project layout changes under
    it mid-session.
    """
    uri = routing_uri(message)
    if uri is None:
        return state["children"][str(state["seed_root"])]
    known = state["documents"].get(uri)
    if known is not None:
        return start_child(state, known)
    path = uri_to_path(uri)
    root = (
        state["seed_root"]
        if path is None
        else find_project_root(path.parent, state["seed_root"])
    )
    state["documents"][uri] = root
    return start_child(state, root)


SCAN_SKIP = frozenset(
    {".venv", "node_modules", ".git", "__pycache__", "data", "test-data"}
)
SCAN_DEPTH = 3


def discover_primary_project(root: Path) -> Path:
    """Pick the project that answers `initialize` and any document-less request.

    The workspace root of the unified container holds no project of its own, and
    handing that root to ty would make it index every repo at once. A shallow
    scan for the nearest project keeps that first server small; which project
    wins only matters for `initialize` capabilities and `workspace/symbol`,
    both of which every ty child answers the same way.
    """
    if any((root / marker).exists() for marker in PROJECT_MARKERS):
        return root
    frontier = [(root, 0)]
    while frontier:
        current, depth = frontier.pop(0)
        if depth >= SCAN_DEPTH:
            continue
        for entry in sorted(current.iterdir()):
            if (
                not entry.is_dir()
                or entry.name.startswith(".")
                or entry.name in SCAN_SKIP
            ):
                continue
            if any((entry / marker).exists() for marker in PROJECT_MARKERS):
                return entry
            frontier.append((entry, depth + 1))
    return root


def handle_request(state: dict[str, Any], message: dict[str, Any]) -> None:
    method = message["method"]
    if method == "shutdown":
        for child in list(state["children"].values()):
            send_to_child(
                child,
                {
                    "jsonrpc": "2.0",
                    "id": f"ty-router-shutdown:{child['root']}",
                    "method": "shutdown",
                },
            )
        send_upstream(state, {"jsonrpc": "2.0", "id": message["id"], "result": None})
        return
    if method in FANOUT_REQUESTS:
        children = list(state["children"].values())
        with state["fanout_lock"]:
            state["fanout"][message["id"]] = {
                "outstanding": len(children),
                "results": [],
            }
        for child in children:
            send_to_child(child, message)
        return
    send_to_child(child_for_message(state, message), message)


def handle_notification(state: dict[str, Any], message: dict[str, Any]) -> None:
    method = message["method"]
    if method == "exit":
        for child in list(state["children"].values()):
            send_to_child(child, message)
            child["proc"].terminate()
        sys.exit(0)
    if method == "initialized":
        return
    if method in BROADCAST_NOTIFICATIONS:
        for child in list(state["children"].values()):
            send_to_child(child, message)
        return
    child = child_for_message(state, message)
    send_to_child(child, message)
    if method == "textDocument/didClose":
        state["documents"].pop(routing_uri(message) or "", None)


def handle_client_response(state: dict[str, Any], message: dict[str, Any]) -> None:
    """Send the client's answer back to whichever child asked the question."""
    with state["upstream_lock"]:
        entry = state["upstream"].pop(message["id"], None)
    if entry is None:
        return
    child, child_id = entry
    send_to_child(child, {**message, "id": child_id})


def main() -> None:
    stdin = sys.stdin.buffer
    state: dict[str, Any] = {
        "stdout": sys.stdout.buffer,
        "stdout_lock": threading.Lock(),
        "children": {},
        "documents": {},
        "fanout": {},
        "fanout_lock": threading.Lock(),
        "upstream": {},
        "upstream_lock": threading.Lock(),
        "next_upstream_id": 1,
    }

    initialize = read_frame(stdin)
    if initialize is None or initialize.get("method") != "initialize":
        raise RuntimeError("expected an initialize request first")
    params = initialize.get("params", {})
    state["init_params"] = params
    workspace = uri_to_path(params.get("rootUri") or "") or Path(
        params.get("rootPath") or Path.cwd()
    )
    state["seed_root"] = discover_primary_project(workspace)
    log(f"workspace {workspace}, primary project {state['seed_root']}")

    seed = start_child(state, state["seed_root"])
    send_upstream(
        state,
        {
            "jsonrpc": "2.0",
            "id": initialize["id"],
            "result": {
                "capabilities": seed["capabilities"],
                "serverInfo": {"name": "ty-router", "version": "1"},
            },
        },
    )

    while True:
        message = read_frame(stdin)
        if message is None:
            return
        if "method" not in message:
            handle_client_response(state, message)
        elif "id" in message:
            handle_request(state, message)
        else:
            handle_notification(state, message)


if __name__ == "__main__":
    main()
