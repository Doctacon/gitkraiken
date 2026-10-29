#!/usr/bin/env python3
"""Reproducible, local-only M1 reference benchmark.

The default profile builds the complete approved reference envelope in a temporary
repository.  ``--smoke`` is intentionally smaller and is never acceptance evidence.
No user repository, Git configuration, credentials, application state, or network
remote is used.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import platform
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any

REFERENCE = {
    "commits": 100_000,
    "tracked_files": 20_000,
    "refs": 1_000,
    "changed_files": 200,
    "ordinary_diff_max_bytes": 200 * 1024,
}
SMOKE = {
    "commits": 500,
    "tracked_files": 500,
    "refs": 50,
    "changed_files": 20,
    "ordinary_diff_max_bytes": 64 * 1024,
}
TARGETS_SECONDS = {
    "app_feedback_p95": 0.100,
    "workspace_cold_max": 2.0,
    "workspace_warm_max": 1.0,
    "diff_presentation_max": 0.500,
}
LARGE_PATH = "tracked/f00000.txt"


@dataclass(frozen=True)
class TimedValue:
    wall_seconds: float
    cpu_seconds: float


def isolated_environment(root: Path) -> dict[str, str]:
    """Return a minimal environment with no user Git/app state or credentials."""
    blocked_exact = {
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "NETRC",
        "GIT_ASKPASS",
        "SSH_ASKPASS",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "AZURE_DEVOPS_EXT_PAT",
        "PYTHONSTARTUP",
    }
    blocked_fragments = ("TOKEN", "PASSWORD", "SECRET", "CREDENTIAL", "AUTH_SOCK")
    keep = {
        "PATH",
        "TMPDIR",
        "TMP",
        "TEMP",
        "SYSTEMROOT",
        "WINDIR",
        "LANG",
        "LC_CTYPE",
        "TERM_PROGRAM",
        "TERM_PROGRAM_VERSION",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in keep
        and key not in blocked_exact
        and not any(fragment in key.upper() for fragment in blocked_fragments)
    }
    home = root / "home"
    config = root / "config"
    state = root / "state"
    cache = root / "cache"
    for directory in (home, config, state, cache):
        directory.mkdir(parents=True, exist_ok=True)
    environment.update(
        {
            "HOME": os.fspath(home),
            "XDG_CONFIG_HOME": os.fspath(config),
            "XDG_STATE_HOME": os.fspath(state),
            "XDG_CACHE_HOME": os.fspath(cache),
            "RHIZOME_STATE_HOME": os.fspath(state / "rhizome"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.fspath(config / "gitconfig"),
            "GIT_TERMINAL_PROMPT": "0",
            "GIT_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS": "/usr/bin/false",
            "SSH_ASKPASS_REQUIRE": "never",
            "GCM_INTERACTIVE": "Never",
            "GIT_ALLOW_PROTOCOL": "file",
            "LC_ALL": "C",
            "TERM": "xterm-256color",
        }
    )
    (config / "gitconfig").write_text("", encoding="utf-8")
    return environment


def run(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        args,
        cwd=cwd,
        env=env,
        input=input_bytes,
        capture_output=True,
        check=True,
    )


def git(repo: Path, *args: str, env: dict[str, str]) -> bytes:
    return run(["git", "-C", os.fspath(repo), *args], env=env).stdout


def create_fixture(repo: Path, dimensions: dict[str, int], env: dict[str, str]) -> dict[str, Any]:
    """Create one reused 19,999-file tree plus a tiny changing history subtree."""
    started_wall = time.perf_counter()
    started_cpu = time.process_time()
    repo.mkdir()
    run(["git", "init", "-q", "-b", "main", os.fspath(repo)], env=env)
    for key, value in (
        ("user.name", "Rhizome Benchmark"),
        ("user.email", "benchmark@example.invalid"),
        ("commit.gpgsign", "false"),
        ("core.hooksPath", "/dev/null"),
        ("gc.auto", "0"),
    ):
        git(repo, "config", key, value, env=env)

    shared_blob = (
        run(
            ["git", "-C", os.fspath(repo), "hash-object", "-w", "--stdin"],
            env=env,
            input_bytes=b"",
        )
        .stdout.decode()
        .strip()
    )
    # The changing history/tick.txt is the final tracked file.
    immutable_count = dimensions["tracked_files"] - 1
    tree_input = bytearray()
    for number in range(immutable_count):
        tree_input.extend(f"100644 blob {shared_blob}\tf{number:05d}.txt\0".encode())
    immutable_tree = (
        run(
            ["git", "-C", os.fspath(repo), "mktree", "-z"],
            env=env,
            input_bytes=bytes(tree_input),
        )
        .stdout.decode()
        .strip()
    )

    importer = subprocess.Popen(
        ["git", "-C", os.fspath(repo), "fast-import", "--quiet", "--date-format=raw"],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    assert importer.stdin is not None
    base_time = 946684800  # 2000-01-01 UTC; deterministic and monotonically increasing.
    try:
        for number in range(dimensions["commits"]):
            message = f"benchmark commit {number:06d}".encode()
            tick = f"{number:012d}\n".encode()
            header = (
                f"commit refs/heads/main\n"
                f"committer Rhizome Benchmark <benchmark@example.invalid> "
                f"{base_time + number} +0000\n"
                f"data {len(message)}\n"
            ).encode()
            importer.stdin.write(header)
            importer.stdin.write(message + b"\n")
            importer.stdin.write(f"M 040000 {immutable_tree} tracked\n".encode())
            importer.stdin.write(b"M 100644 inline history/tick.txt\n")
            importer.stdin.write(f"data {len(tick)}\n".encode())
            importer.stdin.write(tick + b"\n")
        importer.stdin.close()
    except BrokenPipeError:
        pass
    stderr = importer.stderr.read() if importer.stderr is not None else b""
    returncode = importer.wait()
    if returncode:
        raise RuntimeError(
            f"git fast-import failed ({returncode}): {stderr.decode(errors='replace')}"
        )

    head = git(repo, "rev-parse", "refs/heads/main", env=env).decode().strip()
    updates = "".join(
        f"create refs/tags/benchmark/ref-{number:04d} {head}\n"
        for number in range(1, dimensions["refs"])
    ).encode()
    run(["git", "-C", os.fspath(repo), "update-ref", "--stdin"], env=env, input_bytes=updates)
    git(repo, "reset", "--hard", "-q", "refs/heads/main", env=env)

    # Leave room for one unified-diff prefix per ordinary 80-byte line plus headers.
    large_size = dimensions["ordinary_diff_max_bytes"] - 4 * 1024
    full_lines, remainder = divmod(large_size, 80)
    ordinary_text = (b"ordinary benchmark text " + b"x" * 55 + b"\n") * full_lines
    if remainder:
        ordinary_text += b"y" * max(0, remainder - 1) + b"\n"
    (repo / LARGE_PATH).write_bytes(ordinary_text)
    for number in range(1, dimensions["changed_files"]):
        (repo / "tracked" / f"f{number:05d}.txt").write_text(
            f"changed {number:05d}\n", encoding="utf-8"
        )

    return {
        "wall_seconds": time.perf_counter() - started_wall,
        "orchestrator_cpu_seconds": time.process_time() - started_cpu,
        "immutable_files": immutable_count,
        "immutable_tree_oid": immutable_tree,
        "changing_history_files": 1,
        "topology": "linear; immutable tracked subtree reused; history/tick.txt changes per commit",
    }


def nul_count(data: bytes) -> int:
    return len([field for field in data.split(b"\0") if field])


def verify_fixture(repo: Path, expected: dict[str, int], env: dict[str, str]) -> dict[str, Any]:
    commits = int(git(repo, "rev-list", "--count", "--all", env=env))
    tracked = nul_count(git(repo, "ls-files", "-z", env=env))
    refs = len(
        git(
            repo,
            "for-each-ref",
            "--format=%(refname)",
            "refs/heads",
            "refs/remotes",
            "refs/tags",
            env=env,
        ).splitlines()
    )
    status = git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", env=env)
    changed = nul_count(status)
    large_diff = git(
        repo,
        "diff",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--",
        LARGE_PATH,
        env=env,
    )
    remotes = git(repo, "remote", env=env).splitlines()
    actual = {
        "commits": commits,
        "tracked_files": tracked,
        "refs": refs,
        "changed_files": changed,
        "ordinary_diff_path": LARGE_PATH,
        "ordinary_diff_bytes": len(large_diff),
        "ordinary_diff_kib": len(large_diff) / 1024,
        "remotes": len(remotes),
    }
    failures = []
    for name in ("commits", "tracked_files", "refs", "changed_files"):
        if actual[name] != expected[name]:
            failures.append(f"{name}: expected {expected[name]}, got {actual[name]}")
    diff_floor = max(1, expected["ordinary_diff_max_bytes"] - 4 * 1024)
    if not diff_floor <= len(large_diff) <= expected["ordinary_diff_max_bytes"]:
        failures.append(
            f"ordinary diff: expected {diff_floor} through "
            f"{expected['ordinary_diff_max_bytes']} bytes, got {len(large_diff)}"
        )
    if remotes:
        failures.append("fixture unexpectedly has remotes")
    if failures:
        raise RuntimeError("fixture verification failed: " + "; ".join(failures))
    return actual


def rss_bytes() -> int:
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(raw if sys.platform == "darwin" else raw * 1024)


def current_rss_bytes() -> int | None:
    try:
        value = run(["ps", "-o", "rss=", "-p", str(os.getpid())]).stdout.decode().strip()
        return int(value) * 1024
    except (OSError, subprocess.CalledProcessError, ValueError):
        return None


def timed(callable_: Any) -> tuple[Any, TimedValue]:
    wall = time.perf_counter()
    cpu = time.process_time()
    result = callable_()
    return result, TimedValue(time.perf_counter() - wall, time.process_time() - cpu)


def worker_repository(repo_path: Path) -> dict[str, Any]:
    from rhizome.git import Repository

    launched = int(os.environ["RHIZOME_BENCH_LAUNCH_NS"])
    repository, constructor = timed(lambda: Repository(repo_path))
    snapshot, snapshot_time = timed(lambda: repository.snapshot(300))
    patch, diff_time = timed(lambda: repository.diff(LARGE_PATH))
    return {
        "launch_to_snapshot_seconds": (time.monotonic_ns() - launched) / 1_000_000_000,
        "constructor": asdict(constructor),
        "snapshot": asdict(snapshot_time),
        "diff": asdict(diff_time),
        "observed": {
            "history_rows": len(snapshot.history),
            "refs": len(snapshot.refs),
            "changed_files": len(snapshot.files),
            "diff_bytes": len(patch.encode("utf-8", "surrogateescape")),
        },
        "peak_rss_bytes": rss_bytes(),
        "current_rss_bytes": current_rss_bytes(),
    }


async def wait_for_idle(app: Any) -> None:
    async with asyncio.timeout(120):
        while app.busy or app.workers:
            await asyncio.sleep(0)


async def app_measurement(repo_path: Path, warm_runs: int) -> dict[str, Any]:
    from rhizome.app import RhizomeApp

    launched = int(os.environ["RHIZOME_BENCH_LAUNCH_NS"])
    wall = time.perf_counter()
    cpu = time.process_time()
    app = RhizomeApp(repo_path)
    async with app.run_test(size=(170, 48)) as pilot:
        await wait_for_idle(app)
        await pilot.pause(0)
        startup = TimedValue(time.perf_counter() - wall, time.process_time() - cpu)
        launch_to_usable = (time.monotonic_ns() - launched) / 1_000_000_000
        rss_after_startup = current_rss_bytes()

        feedback_wall = time.perf_counter()
        feedback_cpu = time.process_time()
        painted = asyncio.Event()
        app.status("Benchmark feedback")
        app.call_after_refresh(painted.set)
        await painted.wait()
        await pilot.pause(0)
        feedback = TimedValue(
            time.perf_counter() - feedback_wall, time.process_time() - feedback_cpu
        )

        warm_refreshes = []
        for _ in range(warm_runs):
            refresh_wall = time.perf_counter()
            refresh_cpu = time.process_time()
            app.request_refresh(quiet=True)
            await wait_for_idle(app)
            await pilot.pause(0)
            warm_refreshes.append(
                asdict(
                    TimedValue(
                        time.perf_counter() - refresh_wall,
                        time.process_time() - refresh_cpu,
                    )
                )
            )
        rss_after_refreshes = current_rss_bytes()

        assert app.snapshot_data is not None
        selected = next(change for change in app.snapshot_data.files if change.path == LARGE_PATH)
        diff_wall = time.perf_counter()
        diff_cpu = time.process_time()
        app.inspect_worktree_file(selected, False)
        await wait_for_idle(app)
        await pilot.pause(0)
        if app._patch_document is None:
            raise RuntimeError("app did not present the benchmark diff")
        presented_bytes = len(app._patch_document._raw_patch)
        expected_max = int(os.environ["RHIZOME_BENCH_DIFF_MAX_BYTES"])
        if not expected_max - 4 * 1024 <= presented_bytes <= expected_max:
            raise RuntimeError(
                f"app presented {presented_bytes} diff bytes; expected near {expected_max}"
            )
        diff = TimedValue(time.perf_counter() - diff_wall, time.process_time() - diff_cpu)
        rss_after_diff = current_rss_bytes()
        return {
            "launch_to_usable_seconds": launch_to_usable,
            "startup": asdict(startup),
            "feedback_action_to_next_paint_proxy": asdict(feedback),
            "warm_refreshes": warm_refreshes,
            "diff_action_to_next_paint_proxy": asdict(diff),
            "observed": {
                "history_rows": len(app.snapshot_data.history),
                "refs": len(app.snapshot_data.refs),
                "changed_files": len(app.snapshot_data.files),
                "diff_path": app._patch_document.path,
                "diff_canonical_source_bytes": presented_bytes,
                "diff_lines": len(app._patch_document.lines),
                "headless_size": [170, 48],
            },
            "rss_bytes": {
                "after_startup": rss_after_startup,
                "after_repeated_refreshes": rss_after_refreshes,
                "after_diff": rss_after_diff,
                "peak": rss_bytes(),
            },
        }


def worker_app(repo_path: Path, warm_runs: int) -> dict[str, Any]:
    return asyncio.run(app_measurement(repo_path, warm_runs))


def percentile_summary(values: list[float]) -> dict[str, float | int]:
    ordered = sorted(values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return {
        "samples": len(ordered),
        "min": ordered[0],
        "median": statistics.median(ordered),
        "p95_nearest_rank": ordered[rank - 1],
        "max": ordered[-1],
    }


def child_sample(
    script: Path,
    worker: str,
    repo: Path,
    env: dict[str, str],
    warm_runs: int,
    diff_max_bytes: int,
) -> dict[str, Any]:
    child_env = dict(env)
    child_env["RHIZOME_BENCH_LAUNCH_NS"] = str(time.monotonic_ns())
    child_env["RHIZOME_BENCH_DIFF_MAX_BYTES"] = str(diff_max_bytes)
    command = [sys.executable, os.fspath(script), "--worker", worker, "--repo", os.fspath(repo)]
    if worker == "app":
        command.extend(["--warm-runs", str(warm_runs)])
    result = run(command, env=child_env)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(
            f"invalid {worker} worker output: {result.stdout.decode(errors='replace')}\n"
            f"stderr: {result.stderr.decode(errors='replace')}"
        ) from error


def command_text(command: list[str]) -> str:
    return subprocess.list2cmdline(command)


def host_details(env: dict[str, str], repo_root: Path) -> dict[str, Any]:
    def output(command: list[str]) -> str:
        try:
            return run(command, env=env).stdout.decode(errors="replace").strip()
        except (OSError, subprocess.CalledProcessError):
            return "unavailable"

    cpu = platform.processor() or platform.machine()
    model = "unavailable"
    memory = "unavailable"
    filesystem: dict[str, str] = {"type": "unavailable"}
    if sys.platform == "darwin":
        model = output(["sysctl", "-n", "machdep.cpu.brand_string"])
        memory = output(["sysctl", "-n", "hw.memsize"])
        disk = output(["diskutil", "info", "/"])
        allowed = {
            "File System Personality": "type",
            "Solid State": "solid_state",
            "Protocol": "protocol",
            "Device Location": "device_location",
            "Media Type": "media_type",
        }
        for line in disk.splitlines():
            if ":" not in line:
                continue
            key, value = (part.strip() for part in line.split(":", 1))
            if key in allowed:
                filesystem[allowed[key]] = value
    elif sys.platform.startswith("linux"):
        model = output(["sh", "-c", "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2-"])
        memory = output(["sh", "-c", "awk '/MemTotal/ {print $2 * 1024}' /proc/meminfo"])
        filesystem = {
            "type_and_source": output(
                ["findmnt", "-no", "FSTYPE,SOURCE", "-T", os.fspath(repo_root)]
            )
        }
    storage = shutil.disk_usage(repo_root)
    return {
        "os": platform.platform(),
        "system": platform.system(),
        "release": platform.release(),
        "architecture": platform.machine(),
        "cpu": cpu,
        "cpu_model": model,
        "logical_cpus": os.cpu_count(),
        "memory_bytes": int(memory) if memory.isdigit() else memory,
        "filesystem": filesystem,
        "storage_bytes": {
            "total": storage.total,
            "used": storage.used,
            "free_at_measurement": storage.free,
        },
        "git": output(["git", "--version"]),
        "python": platform.python_version(),
        "python_executable": Path(sys.executable).name,
        "textual": version("textual"),
        "terminal": {
            "measurement": "Textual headless driver",
            "dimensions": [170, 48],
            "invoking_TERM": os.environ.get("TERM", "unset"),
            "invoking_COLORTERM": os.environ.get("COLORTERM", "unset"),
            "invoking_TMUX": bool(os.environ.get("TMUX")),
        },
    }


def repository_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    fields = {
        "process_launch_to_snapshot_seconds": [
            item["launch_to_snapshot_seconds"] for item in samples
        ],
        "repository_constructor_wall_seconds": [
            item["constructor"]["wall_seconds"] for item in samples
        ],
        "snapshot_wall_seconds": [item["snapshot"]["wall_seconds"] for item in samples],
        "snapshot_cpu_seconds": [item["snapshot"]["cpu_seconds"] for item in samples],
        "diff_wall_seconds": [item["diff"]["wall_seconds"] for item in samples],
        "diff_cpu_seconds": [item["diff"]["cpu_seconds"] for item in samples],
        "peak_rss_bytes": [item["peak_rss_bytes"] for item in samples],
    }
    return {name: percentile_summary(values) for name, values in fields.items()}


def app_metrics(samples: list[dict[str, Any]]) -> dict[str, Any]:
    warm = [
        refresh[field]
        for item in samples
        for refresh in item["warm_refreshes"]
        for field in ("wall_seconds",)
    ]
    fields = {
        "process_launch_to_usable_seconds": [item["launch_to_usable_seconds"] for item in samples],
        "startup_internal_wall_seconds": [item["startup"]["wall_seconds"] for item in samples],
        "startup_internal_cpu_seconds": [item["startup"]["cpu_seconds"] for item in samples],
        "feedback_proxy_wall_seconds": [
            item["feedback_action_to_next_paint_proxy"]["wall_seconds"] for item in samples
        ],
        "diff_proxy_wall_seconds": [
            item["diff_action_to_next_paint_proxy"]["wall_seconds"] for item in samples
        ],
        "diff_proxy_cpu_seconds": [
            item["diff_action_to_next_paint_proxy"]["cpu_seconds"] for item in samples
        ],
        "warm_refresh_wall_seconds": warm,
        "peak_rss_bytes": [item["rss_bytes"]["peak"] for item in samples],
    }
    result = {name: percentile_summary(values) for name, values in fields.items()}
    growth = []
    for item in samples:
        before = item["rss_bytes"]["after_startup"]
        after = item["rss_bytes"]["after_repeated_refreshes"]
        if before is not None and after is not None:
            growth.append(after - before)
    if growth:
        result["rss_change_after_repeated_refreshes_bytes"] = percentile_summary(growth)
    return result


def assess_targets(metrics: dict[str, Any], *, smoke: bool) -> dict[str, Any]:
    app = metrics["app"]
    if smoke:
        return {
            "acceptance_assessment": "not applicable: smoke profile is below the approved envelope",
            "targets_seconds": TARGETS_SECONDS,
        }
    process_warm = app["process_launch_to_usable_seconds"]
    same_process_warm = app["warm_refresh_wall_seconds"]
    diff = app["diff_proxy_wall_seconds"]
    feedback = app["feedback_proxy_wall_seconds"]
    return {
        "targets_seconds": TARGETS_SECONDS,
        "evaluation_rule": (
            "feedback uses nearest-rank p95; non-p95 warm workspace and diff targets require "
            "every measured sample (max) to be at or below the threshold"
        ),
        "workspace_os_cache_cold": {
            "met": None,
            "reason": "not established: the benchmark does not drop or control operating-system caches",
        },
        "workspace_warm_definition": (
            "fresh app process after fixture construction/verification, with uncontrolled but warmed "
            "OS caches; same-process manual refresh is reported as a second diagnostic"
        ),
        "workspace_process_cold_cache_warm": {
            "met": process_warm["max"] <= TARGETS_SECONDS["workspace_warm_max"],
            "observed_max_seconds": process_warm["max"],
        },
        "workspace_same_process_warm_refresh": {
            "met": same_process_warm["max"] <= TARGETS_SECONDS["workspace_warm_max"],
            "observed_max_seconds": same_process_warm["max"],
        },
        "ordinary_diff_headless_proxy": {
            "met": diff["max"] <= TARGETS_SECONDS["diff_presentation_max"],
            "observed_max_seconds": diff["max"],
        },
        "feedback_headless_proxy": {
            "met": feedback["p95_nearest_rank"] <= TARGETS_SECONDS["app_feedback_p95"],
            "observed_p95_seconds": feedback["p95_nearest_rank"],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="use a smaller non-acceptance profile")
    parser.add_argument("--samples", type=int, default=5, help="fresh-process samples (default: 5)")
    parser.add_argument(
        "--warm-runs",
        type=int,
        default=1,
        help="same-process refreshes per app sample (default: 1)",
    )
    parser.add_argument("--output", type=Path, help="write the JSON report to this path")
    parser.add_argument("--worker", choices=("repository", "app"), help=argparse.SUPPRESS)
    parser.add_argument("--repo", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.worker:
        if args.repo is None:
            parser.error("--worker requires --repo")
        result = (
            worker_repository(args.repo)
            if args.worker == "repository"
            else worker_app(args.repo, args.warm_runs)
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    if args.samples < 2:
        parser.error("--samples must be at least 2")
    if args.warm_runs < 1:
        parser.error("--warm-runs must be at least 1")

    dimensions = dict(SMOKE if args.smoke else REFERENCE)
    script = Path(__file__).resolve()
    project = script.parents[1]
    command = [Path(sys.executable).name, os.fspath(Path("scripts") / script.name)]
    if args.smoke:
        command.append("--smoke")
    command.extend(["--samples", str(args.samples), "--warm-runs", str(args.warm_runs)])
    if args.output:
        command.extend(["--output", os.fspath(args.output)])

    with tempfile.TemporaryDirectory(prefix="rhizome-m1-benchmark-") as temporary:
        # macOS exposes /var as a symlink; resolve it so the application's
        # deliberate no-symlink state policy accepts this disposable root.
        root = Path(temporary).resolve()
        environment = isolated_environment(root)
        # The app defaults are copied and made explicitly offline before timing.
        from rhizome.persistence import DEFAULT_SETTINGS, WorkspaceStateStore

        settings = dict(DEFAULT_SETTINGS)
        settings.update({"offline": True, "remote_refresh_enabled": False})
        WorkspaceStateStore(root / "state" / "rhizome").save("global", settings)
        fixture = root / "fixture"
        construction = create_fixture(fixture, dimensions, environment)
        verified = verify_fixture(fixture, dimensions, environment)

        repository_samples = []
        app_samples = []
        for number in range(args.samples):
            print(f"repository sample {number + 1}/{args.samples}", file=sys.stderr, flush=True)
            repository_samples.append(
                child_sample(
                    script,
                    "repository",
                    fixture,
                    environment,
                    args.warm_runs,
                    dimensions["ordinary_diff_max_bytes"],
                )
            )
        for number in range(args.samples):
            print(f"app sample {number + 1}/{args.samples}", file=sys.stderr, flush=True)
            app_samples.append(
                child_sample(
                    script,
                    "app",
                    fixture,
                    environment,
                    args.warm_runs,
                    dimensions["ordinary_diff_max_bytes"],
                )
            )

        metrics = {
            "repository": repository_metrics(repository_samples),
            "app": app_metrics(app_samples),
        }
        revision = output_or_unknown(
            ["git", "-C", os.fspath(project), "rev-parse", "HEAD"], environment
        )
        report = {
            "schema": 1,
            "brief": "V04 M1 performance and resource baseline",
            "profile": "smoke (not acceptance)" if args.smoke else "approved-reference-envelope",
            "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "candidate_revision": revision,
            "command": command_text(command),
            "isolation": {
                "fixture": "temporary directory removed at exit",
                "operator_checkout_mutated": False,
                "network_remotes": 0,
                "home_git_config_app_state_credentials": "temporary/minimal environment",
                "credential_environment_removed": True,
            },
            "environment": host_details(environment, fixture),
            "dimensions_requested": dimensions,
            "dimensions_verified": verified,
            "construction": construction,
            "sampling": {
                "fresh_process_samples": args.samples,
                "same_process_warm_refreshes_per_app_sample": args.warm_runs,
                "process_cold_definition": "new Python process for every repository/app sample",
                "warm_definition": (
                    "samples after fixture verification with operating-system caches uncontrolled; "
                    "same-process refreshes are reported separately"
                ),
                "cold_definition": (
                    "OS cache-cold was not measured: no cache drop was attempted or claimed"
                ),
                "app_timing": (
                    "actual RhizomeApp under Textual's headless driver; action-to-next-paint values "
                    "are proxies, not real-terminal latency"
                ),
                "pilot_sleep_overhead": (
                    "no deliberate sleeps; idle waits yield with asyncio.sleep(0), and paint "
                    "completion uses Pilot.pause(0)"
                ),
                "p95": "nearest-rank p95",
            },
            "metrics": metrics,
            "targets": assess_targets(metrics, smoke=args.smoke),
            "resource_units_and_limits": {
                "cpu": "process CPU seconds (user+system via time.process_time); not host-wide utilization",
                "memory": (
                    "bytes; peak RSS from getrusage normalized to bytes, current RSS from ps where available"
                ),
                "limitations": (
                    "headless rendering only; no OS cache control, energy/storage-I/O counters, real-terminal "
                    "paint timing, long soak, or concurrent system-load normalization"
                ),
            },
            "raw_samples": {"repository": repository_samples, "app": app_samples},
        }

    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


def output_or_unknown(command: list[str], env: dict[str, str]) -> str:
    try:
        return run(command, env=env).stdout.decode().strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
