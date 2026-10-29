from __future__ import annotations

import os
import re
import shlex
import shutil
import signal
import subprocess
import time
import tomllib
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "install.sh"
RUNTIME_CONSTRAINTS = SCRIPT.with_name("install-runtime-constraints.txt")
BUILD_CONSTRAINTS = SCRIPT.with_name("install-build-constraints.txt")
LOCK = SCRIPT.parents[1] / "uv.lock"
REVISION_A = "a" * 40
REVISION_B = "b" * 40
UV_DARWIN_ARM64_SHA256 = "66e37d91f839e12481d7b932a1eccbfe732560f42c1cfb89faddfa2454534ba8"


def _executable(path: Path, content: str) -> None:
    path.write_text("#!/bin/sh\nset -eu\n" + content)
    path.chmod(0o755)


@pytest.fixture
def bootstrap_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    home = tmp_path / "home with spaces"
    root = home / "private apps" / "rhizome"
    commands = tmp_path / "commands"
    commands.mkdir()
    home.mkdir()
    archive = tmp_path / "downloaded archive"
    archive.write_text("stub archive; tar and checksum are isolated test doubles")
    log = tmp_path / "bootstrap.log"
    control = tmp_path / "control"
    control.mkdir()
    (control / "uname-s").write_text("Darwin\n")
    (control / "uname-m").write_text("arm64\n")
    (control / "libc").write_text("\n")
    repository = tmp_path / "repository"
    repository.symlink_to(SCRIPT.parents[1], target_is_directory=True)

    _executable(
        commands / "uname",
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'if [ "$1" = "-s" ]; then cat "$test_root/control/uname-s"; '
        'else cat "$test_root/control/uname-m"; fi\n',
    )
    _executable(
        commands / "getconf",
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'if [ "$(cat "$test_root/control/libc")" = gnu ]; then echo "glibc 2.36"; else exit 1; fi\n',
    )
    _executable(
        commands / "ldd",
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'if [ "$(cat "$test_root/control/libc")" = musl ]; then echo "musl libc" >&2; else exit 1; fi\n',
    )
    _executable(
        commands / "curl",
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'printf "DOWNLOAD %s | netrc=%s cert=%s pythonpath=%s pythonhome=%s home=%s data=%s config=%s trace2=%s trace2event=%s curlverbose=%s redact=%s\\n" '
        '"$*" "${NETRC:-unset}" "${SSL_CLIENT_CERT:-unset}" "${PYTHONPATH:-unset}" '
        '"${PYTHONHOME:-unset}" "$HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" '
        '"${GIT_TRACE2:-unset}" "${GIT_TRACE2_EVENT:-unset}" '
        '"${GIT_CURL_VERBOSE:-unset}" "${GIT_TRACE_REDACT:-unset}" >> "$test_root/bootstrap.log"\n'
        "output=\n"
        "url=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "--output" ]; then shift; output=$1\n'
        '  elif [ "${1#https://}" != "$1" ]; then url=$1; fi\n'
        "  shift\n"
        "done\n"
        'case "$url" in\n'
        '  */install-runtime-constraints.txt) cp "$test_root/repository/scripts/install-runtime-constraints.txt" "$output" ;;\n'
        '  */install-build-constraints.txt) cp "$test_root/repository/scripts/install-build-constraints.txt" "$output" ;;\n'
        '  *) cp "$test_root/downloaded archive" "$output" ;;\n'
        "esac\n",
    )
    checksum_stub = (
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'printf "CHECKSUM\\n" >> "$test_root/bootstrap.log"\n'
        'if [ -e "$test_root/control/bad-checksum" ]; then '
        "digest=0000000000000000000000000000000000000000000000000000000000000000; "
        f"else digest={UV_DARWIN_ARM64_SHA256}; fi\n"
        'printf "%s  archive\\n" "$digest"\n'
    )
    _executable(commands / "shasum", checksum_stub)
    _executable(commands / "sha256sum", checksum_stub)
    _executable(
        commands / "tar",
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'printf "EXTRACT\\n" >> "$test_root/bootstrap.log"\n'
        "destination=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-C" ]; then shift; destination=$1; fi\n'
        "  shift\n"
        "done\n"
        'mkdir -p "$destination/uv-aarch64-apple-darwin"\n'
        'printf "#!/bin/sh\\nset -eu\\nTEST_ROOT=\'%s\'\\n" "$test_root" > "$destination/uv-aarch64-apple-darwin/uv"\n'
        "cat >> \"$destination/uv-aarch64-apple-darwin/uv\" <<'UV'\n"
        'printf "UV %s | %s | %s | %s | %s | %s | proxy=%s ca=%s prompt=%s home=%s gitdir=%s header=%s global=%s netrc=%s clientcert=%s pythonpath=%s pythonhome=%s data=%s config=%s template=%s tls=%s trace2=%s trace2event=%s curlverbose=%s redact=%s\\n" '
        '"$*" "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR" "$UV_PYTHON_INSTALL_DIR" '
        '"$UV_PYTHON_BIN_DIR" "$UV_CACHE_DIR" "${HTTPS_PROXY:-}" "${SSL_CERT_FILE:-}" '
        '"${GIT_TERMINAL_PROMPT:-}" "$HOME" "${GIT_DIR:-unset}" '
        '"${GIT_HTTP_EXTRA_HEADER:-unset}" "${GIT_CONFIG_GLOBAL:-unset}" '
        '"${NETRC:-unset}" "${SSL_CLIENT_CERT:-unset}" "${PYTHONPATH:-unset}" '
        '"${PYTHONHOME:-unset}" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" '
        '"${GIT_TEMPLATE_DIR:-unset}" "${GIT_SSL_NO_VERIFY:-unset}" '
        '"${GIT_TRACE2:-unset}" "${GIT_TRACE2_EVENT:-unset}" '
        '"${GIT_CURL_VERBOSE:-unset}" "${GIT_TRACE_REDACT:-unset}" >> "$TEST_ROOT/bootstrap.log"\n'
        'if [ "${1:-}" = "--version" ]; then echo "uv 0.11.7"; exit 0; fi\n'
        'case " $* " in\n'
        '  *" python install "*)\n'
        '    [ ! -e "$TEST_ROOT/control/fail-python" ] || exit 71\n'
        '    mkdir -p "$UV_PYTHON_INSTALL_DIR" "$UV_PYTHON_BIN_DIR" "$UV_CACHE_DIR"\n'
        "    ;;\n"
        '  *" tool install "*)\n'
        '    if [ -e "$TEST_ROOT/control/intermediate-launcher" ]; then\n'
        '      printf "%s\\n" "$$" > "$TEST_ROOT/control/intermediate-launcher-pid"\n'
        '      sh "$TEST_ROOT/control/delayed-foreground-child" '
        '"$UV_TOOL_DIR/intermediate-work" </dev/null >/dev/null 2>&1\n'
        "    fi\n"
        '    if [ -e "$TEST_ROOT/control/signal-wrapper" ]; then\n'
        '      kill -TERM "$PPID"\n'
        '      printf "%s\\n" "$$" > "$TEST_ROOT/control/signaled-child-pid"\n'
        '      : > "$TEST_ROOT/control/wrapper-signaled"\n'
        '      while [ -e "$TEST_ROOT/control/signal-wrapper" ]; do sleep 0.05; done\n'
        "    fi\n"
        '    if [ -e "$TEST_ROOT/control/hold" ]; then\n'
        '      : > "$TEST_ROOT/control/ready"\n'
        '      while [ -e "$TEST_ROOT/control/hold" ]; do sleep 0.05; done\n'
        "    fi\n"
        '    if [ -e "$TEST_ROOT/control/foreground-hold" ]; then\n'
        '      sh "$TEST_ROOT/control/foreground-child" "$UV_TOOL_DIR/foreground-work"\n'
        "    fi\n"
        '    [ ! -e "$TEST_ROOT/control/fail-tool" ] || exit 73\n'
        '    mkdir -p "$UV_TOOL_DIR" "$UV_TOOL_BIN_DIR"\n'
        "    cat > \"$UV_TOOL_BIN_DIR/rhizome\" <<'APP'\n"
        "#!/bin/sh\n"
        "set -eu\n"
        "if env | grep '^UV_' >/dev/null; then\n"
        '  echo "installer UV variables leaked" >&2\n'
        "  exit 90\n"
        "fi\n"
        'echo "fake rhizome"\n'
        "APP\n"
        '    chmod 700 "$UV_TOOL_BIN_DIR/rhizome"\n'
        "    ;;\n"
        "esac\n"
        "UV\n"
        'chmod 700 "$destination/uv-aarch64-apple-darwin/uv"\n',
    )
    _executable(
        control / "foreground-child",
        "work=$1\n"
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'mkdir -p "$work"\n'
        'printf "%s\\n" "$$" > "$test_root/control/child-pid"\n'
        ': > "$test_root/control/child-ready"\n'
        'while [ -e "$test_root/control/foreground-hold" ]; do\n'
        '  printf "child owns this work\\n" > "$work/sentinel"\n'
        "  sleep 0.05\n"
        "done\n",
    )
    _executable(
        control / "delayed-foreground-child",
        "work=$1\n"
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'printf "%s\\n" "$$" > "$test_root/control/delayed-child-pid"\n'
        ': > "$test_root/control/delayed-child-ready"\n'
        'while [ -e "$test_root/control/delayed-child-hold" ]; do sleep 0.05; done\n'
        'mkdir -p "$work"\n'
        'printf "delayed child owns this work\\n" > "$work/sentinel"\n'
        ': > "$test_root/control/delayed-child-finished"\n',
    )

    state = home / "state" / "rhizome"
    state.mkdir(parents=True)
    (state / "draft.json").write_text("preserve me")
    unrelated = home / "unrelated-tool"
    unrelated.write_text("do not touch")
    environment = {
        **{key: value for key, value in os.environ.items() if not key.startswith("UV_")},
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / "unused-xdg-state"),
        "RHIZOME_STATE_HOME": str(state),
        "RHIZOME_INSTALL_ROOT": str(root),
        "PATH": f"{commands}{os.pathsep}{os.environ['PATH']}",
    }
    return environment, root, log


def _control(environment: dict[str, str]) -> Path:
    return Path(environment["HOME"]).parent / "control"


def _select_wget_boundary(environment: dict[str, str]) -> None:
    source_commands = Path(environment["PATH"].split(os.pathsep)[0])
    test_root = Path(environment["HOME"]).parent
    commands = test_root / "wget-only-commands"
    commands.mkdir()
    for name in ("uname", "getconf", "ldd", "shasum", "sha256sum", "tar"):
        (commands / name).symlink_to(source_commands / name)
    for name in (
        "awk",
        "cat",
        "chmod",
        "cp",
        "dirname",
        "env",
        "grep",
        "ls",
        "mkdir",
        "mktemp",
        "mv",
        "rm",
        "rmdir",
        "sed",
        "sh",
        "sort",
        "tr",
    ):
        executable = shutil.which(name)
        assert executable is not None
        (commands / name).symlink_to(executable)
    _executable(
        commands / "wget",
        'test_root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)\n'
        'printf "WGET %s | netrc=%s cert=%s pythonpath=%s pythonhome=%s home=%s data=%s config=%s trace2=%s trace2event=%s curlverbose=%s redact=%s\\n" '
        '"$*" "${NETRC:-unset}" "${SSL_CLIENT_CERT:-unset}" "${PYTHONPATH:-unset}" '
        '"${PYTHONHOME:-unset}" "$HOME" "$XDG_DATA_HOME" "$XDG_CONFIG_HOME" '
        '"${GIT_TRACE2:-unset}" "${GIT_TRACE2_EVENT:-unset}" '
        '"${GIT_CURL_VERBOSE:-unset}" "${GIT_TRACE_REDACT:-unset}" >> "$test_root/bootstrap.log"\n'
        "output=\n"
        "url=\n"
        'while [ "$#" -gt 0 ]; do\n'
        '  if [ "$1" = "-O" ]; then shift; output=$1\n'
        '  elif [ "${1#https://}" != "$1" ]; then url=$1; fi\n'
        "  shift\n"
        "done\n"
        'case "$url" in\n'
        '  */install-runtime-constraints.txt) cp "$test_root/repository/scripts/install-runtime-constraints.txt" "$output" ;;\n'
        '  */install-build-constraints.txt) cp "$test_root/repository/scripts/install-build-constraints.txt" "$output" ;;\n'
        '  *) cp "$test_root/downloaded archive" "$output" ;;\n'
        "esac\n",
    )
    environment["PATH"] = str(commands)


def _inject_killing_mv(environment: dict[str, str], match: Path) -> None:
    commands = Path(environment["PATH"].split(os.pathsep)[0])
    _executable(
        commands / "mv",
        'if [ "${FAKE_MV_KILL:-0}" = 1 ] && [ "$1" = "$FAKE_MV_MATCH" ]; then\n'
        '  kill -KILL "$PPID"\n'
        "  exit 99\n"
        "fi\n"
        'exec /bin/mv "$@"\n',
    )
    environment["FAKE_MV_MATCH"] = str(match)
    environment["FAKE_MV_KILL"] = "1"


def _inject_blocking_partial_rm(environment: dict[str, str], match: Path) -> None:
    commands = Path(environment["PATH"].split(os.pathsep)[0])
    test_root = Path(environment["HOME"]).parent
    _executable(
        commands / "rm",
        f"match={shlex.quote(str(match))}\n"
        f"test_root={shlex.quote(str(test_root))}\n"
        'if [ "${1:-}" = -rf ] && [ "${2:-}" = "$match" ] && '
        '[ -e "$test_root/control/uninstall-hold" ]; then\n'
        '  /bin/rm -rf "$match/bin"\n'
        '  : > "$test_root/control/uninstall-ready"\n'
        '  while [ -e "$test_root/control/uninstall-hold" ]; do sleep 0.05; done\n'
        "  exit 74\n"
        "fi\n"
        'exec /bin/rm "$@"\n',
    )


def _run(environment: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), *arguments],
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _interrupt_held_install(
    environment: dict[str, str], revision: str, signal_number: int
) -> tuple[int, str, str]:
    control = _control(environment)
    hold = control / "hold"
    ready = control / "ready"
    hold.touch()
    process = subprocess.Popen(
        ["sh", str(SCRIPT), "--revision", revision],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), process.communicate(timeout=1)
    process.send_signal(signal_number)
    hold.unlink()
    stdout, stderr = process.communicate(timeout=10)
    return process.returncode, stdout, stderr


def _constraint_entries(path: Path) -> dict[str, tuple[str, str]]:
    entries = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        name, artifact = line.split(" @ ", maxsplit=1)
        url, digest = artifact.rsplit("#sha256=", maxsplit=1)
        assert url.startswith("https://files.pythonhosted.org/")
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        entries[name] = (url, digest)
    return entries


def test_constraint_manifests_pin_lock_artifacts_and_complete_build_closure():
    runtime = _constraint_entries(RUNTIME_CONSTRAINTS)
    lock = tomllib.loads(LOCK.read_text())
    locked_packages = {package["name"]: package for package in lock["package"]}
    expected_runtime = {
        "linkify-it-py",
        "markdown-it-py",
        "mdit-py-plugins",
        "mdurl",
        "platformdirs",
        "pygments",
        "rich",
        "textual",
        "typing-extensions",
    }
    assert runtime.keys() == expected_runtime
    for name, (url, digest) in runtime.items():
        wheels = locked_packages[name]["wheels"]
        assert {wheel["url"] for wheel in wheels} == {url}
        assert {wheel["hash"] for wheel in wheels} == {f"sha256:{digest}"}

    build = _constraint_entries(BUILD_CONSTRAINTS)
    assert build.keys() == {
        "hatchling",
        "packaging",
        "pathspec",
        "pluggy",
        "tomlkit",
        "trove-classifiers",
    }
    expected_build_wheels = {
        "hatchling": "hatchling-1.33.0-py3-none-any.whl",
        "packaging": "packaging-26.3-py3-none-any.whl",
        "pathspec": "pathspec-1.1.1-py3-none-any.whl",
        "pluggy": "pluggy-1.7.0-py3-none-any.whl",
        "tomlkit": "tomlkit-0.15.2-py3-none-any.whl",
        "trove-classifiers": "trove_classifiers-2026.9.21.13-py3-none-any.whl",
    }
    for name, filename in expected_build_wheels.items():
        assert filename in build[name][0]


def test_revision_is_required_and_must_be_a_full_lowercase_commit(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    for arguments in ((), ("--revision", "main"), ("--revision", "A" * 40)):
        result = _run(environment, *arguments)
        assert result.returncode == 2
        assert "40-character lowercase hexadecimal commit" in result.stderr
    assert not root.exists()


def test_corrupt_uv_archive_is_never_extracted_or_run(bootstrap_environment):
    environment, root, log = bootstrap_environment
    (_control(environment) / "bad-checksum").touch()

    result = _run(environment, "--revision", REVISION_A)

    assert result.returncode != 0
    assert "checksum" in result.stderr.lower()
    assert log.read_text().splitlines()[-1] == "CHECKSUM"
    assert "EXTRACT" not in log.read_text()
    assert "UV " not in log.read_text()
    assert not root.exists()


def test_install_update_rollback_and_owned_uninstall_are_isolated(bootstrap_environment):
    environment, root, log = bootstrap_environment

    first = _run(environment, "--revision", REVISION_A)
    assert first.returncode == 0, first.stderr
    assert "Planned persistent effects" in first.stdout
    assert "No shell profile, PATH, Git, tmux, or system installation is modified" in first.stdout
    assert str(root / "bin" / "rhizome") in first.stdout
    assert "Direct uv alternative" in first.stdout
    assert "Private uv wrapper" not in first.stdout
    assert not (root / "bin" / "uv").exists()
    assert "3.13.13" in first.stdout
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    assert not (root / "previous").exists()

    uv_lines = [line for line in log.read_text().splitlines() if line.startswith("UV ")]
    assert "python install --managed-python --no-config --no-bin 3.13.13" in uv_lines[1]
    assert "tool install --python 3.13.13 --managed-python" in uv_lines[2]
    assert "--default-index https://pypi.org/simple --index-strategy first-index" in uv_lines[2]
    assert "--keyring-provider disabled" in uv_lines[2]
    assert "runtime-constraints.txt" in uv_lines[2]
    assert "build-constraints.txt" in uv_lines[2]
    assert f"git+https://github.com/Doctacon/rhizome.git@{REVISION_A}" in uv_lines[2]
    assert "prompt=0" in uv_lines[2]
    for directory in ("tools", "bin", "python", "python-bin", "cache"):
        final_path = root / "store" / REVISION_A / directory
        assert str(final_path) in uv_lines[1] + uv_lines[2]
        assert ".staging-" not in uv_lines[1] + uv_lines[2]

    fail_tool = _control(environment) / "fail-tool"
    fail_tool.touch()
    interrupted = _run(environment, "--revision", REVISION_B)
    assert interrupted.returncode != 0
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    assert not (root / "revisions" / REVISION_B).exists()
    assert not (root / "store" / REVISION_B).exists()

    fail_tool.unlink()
    second = _run(environment, "--revision", REVISION_B)
    assert second.returncode == 0, second.stderr
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_B}"
    assert (root / "previous").read_text().strip() == f"revisions/{REVISION_A}"
    assert (root / "store" / REVISION_A / "bin" / "rhizome").is_file()

    rollback = _run(environment, "--rollback")
    assert rollback.returncode == 0, rollback.stderr
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    assert (root / "previous").read_text().strip() == f"revisions/{REVISION_B}"
    assert "does not restart a running Rhizome process" in rollback.stdout

    launch = subprocess.run(
        [str(root / "bin" / "rhizome")],
        env=environment,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert launch.returncode == 0
    assert launch.stdout == "fake rhizome\n"

    uninstall = _run(environment, "--uninstall")
    assert uninstall.returncode == 0, uninstall.stderr
    assert not root.exists()
    assert (Path(environment["RHIZOME_STATE_HOME"]) / "draft.json").read_text() == "preserve me"
    assert (Path(environment["HOME"]) / "unrelated-tool").read_text() == "do not touch"
    assert "Drafts and recovery data were preserved" in uninstall.stdout


def test_abrupt_post_staging_interruption_is_recoverable(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    first = _run(environment, "--revision", REVISION_A)
    assert first.returncode == 0, first.stderr

    returncode, _, _ = _interrupt_held_install(environment, REVISION_B, 9)
    assert returncode != 0
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    assert (root / "store" / REVISION_B / ".rhizome-installing").is_file()
    transaction_lock = root.parent / f".rhizome-{root.name}.transaction.lock"
    assert (transaction_lock / "owner").is_file()

    retry = _run(environment, "--revision", REVISION_B)
    assert retry.returncode == 0, retry.stderr
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_B}"
    assert not (root / "store" / REVISION_B / ".rhizome-installing").exists()
    assert not transaction_lock.exists()


def test_marked_payload_staging_move_interruption_is_recoverable(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    payload_staging = root / "store" / f".staging-{REVISION_B}"
    _inject_killing_mv(environment, payload_staging)

    killed = _run(environment, "--revision", REVISION_B)

    assert killed.returncode != 0
    assert (payload_staging / ".rhizome-installing").read_text().splitlines()[0] == REVISION_B
    assert not (root / "store" / REVISION_B).exists()
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"

    environment.pop("FAKE_MV_KILL")
    retry = _run(environment, "--revision", REVISION_B)
    assert retry.returncode == 0, retry.stderr
    assert not payload_staging.exists()
    assert (root / "store" / REVISION_B / ".rhizome-revision").read_text().strip() == REVISION_B


def test_completed_payload_publication_interruption_is_recoverable(bootstrap_environment):
    environment, root, log = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    publication_staging = root / "revisions" / f".staging-{REVISION_B}"
    _inject_killing_mv(environment, publication_staging)

    killed = _run(environment, "--revision", REVISION_B)

    assert killed.returncode != 0
    assert (root / "store" / REVISION_B / ".rhizome-revision").read_text().strip() == REVISION_B
    assert (publication_staging / ".rhizome-revision").read_text().strip() == REVISION_B
    assert not (root / "revisions" / REVISION_B).exists()
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    uv_calls_before_retry = log.read_text().count("UV ")

    environment.pop("FAKE_MV_KILL")
    retry = _run(environment, "--revision", REVISION_B)
    assert retry.returncode == 0, retry.stderr
    assert not publication_staging.exists()
    assert (root / "revisions" / REVISION_B / ".rhizome-revision").read_text().strip() == REVISION_B
    assert log.read_text().count("UV ") == uv_calls_before_retry


def test_dependency_provenance_and_network_environment_are_explicit(bootstrap_environment):
    environment, root, log = bootstrap_environment
    environment["HTTPS_PROXY"] = "http://proxy.example.test:8443"
    environment["SSL_CERT_FILE"] = str(Path(environment["HOME"]) / "corporate-ca.pem")
    environment["GIT_DIR"] = str(Path(environment["HOME"]) / "credential-bearing-repository")
    environment["GIT_HTTP_EXTRA_HEADER"] = "Authorization: secret"
    environment["GIT_CONFIG_GLOBAL"] = str(Path(environment["HOME"]) / "credential-config")
    environment["NETRC"] = "netrc-secret-value"
    environment["SSL_CLIENT_CERT"] = "client-cert-secret-value"
    environment["PYTHONPATH"] = "python-path-secret-value"
    environment["PYTHONHOME"] = "python-home-secret-value"
    environment["GIT_TEMPLATE_DIR"] = "git-template-secret-value"
    environment["GIT_SSL_NO_VERIFY"] = "true"
    environment["GIT_TRACE2"] = str(Path(environment["HOME"]) / "trace-secret")
    environment["GIT_TRACE2_EVENT"] = str(Path(environment["HOME"]) / "event-secret")
    environment["GIT_TRACE2_PERF"] = str(Path(environment["HOME"]) / "perf-secret")
    environment["GIT_CURL_VERBOSE"] = "curl-verbose-secret"
    environment["GIT_TRACE_REDACT"] = "0"
    environment["XDG_DATA_HOME"] = str(Path(environment["HOME"]) / "ambient-data")
    environment["XDG_CONFIG_HOME"] = str(Path(environment["HOME"]) / "ambient-config")

    result = _run(environment, "--revision", REVISION_A)

    assert result.returncode == 0, result.stderr
    downloads = [line for line in log.read_text().splitlines() if line.startswith("DOWNLOAD ")]
    assert len(downloads) == 3
    assert all(" -q " in f" {line} " for line in downloads)
    assert all("--proto =https" in line for line in downloads)
    assert all("--tlsv1.2" in line for line in downloads)
    assert all("netrc=unset" in line and "cert=unset" in line for line in downloads)
    assert all("pythonpath=unset" in line and "pythonhome=unset" in line for line in downloads)
    assert all("ambient-data" not in line and "ambient-config" not in line for line in downloads)
    assert all("trace2=unset" in line and "trace2event=unset" in line for line in downloads)
    assert all("curlverbose=unset" in line and "redact=unset" in line for line in downloads)
    tool_line = [line for line in log.read_text().splitlines() if "tool install" in line][0]
    assert "proxy=http://proxy.example.test:8443" in tool_line
    assert f"ca={environment['SSL_CERT_FILE']}" in tool_line
    assert "gitdir=unset" in tool_line
    assert "header=unset" in tool_line
    assert "global=/dev/null" in tool_line
    assert "netrc=unset" in tool_line
    assert "clientcert=unset" in tool_line
    assert "pythonpath=unset" in tool_line
    assert "pythonhome=unset" in tool_line
    assert "template=unset" in tool_line
    assert "tls=unset" in tool_line
    assert "trace2=unset" in tool_line
    assert "trace2event=unset" in tool_line
    assert "curlverbose=unset" in tool_line
    assert "redact=unset" in tool_line
    assert "ambient-data" not in tool_line
    assert "ambient-config" not in tool_line
    for secret in (
        "netrc-secret-value",
        "client-cert-secret-value",
        "python-path-secret-value",
        "python-home-secret-value",
        "git-template-secret-value",
        "trace-secret",
        "event-secret",
        "perf-secret",
        "curl-verbose-secret",
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr
    for trace_name in ("trace-secret", "event-secret", "perf-secret"):
        assert not (Path(environment["HOME"]) / trace_name).exists()
    runtime = (root / "store" / REVISION_A / "runtime-constraints.txt").read_text()
    build = (root / "store" / REVISION_A / "build-constraints.txt").read_text()
    assert "textual-1.0.0-py3-none-any.whl#sha256=" in runtime
    assert "hatchling-1.33.0-py3-none-any.whl#sha256=" in build
    assert "tomlkit-0.15.2-py3-none-any.whl#sha256=" in build


def test_wget_fallback_disables_netrc_hsts_and_uses_private_config(bootstrap_environment):
    environment, root, log = bootstrap_environment
    environment["NETRC"] = "wget-netrc-secret"
    environment["SSL_CLIENT_CERT"] = "wget-client-cert-secret"
    environment["PYTHONPATH"] = "wget-python-path-secret"
    environment["PYTHONHOME"] = "wget-python-home-secret"
    environment["XDG_DATA_HOME"] = str(Path(environment["HOME"]) / "ambient-wget-data")
    environment["XDG_CONFIG_HOME"] = str(Path(environment["HOME"]) / "ambient-wget-config")
    _select_wget_boundary(environment)

    result = _run(environment, "--revision", REVISION_A)

    assert result.returncode == 0, result.stderr
    downloads = [line for line in log.read_text().splitlines() if line.startswith("WGET ")]
    assert len(downloads) == 3
    assert all("--no-config" in line for line in downloads)
    assert all("--no-netrc" in line for line in downloads)
    assert all("--no-hsts" in line for line in downloads)
    assert all("--https-only" in line for line in downloads)
    assert all("--secure-protocol=TLSv1_2" in line for line in downloads)
    assert all("netrc=unset" in line and "cert=unset" in line for line in downloads)
    assert all("pythonpath=unset" in line and "pythonhome=unset" in line for line in downloads)
    assert all("ambient-wget-data" not in line for line in downloads)
    assert all("ambient-wget-config" not in line for line in downloads)
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    for secret in (
        "wget-netrc-secret",
        "wget-client-cert-secret",
        "wget-python-path-secret",
        "wget-python-home-secret",
    ):
        assert secret not in result.stdout
        assert secret not in result.stderr


def test_ambient_uv_configuration_is_rejected_before_download(bootstrap_environment):
    environment, root, log = bootstrap_environment
    environment["UV_INDEX"] = "https://credentials@example.test/simple"
    environment["UV_INSECURE_HOST"] = "example.test"

    result = _run(environment, "--revision", REVISION_A)

    assert result.returncode != 0
    assert "refusing ambient UV_* configuration" in result.stderr
    assert "UV_INDEX" in result.stderr
    assert "UV_INSECURE_HOST" in result.stderr
    assert not log.exists()
    assert not root.exists()


def test_uninstall_refuses_relative_or_install_nested_state(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    installed = _run(environment, "--revision", REVISION_A)
    assert installed.returncode == 0, installed.stderr

    environment["RHIZOME_STATE_HOME"] = "relative-state"
    relative = _run(environment, "--uninstall")
    assert relative.returncode != 0
    assert "state location must be an absolute path" in relative.stderr
    assert root.is_dir()

    nested_state = root / "nested-state"
    nested_state.mkdir()
    environment["RHIZOME_STATE_HOME"] = str(nested_state)
    nested = _run(environment, "--uninstall")
    assert nested.returncode != 0
    assert "must not contain Rhizome drafts" in nested.stderr
    assert root.is_dir()


def test_uninstall_refuses_unexpected_foreign_top_level_content(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    installed = _run(environment, "--revision", REVISION_A)
    assert installed.returncode == 0, installed.stderr
    foreign = root / "someone-elses-file"
    foreign.write_text("preserve")

    result = _run(environment, "--uninstall")

    assert result.returncode != 0
    assert "unexpected foreign top-level content" in result.stderr
    assert foreign.read_text() == "preserve"
    assert root.is_dir()


def test_uninstall_refuses_foreign_content_in_managed_subtree(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    installed = _run(environment, "--revision", REVISION_A)
    assert installed.returncode == 0, installed.stderr
    foreign = root / "store" / "personal-notes.txt"
    foreign.write_text("not installer-owned")

    result = _run(environment, "--uninstall")

    assert result.returncode != 0
    assert "unexpected foreign content" in result.stderr
    assert foreign.read_text() == "not installer-owned"
    assert root.is_dir()


def test_one_root_lock_serializes_install_rollback_and_uninstall(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    first = _run(environment, "--revision", REVISION_A)
    assert first.returncode == 0, first.stderr

    control = _control(environment)
    block = control / "hold"
    block.touch()
    updating = subprocess.Popen(
        ["sh", str(SCRIPT), "--revision", REVISION_B],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = control / "ready"
    deadline = time.monotonic() + 10
    while not ready.exists() and updating.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), updating.communicate(timeout=1)

    for arguments in (("--revision", REVISION_B), ("--rollback",), ("--uninstall",)):
        refused = _run(environment, *arguments)
        assert refused.returncode != 0
        assert "transaction is active" in refused.stderr
        assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"

    block.unlink()
    stdout, stderr = updating.communicate(timeout=10)
    assert updating.returncode == 0, (stdout, stderr)
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_B}"


def test_surviving_foreground_descendant_fences_stale_lock_recovery(
    bootstrap_environment,
):
    environment, root, _ = bootstrap_environment
    control = _control(environment)
    hold = control / "foreground-hold"
    hold.touch()
    installing = subprocess.Popen(
        ["sh", str(SCRIPT), "--revision", REVISION_A],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = control / "child-ready"
    deadline = time.monotonic() + 10
    while not ready.exists() and installing.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists()
    child_pid = int((control / "child-pid").read_text())
    work = root / "store" / REVISION_A / "tools" / "foreground-work" / "sentinel"
    assert work.read_text() == "child owns this work\n"

    installing.kill()
    installing.wait(timeout=5)
    refused = _run(environment, "--revision", REVISION_A)

    assert refused.returncode != 0
    assert "subprocess activity" in refused.stderr
    os.kill(child_pid, 0)
    assert work.read_text() == "child owns this work\n"
    hold.unlink()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        pytest.fail("foreground child did not exit naturally after its hold was released")
    installing.communicate(timeout=5)


def test_killed_intermediate_launcher_retains_activity_until_child_exits(
    bootstrap_environment,
):
    environment, root, log = bootstrap_environment
    first = _run(environment, "--revision", REVISION_A)
    assert first.returncode == 0, first.stderr
    second = _run(environment, "--revision", REVISION_B)
    assert second.returncode == 0, second.stderr
    current_before = (root / "current").read_text()
    previous_before = (root / "previous").read_text()
    assert current_before.strip() == f"revisions/{REVISION_B}"
    assert previous_before.strip() == f"revisions/{REVISION_A}"

    revision = "c" * 40
    control = _control(environment)
    trigger = control / "intermediate-launcher"
    hold = control / "delayed-child-hold"
    trigger.touch()
    hold.touch()
    installing = None
    child_pid = None
    try:
        installing = subprocess.Popen(
            ["sh", str(SCRIPT), "--revision", revision],
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        ready = control / "delayed-child-ready"
        deadline = time.monotonic() + 10
        while not ready.exists() and installing.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), installing.communicate(timeout=1)

        child_pid = int((control / "delayed-child-pid").read_text())
        launcher_pid = int((control / "intermediate-launcher-pid").read_text())
        stage = root / "store" / revision
        marker = stage / ".rhizome-installing"
        marker_before = marker.read_text()
        stage_identity = (stage.stat().st_dev, stage.stat().st_ino)
        sentinel = stage / "tools" / "intermediate-work" / "sentinel"
        assert not sentinel.exists()

        os.kill(launcher_pid, signal.SIGKILL)
        trigger.unlink()
        stdout, stderr = installing.communicate(timeout=10)
        assert installing.returncode != 0, (stdout, stderr)
        log_before_retry = log.read_text()

        retry = _run(environment, "--revision", revision)

        assert retry.returncode != 0, retry.stderr
        assert "subprocess activity" in retry.stderr
        lock = root.parent / f".rhizome-{root.name}.transaction.lock"
        assert (lock / "activity").is_file()
        assert stage.is_dir()
        assert (stage.stat().st_dev, stage.stat().st_ino) == stage_identity
        assert marker.read_text() == marker_before
        assert log.read_text() == log_before_retry
        assert not sentinel.exists()
        assert (root / "current").read_text() == current_before
        assert (root / "previous").read_text() == previous_before
    finally:
        trigger.unlink(missing_ok=True)
        hold.unlink(missing_ok=True)
        if child_pid is not None:
            finished = control / "delayed-child-finished"
            deadline = time.monotonic() + 10
            while not finished.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert finished.exists(), "surviving child did not finish after its hold was released"
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.02)
            else:
                pytest.fail("surviving child did not exit after its hold was released")
        if installing is not None and installing.poll() is None:
            installing.communicate(timeout=10)


def test_signaled_foreground_wrapper_leaves_activity_fenced(
    bootstrap_environment,
):
    environment, _, _ = bootstrap_environment
    control = _control(environment)
    signal_wrapper = control / "signal-wrapper"
    signal_wrapper.touch()
    installing = subprocess.Popen(
        ["sh", str(SCRIPT), "--revision", REVISION_A],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ready = control / "wrapper-signaled"
    deadline = time.monotonic() + 10
    while not ready.exists() and installing.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), installing.communicate(timeout=1)
    child_pid = int((control / "signaled-child-pid").read_text())

    while_child_is_live = _run(environment, "--revision", REVISION_A)

    assert while_child_is_live.returncode != 0
    assert "transaction is active" in while_child_is_live.stderr
    os.kill(child_pid, 0)
    signal_wrapper.unlink()
    installing.communicate(timeout=10)
    after_signal_exit = _run(environment, "--revision", REVISION_A)
    assert after_signal_exit.returncode != 0
    assert "subprocess activity" in after_signal_exit.stderr


def test_interrupted_uninstall_recovers_from_external_owned_transaction(
    bootstrap_environment,
):
    environment, root, _ = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    stage = root.parent / f".rhizome-{root.name}.uninstalling"
    _inject_blocking_partial_rm(environment, stage / "root")
    control = _control(environment)
    hold = control / "uninstall-hold"
    ready = control / "uninstall-ready"
    hold.touch()
    uninstalling = subprocess.Popen(
        ["sh", str(SCRIPT), "--uninstall"],
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 10
    while not ready.exists() and uninstalling.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)
    assert ready.exists(), uninstalling.communicate(timeout=1)
    assert not root.exists()
    assert (stage / "owner").is_file()
    assert (stage / "root").is_dir()

    uninstalling.kill()
    uninstalling.wait(timeout=5)
    hold.unlink()
    uninstalling.communicate(timeout=10)
    recovered = _run(environment, "--uninstall")

    assert recovered.returncode == 0, recovered.stderr
    assert not stage.exists()
    assert not root.exists()
    assert (Path(environment["RHIZOME_STATE_HOME"]) / "draft.json").read_text() == "preserve me"


def test_interrupted_stale_lock_reaper_fails_closed(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    returncode, _, _ = _interrupt_held_install(environment, REVISION_A, 9)
    assert returncode != 0
    transaction_lock = root.parent / f".rhizome-{root.name}.transaction.lock"
    assert (transaction_lock / "owner").is_file()

    _inject_killing_mv(environment, transaction_lock)
    killed_reaper = _run(environment, "--revision", REVISION_A)
    assert killed_reaper.returncode != 0
    assert (transaction_lock / "reaper" / "owner").is_file()

    environment.pop("FAKE_MV_KILL")
    refused = _run(environment, "--revision", REVISION_A)
    assert refused.returncode != 0
    assert "manual inspection" in refused.stderr
    assert (transaction_lock / "reaper" / "owner").is_file()


def test_journaled_rollback_recovers_a_partial_pointer_swap(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    assert _run(environment, "--revision", REVISION_B).returncode == 0
    transactions = root / "transactions"
    (transactions / "rollback").write_text(
        f"rhizome-rollback-v1\nrevisions/{REVISION_A}\nrevisions/{REVISION_B}\n"
    )
    (root / "current").write_text(f"revisions/{REVISION_A}\n")
    (root / "previous").write_text(f"revisions/{REVISION_A}\n")

    recovered = _run(environment, "--rollback")

    assert recovered.returncode == 0, recovered.stderr
    assert "Completed an interrupted rollback" in recovered.stdout
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_A}"
    assert (root / "previous").read_text().strip() == f"revisions/{REVISION_B}"
    assert not (transactions / "rollback").exists()


@pytest.mark.parametrize(
    ("system", "machine", "libc", "asset"),
    [
        ("Darwin", "arm64", "", "uv-aarch64-apple-darwin.tar.gz"),
        ("Darwin", "x86_64", "", "uv-x86_64-apple-darwin.tar.gz"),
        ("Linux", "aarch64", "gnu", "uv-aarch64-unknown-linux-gnu.tar.gz"),
        ("Linux", "x86_64", "gnu", "uv-x86_64-unknown-linux-gnu.tar.gz"),
        ("Linux", "x86_64", "musl", "uv-x86_64-unknown-linux-musl.tar.gz"),
    ],
)
def test_supported_platforms_select_a_pinned_archive(
    bootstrap_environment, system, machine, libc, asset
):
    environment, root, log = bootstrap_environment
    control = _control(environment)
    (control / "uname-s").write_text(f"{system}\n")
    (control / "uname-m").write_text(f"{machine}\n")
    (control / "libc").write_text(f"{libc}\n")
    (control / "bad-checksum").touch()

    result = _run(environment, "--revision", REVISION_A)

    assert result.returncode != 0
    assert asset in log.read_text()
    assert "checksum mismatch" in result.stderr
    assert not root.exists()


def test_uninstall_refuses_a_symlink_even_when_target_looks_owned(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    target = root.parent / "target"
    (target / "revisions").mkdir(parents=True)
    (target / "bin").mkdir()
    (target / ".rhizome-install-root").write_text("rhizome-install-root-v1\n")
    root.symlink_to(target, target_is_directory=True)

    result = _run(environment, "--uninstall")

    assert result.returncode != 0
    assert "symlink installation root" in result.stderr
    assert root.is_symlink()
    assert (target / ".rhizome-install-root").is_file()


def test_refuses_unsupported_platform_and_unmarked_existing_root(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    (_control(environment) / "uname-s").write_text("FreeBSD\n")
    unsupported = _run(environment, "--revision", REVISION_A)
    assert unsupported.returncode != 0
    assert "unsupported platform" in unsupported.stderr.lower()
    assert not root.exists()

    (_control(environment) / "uname-s").write_text("Darwin\n")
    root.mkdir(parents=True)
    (root / "someone-elses-file").write_text("keep")
    unmarked = _run(environment, "--uninstall")
    assert unmarked.returncode != 0
    assert "not marked as Rhizome-owned" in unmarked.stderr
    assert (root / "someone-elses-file").read_text() == "keep"


def test_install_reconciles_an_interrupted_rollback_before_updating(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    assert _run(environment, "--revision", REVISION_B).returncode == 0
    journal = root / "transactions" / "rollback"
    journal.write_text(f"rhizome-rollback-v1\nrevisions/{REVISION_A}\nrevisions/{REVISION_B}\n")
    (root / "current").write_text(f"revisions/{REVISION_A}\n")
    (root / "previous").write_text(f"revisions/{REVISION_A}\n")

    updated = _run(environment, "--revision", REVISION_B)

    assert updated.returncode == 0, updated.stderr
    assert not journal.exists()
    assert (root / "current").read_text().strip() == f"revisions/{REVISION_B}"
    assert (root / "previous").read_text().strip() == f"revisions/{REVISION_A}"


@pytest.mark.parametrize("pointer", ["current", "previous"])
@pytest.mark.parametrize("kind", ["directory", "symlink", "foreign-file"])
def test_uninstall_refuses_foreign_reserved_pointer_entries(bootstrap_environment, pointer, kind):
    environment, root, _ = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    path = root / pointer
    if path.exists() or path.is_symlink():
        path.unlink()
    preserved: Path
    if kind == "directory":
        path.mkdir()
        preserved = path / "foreign.txt"
        preserved.write_text("preserve")
    elif kind == "symlink":
        preserved = root.parent / f"{pointer}-foreign-target"
        preserved.write_text("preserve")
        path.symlink_to(preserved)
    else:
        preserved = path
        path.write_text("foreign pointer contents\n")

    refused = _run(environment, "--uninstall")

    assert refused.returncode != 0
    assert "pointer" in refused.stderr
    assert preserved.exists() or preserved.is_symlink()
    assert root.is_dir()


def test_completed_payload_with_installing_marker_is_reconciled_before_publication(
    bootstrap_environment,
):
    environment, root, _ = bootstrap_environment
    assert _run(environment, "--revision", REVISION_A).returncode == 0
    payload = root / "store" / REVISION_B
    shutil.copytree(root / "store" / REVISION_A, payload)
    (payload / ".rhizome-revision").write_text(f"{REVISION_B}\n")
    (payload / ".rhizome-installing").write_text(f"{REVISION_B}\n999999\n")
    publication_staging = root / "revisions" / f".staging-{REVISION_B}"
    _inject_killing_mv(environment, publication_staging)

    interrupted = _run(environment, "--revision", REVISION_B)

    assert interrupted.returncode != 0
    assert payload.is_dir()
    assert (payload / ".rhizome-revision").read_text().strip() == REVISION_B
    assert not (payload / ".rhizome-installing").exists()


def test_unmarked_first_root_staging_remnant_fails_closed(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    remnant = root.parent / ".rhizome-root.999999.staging"
    remnant.mkdir(parents=True)
    foreign = remnant / "foreign.txt"
    foreign.write_text("preserve")

    refused = _run(environment, "--revision", REVISION_A)

    assert refused.returncode != 0
    assert "manual inspection" in refused.stderr
    assert foreign.read_text() == "preserve"
    assert not root.exists()


def test_owned_first_root_staging_remnant_is_recovered(bootstrap_environment):
    environment, root, _ = bootstrap_environment
    remnant = root.parent / ".rhizome-root.999999.staging"
    remnant.mkdir(parents=True)
    (remnant / ".rhizome-install-root").write_text("rhizome-install-root-v1\n")
    for name in ("revisions", "store", "bin", "transactions"):
        (remnant / name).mkdir()

    installed = _run(environment, "--revision", REVISION_A)

    assert installed.returncode == 0, installed.stderr
    assert not remnant.exists()
