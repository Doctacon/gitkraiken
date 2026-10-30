#!/usr/bin/env python3
"""Reproducible, local-only V04/V08 M2 scale benchmark.

The fixture is constructed in the parent and every measured workload runs in a
fresh child process, so fixture construction cannot inflate measured peak RSS.
``--smoke`` is a correctness check only; it is not reference performance evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from benchmark_m1 import (
    create_fixture,
    current_rss_bytes,
    host_details,
    isolated_environment,
    rss_bytes,
    verify_fixture,
)

REFERENCE = {
    "commits": 100_000,
    "tracked_files": 20_000,
    "refs": 1_000,
    "changed_files": 200,
    "ordinary_diff_max_bytes": 200 * 1024,
}
SMOKE = {
    # More than two 300-row history pages and one 1,000-row search batch.
    "commits": 1_200,
    "tracked_files": 100,
    "refs": 20,
    "changed_files": 10,
    "ordinary_diff_max_bytes": 64 * 1024,
}
PAGE_SIZE = 300
SCAN_LIMIT = 1_000
WORKER_TIMEOUT_SECONDS = 360
HEADLESS_SIZE = (170, 48)


def elapsed(call: Callable[[], Any]) -> tuple[Any, dict[str, float]]:
    wall = time.perf_counter()
    cpu = time.process_time()
    value = call()
    return value, {
        "wall_seconds": time.perf_counter() - wall,
        "cpu_seconds": time.process_time() - cpu,
    }


def _require_disposable_repo(repo: Path) -> Path:
    """Refuse a worker path outside the parent's declared temporary root."""
    declared = os.environ.get("RHIZOME_M2_DISPOSABLE_ROOT")
    if not declared:
        raise RuntimeError("worker has no declared disposable root")
    root = Path(declared).resolve(strict=True)
    resolved = repo.resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved == root or not (resolved / ".git").is_dir():
        raise RuntimeError("worker repository is not the declared disposable fixture")
    return resolved


def backend_measurement(repo_path: Path, actual_commits: int) -> dict[str, Any]:
    """Measure service entry points, retaining completed stages if a later stage fails."""
    from rhizome.git import Repository
    from rhizome.investigation import InvestigationService
    from rhizome.models import SearchQuery

    result: dict[str, Any] = {
        "status": "partial",
        "completed_stages": [],
        "measurements": {},
    }
    measurements = result["measurements"]
    try:
        repository, constructor = elapsed(lambda: Repository(repo_path))
        measurements["repository_constructor"] = constructor
        result["completed_stages"].append("repository_constructor")

        service = InvestigationService(repository)
        scope, capture = elapsed(service.capture)
        measurements["capture_unified_scope"] = {
            **capture,
            "captured_refs": len(scope.captured_refs),
            "distinct_root_oids": len(scope.root_oids),
        }
        result["completed_stages"].append("capture_unified_scope")

        topology_first, timing = elapsed(
            lambda: service.history(scope, order="topology", limit=PAGE_SIZE)
        )
        if len(topology_first.commits) != min(PAGE_SIZE, actual_commits):
            raise RuntimeError("topology first page returned an unexpected row count")
        measurements["topology_first_page"] = {
            **timing,
            "rows": len(topology_first.commits),
            "has_more": topology_first.has_more,
        }
        result["completed_stages"].append("topology_first_page")

        if topology_first.next_cursor is None:
            raise RuntimeError("fixture is too small for the required topology next page")
        topology_next, timing = elapsed(
            lambda: service.history(
                scope,
                order="topology",
                cursor=topology_first.next_cursor,
                limit=PAGE_SIZE,
            )
        )
        expected_next = min(PAGE_SIZE, max(0, actual_commits - PAGE_SIZE))
        if len(topology_next.commits) != expected_next:
            raise RuntimeError("topology next page returned an unexpected row count")
        measurements["topology_next_page"] = {
            **timing,
            "rows": len(topology_next.commits),
            "omitted_before": topology_next.omitted_before,
            "has_more": topology_next.has_more,
        }
        result["completed_stages"].append("topology_next_page")

        author_first, timing = elapsed(
            lambda: service.history(scope, order="author-time", limit=PAGE_SIZE)
        )
        if len(author_first.commits) != min(PAGE_SIZE, actual_commits):
            raise RuntimeError("author-time first page returned an unexpected row count")
        measurements["author_time_first_page"] = {
            **timing,
            "rows": len(author_first.commits),
            "has_more": author_first.has_more,
        }
        result["completed_stages"].append("author_time_first_page")

        # This is batch-return latency for a query whose first returned hit is in
        # the first batch. The API is not streaming, so it is not keystroke-to-row
        # or real-user/terminal latency.
        known = SearchQuery(text=f"benchmark commit {actual_commits - 1:06d}")
        hit_page, timing = elapsed(
            lambda: service.search(scope, known, order="topology", scan_limit=SCAN_LIMIT)
        )
        if not hit_page.hits:
            raise RuntimeError("known-hit search returned no result")
        measurements["known_hit_first_batch"] = {
            **timing,
            "hits": len(hit_page.hits),
            "examined": hit_page.examined,
            "examined_total": hit_page.examined_total,
            "latency_semantics": (
                "service call until the first result-bearing batch returned; not streaming "
                "first-result paint and not real-user latency"
            ),
        }
        result["completed_stages"].append("known_hit_first_batch")

        query = SearchQuery(text="__rhizome_benchmark_deliberate_no_hit__")
        cursor: str | None = None
        progress = []
        search_wall = time.perf_counter()
        search_cpu = time.process_time()
        while True:
            batch_wall = time.perf_counter()
            page = service.search(
                scope,
                query,
                order="topology",
                cursor=cursor,
                scan_limit=SCAN_LIMIT,
            )
            progress.append(
                {
                    "batch": len(progress) + 1,
                    "wall_seconds": time.perf_counter() - batch_wall,
                    "examined": page.examined,
                    "examined_total": page.examined_total,
                    "complete": page.complete,
                    "hits": len(page.hits),
                }
            )
            if page.hits:
                raise RuntimeError("deliberate no-hit query unexpectedly matched")
            if page.complete:
                if page.examined_total != actual_commits:
                    raise RuntimeError(
                        "complete search examined_total does not equal verified fixture commits: "
                        f"{page.examined_total} != {actual_commits}"
                    )
                break
            if page.next_cursor is None:
                raise RuntimeError("incomplete search did not provide a continuation cursor")
            cursor = page.next_cursor
        measurements["full_scope_no_hit_search"] = {
            "wall_seconds": time.perf_counter() - search_wall,
            "cpu_seconds": time.process_time() - search_cpu,
            "scan_limit": SCAN_LIMIT,
            "batches": len(progress),
            "examined_total": page.examined_total,
            "complete": page.complete,
            "first_result_latency_seconds": None,
            "first_result_reason": "deliberate no-hit query has no result",
            "progress": progress,
        }
        result["completed_stages"].append("full_scope_no_hit_search")
        result["status"] = "complete"
    except Exception as error:  # Preserve honest partial measurements in worker JSON.
        result["error"] = {"type": type(error).__name__, "message": str(error)}
    result["rss_bytes"] = {
        "current_after_measurements": current_rss_bytes(),
        "peak": rss_bytes(),
    }
    return result


async def _wait_for_idle(app: Any) -> None:
    async with asyncio.timeout(180):
        while app.busy or app.workers:
            # Do not busy-spin while measuring process CPU; polling adds at most 10 ms.
            await asyncio.sleep(0.01)


async def app_measurement(repo_path: Path, warm_samples: int) -> dict[str, Any]:
    """Run the complete application under Textual's headless Pilot."""
    from rhizome.app import RhizomeApp
    from rhizome.persistence import DEFAULT_SETTINGS, WorkspaceStateStore

    state_root = Path(os.environ["RHIZOME_STATE_HOME"]).resolve()
    disposable_root = Path(os.environ["RHIZOME_M2_DISPOSABLE_ROOT"]).resolve(strict=True)
    if not state_root.is_relative_to(disposable_root) or state_root == disposable_root:
        raise RuntimeError("App state must remain below the disposable fixture root")
    settings = dict(DEFAULT_SETTINGS)
    settings.update({"offline": True, "remote_refresh_enabled": False})
    WorkspaceStateStore(state_root).save("global", settings)

    startup_wall = time.perf_counter()
    startup_cpu = time.process_time()
    app = RhizomeApp(repo_path)
    async with app.run_test(size=HEADLESS_SIZE) as pilot:
        await _wait_for_idle(app)
        await pilot.pause(0)
        startup = {
            "wall_seconds": time.perf_counter() - startup_wall,
            "cpu_seconds": time.process_time() - startup_cpu,
        }
        if app.snapshot_data is None or app._history_page is None:
            raise RuntimeError("app did not produce a usable history page")
        startup_page_rows = len(app._history_page.commits)
        startup_snapshot_rows = len(app.snapshot_data.history)
        if startup_page_rows > PAGE_SIZE or startup_snapshot_rows > PAGE_SIZE:
            raise RuntimeError("app loaded more than the bounded 300-row history page")
        table = app.query_one("#graph-table")
        startup_widget_rows = table.row_count
        rss_after_startup = current_rss_bytes()
        if not app._settings["offline"] or app._settings["remote_refresh_enabled"]:
            raise RuntimeError("app did not restore the persisted offline-only settings")

        refresh_samples = []
        for _ in range(warm_samples):
            refresh_wall = time.perf_counter()
            refresh_cpu = time.process_time()
            app.request_refresh(quiet=True)
            await _wait_for_idle(app)
            await pilot.pause(0)
            sample = {
                "wall_seconds": time.perf_counter() - refresh_wall,
                "cpu_seconds": time.process_time() - refresh_cpu,
                "rss_bytes_after": current_rss_bytes(),
            }
            if app.snapshot_data is None or app._history_page is None:
                raise RuntimeError("app lost its history after warm refresh")
            if (
                len(app._history_page.commits) > PAGE_SIZE
                or len(app.snapshot_data.history) > PAGE_SIZE
            ):
                raise RuntimeError("warm refresh loaded more than the bounded history page")
            if table.row_count > PAGE_SIZE + 1:
                raise RuntimeError("warm refresh rendered more than one bounded window plus WIP")
            refresh_samples.append(sample)
        times = sorted(sample["wall_seconds"] for sample in refresh_samples)
        distribution = {
            "count": len(times),
            "minimum_seconds": times[0],
            "median_seconds": statistics.median(times),
            "maximum_seconds": times[-1],
            "p95_seconds": times[math.ceil(0.95 * len(times)) - 1] if len(times) >= 20 else None,
            "p95_method": "nearest rank; not reported for fewer than 20 samples",
        }
        return {
            "status": "complete",
            "startup": startup,
            "warm_refresh": refresh_samples[0],
            "warm_refresh_samples": refresh_samples,
            "warm_refresh_distribution": distribution,
            "observed": {
                "history_page_commit_rows_startup": startup_page_rows,
                "snapshot_history_rows_startup": startup_snapshot_rows,
                "history_widget_rows_startup": startup_widget_rows,
                "history_page_commit_rows_after_refresh": len(app._history_page.commits),
                "snapshot_history_rows_after_refresh": len(app.snapshot_data.history),
                "history_widget_rows_after_refresh": table.row_count,
                "history_page_limit": PAGE_SIZE,
                "offline": app._settings["offline"],
                "remote_refresh_enabled": app._settings["remote_refresh_enabled"],
                "headless_size": list(HEADLESS_SIZE),
            },
            "rss_bytes": {
                "after_startup": rss_after_startup,
                "after_warm_refresh": current_rss_bytes(),
                "peak": rss_bytes(),
            },
            "latency_semantics": (
                "full RhizomeApp.run_test with Textual headless Pilot; startup and refresh are "
                "headless harness timings, not real-terminal or real-user latency"
            ),
        }


def worker_app(repo_path: Path, warm_samples: int) -> dict[str, Any]:
    try:
        return asyncio.run(app_measurement(repo_path, warm_samples))
    except Exception as error:
        return {
            "status": "error",
            "error": {"type": type(error).__name__, "message": str(error)},
            "rss_bytes": {"peak": rss_bytes(), "current": current_rss_bytes()},
        }


def run_worker(
    script: Path,
    worker: str,
    repo: Path,
    actual_commits: int,
    environment: dict[str, str],
    disposable_root: Path,
    *,
    warm_samples: int = 1,
) -> dict[str, Any]:
    child_environment = dict(environment)
    child_environment["RHIZOME_M2_DISPOSABLE_ROOT"] = os.fspath(disposable_root)
    command = [
        sys.executable,
        os.fspath(script),
        "--worker",
        worker,
        "--repo",
        os.fspath(repo),
        "--actual-commits",
        str(actual_commits),
        "--warm-samples",
        str(warm_samples),
    ]
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            env=child_environment,
            capture_output=True,
            check=False,
            timeout=WORKER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "timeout",
            "timeout_seconds": WORKER_TIMEOUT_SECONDS,
            "wall_seconds_until_termination": time.perf_counter() - started,
            "error": "worker exceeded the parent-enforced timeout; no pass is claimed",
        }
    if completed.returncode != 0:
        return {
            "status": "error",
            "returncode": completed.returncode,
            "wall_seconds": time.perf_counter() - started,
            "stderr": completed.stderr.decode(errors="replace")[-4000:],
            "error": "worker exited unsuccessfully; no pass is claimed",
        }
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "status": "error",
            "wall_seconds": time.perf_counter() - started,
            "stderr": completed.stderr.decode(errors="replace")[-4000:],
            "error": "worker returned invalid JSON; no pass is claimed",
        }
    result["parent_observed_wall_seconds"] = time.perf_counter() - started
    result["parent_timeout_seconds"] = WORKER_TIMEOUT_SECONDS
    return result


def _write_report(report: dict[str, Any], output: Path) -> None:
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    profile = parser.add_mutually_exclusive_group(required=False)
    profile.add_argument("--smoke", action="store_true", help="small correctness-only profile")
    profile.add_argument("--reference", action="store_true", help="approved V04 reference envelope")
    parser.add_argument("--output", type=Path, help="required JSON report path")
    parser.add_argument(
        "--warm-samples",
        type=int,
        default=1,
        help="1–50 explicit warm App refreshes; 20+ reports nearest-rank p95",
    )
    parser.add_argument("--worker", choices=("backend", "app"), help=argparse.SUPPRESS)
    parser.add_argument("--repo", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--actual-commits", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not 1 <= args.warm_samples <= 50:
        parser.error("--warm-samples must be between 1 and 50")

    if args.worker:
        if args.repo is None or args.actual_commits is None:
            parser.error("worker mode requires --repo and --actual-commits")
        repo = _require_disposable_repo(args.repo)
        result = (
            backend_measurement(repo, args.actual_commits)
            if args.worker == "backend"
            else worker_app(repo, args.warm_samples)
        )
        print(json.dumps(result, sort_keys=True))
        return 0

    if not (args.smoke or args.reference):
        parser.error("choose exactly one of --smoke or --reference")
    if args.output is None:
        parser.error("--output is required")

    dimensions = dict(SMOKE if args.smoke else REFERENCE)
    script = Path(__file__).resolve()
    command = [
        Path(sys.executable).name,
        os.fspath(Path("scripts") / script.name),
        "--smoke" if args.smoke else "--reference",
        "--output",
        os.fspath(args.output),
        "--warm-samples",
        str(args.warm_samples),
    ]
    started_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report: dict[str, Any] = {
        "schema": 2,
        "brief": "V04/V08 M2 graph, search, and application scale benchmark",
        "profile": "smoke (not performance evidence)" if args.smoke else "reference",
        "started_utc": started_utc,
        "command": subprocess.list2cmdline(command),
        "dimensions_requested": dimensions,
        "status": "error",
        "worker_timeout_seconds": WORKER_TIMEOUT_SECONDS,
        "sampling": {
            "backend_and_startup_samples": 1,
            "warm_refresh_samples": args.warm_samples,
            "percentiles": "only explicit warm App refreshes; nearest-rank p95 when sample count >=20",
            "cache_state": "one fresh App process; successive explicit refreshes in that same App. OS/filesystem caches uncontrolled; no cold-cache claim",
        },
        "timing_semantics": {
            "backend": "synchronous service-call wall and process CPU time in a fresh child",
            "cpu_rss": "Python worker only; excludes Git child CPU/RSS; no whole-process-tree resource claim",
            "first_result": (
                "known-hit latency is until a result-bearing 1,000-commit batch returns; the API "
                "does not stream an individual first result"
            ),
            "full_search": "total service time through complete captured scope",
            "application": (
                "full RhizomeApp.run_test under headless Pilot, distinct from real-user and "
                "real-terminal latency"
            ),
        },
    }

    try:
        with tempfile.TemporaryDirectory(prefix="rhizome-m2-benchmark-") as temporary:
            root = Path(temporary).resolve()
            environment = isolated_environment(root)
            fixture = root / "fixture"
            construction = create_fixture(fixture, dimensions, environment)
            verified = verify_fixture(fixture, dimensions, environment)
            report.update(
                {
                    "environment": host_details(environment, fixture),
                    "isolation": {
                        "fixture": "disposable temporary path removed at exit",
                        "operator_checkout_mutated": False,
                        "network_remotes_verified": verified["remotes"],
                        "home_xdg_git_config_app_state": "isolated below disposable root",
                        "credentials_removed_from_worker_environment": True,
                        "git_allowed_protocols": "file only",
                        "app_offline_state_persisted_in_worker_before_launch": True,
                        "fixture_construction_excluded_from_measured_worker_rss": True,
                    },
                    "construction": construction,
                    "dimensions_verified": verified,
                }
            )
            backend = run_worker(script, "backend", fixture, verified["commits"], environment, root)
            report["backend"] = backend
            # Backend evidence is always attempted first. App is a separate bounded
            # child, so a timeout/failure is reported rather than converted to a pass.
            app = run_worker(
                script,
                "app",
                fixture,
                verified["commits"],
                environment,
                root,
                warm_samples=args.warm_samples,
            )
            report["app"] = app
            report["status"] = (
                "complete"
                if backend.get("status") == "complete" and app.get("status") == "complete"
                else "partial_or_error"
            )
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "message": str(error)}

    report["ended_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _write_report(report, args.output)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
