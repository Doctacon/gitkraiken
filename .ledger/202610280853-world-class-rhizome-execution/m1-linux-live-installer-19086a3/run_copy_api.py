"""Run the real pinned lifecycle via Docker's copy API, without host mounts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

parser = argparse.ArgumentParser()
parser.add_argument('--platform', choices=['linux/arm64', 'linux/amd64'], default='linux/arm64')
parser.add_argument('--output', type=Path)
args = parser.parse_args()
HERE = Path(__file__).resolve().parent
REPORT = (args.output or HERE / ('copy-api-' + args.platform.split('/')[1])).resolve()
REPORT.mkdir(parents=True, exist_ok=True)
assert not any(REPORT.iterdir()), 'Use a fresh evidence directory; do not overwrite failures'
START = time.monotonic()
CANDIDATE = '19086a362dd9ac48bdffdd461816add47ca7c6ed'
source = HERE.parent / 'm1-live-installer-19086a3/attempt-2/run_lifecycle.py'
host = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True, timeout=10).strip()
assert host.startswith('unix://'), 'Refuse a remote Docker endpoint'
docker = ['docker', '--host', host]
env = {k: v for k, v in os.environ.items() if k not in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
nonce = uuid.uuid4().hex
name = 'rhizome-m1-install-' + nonce[:12]
staging = Path(tempfile.mkdtemp(prefix='rhizome-linux-copy-', dir='/tmp')).resolve()
report = {'candidate': CANDIDATE, 'platform_requested': args.platform,
          'transport': 'Docker copy API; no bind mounts or daemon configuration changes',
          'container': name, 'staging': str(staging), 'passed': False, 'removed': False}
container = name


def redact(text):
    return re.sub(r'(https?://)[^/@\s]+:[^/@\s]+@', r'\1<REDACTED>@', text)


def call(arguments, **kwargs):
    return subprocess.run([*docker, *arguments], env=env, text=True, capture_output=True, **kwargs)


def save_log(name, result):
    (REPORT / name).write_text(redact(result.stdout + result.stderr))


try:
    expected = subprocess.check_output(['git', 'show', f'{CANDIDATE}:scripts/install.sh'], timeout=10)
    (staging / 'reviewed-install.sh').write_bytes(expected)
    report['reviewed_bootstrap_sha256'] = hashlib.sha256(expected).hexdigest()
    driver = source.read_text()
    assert f"B = '{CANDIDATE}'" in driver
    old = "expected = subprocess.check_output(['git', 'show', f'{B}:scripts/install.sh'], timeout=10)"
    assert driver.count(old) == 1
    driver = driver.replace(old, "expected = Path('/validation/reviewed-install.sh').read_bytes()")
    driver = driver.replace('en_US.UTF-8', 'C.UTF-8').replace('DEADLINE = START + 420', 'DEADLINE = START + 240')
    # Version assertions consume stdout, not QEMU/jemalloc diagnostics on stderr.
    # Retain stderr verbatim (after redaction) rather than suppressing warnings.
    assert driver.count('stderr=subprocess.STDOUT,') == 1
    assert driver.count('output, _ = process.communicate(') == 3
    driver = driver.replace('stderr=subprocess.STDOUT,', 'stderr=subprocess.PIPE,')
    driver = driver.replace('output, _ = process.communicate(', 'output, errors = process.communicate(')
    old_log = "(REPORT / f'{name}.txt').write_text(output)"
    assert driver.count(old_log) == 1
    driver = driver.replace(old_log, "(REPORT / f'{name}.txt').write_text(output + ('\\n[stderr]\\n' + redact(errors) if errors else ''))")
    (staging / 'run_lifecycle.py').write_text(driver)
    command = '''set -eu
test "$(uname -m)" = "$RHIZOME_VALIDATION_ARCH"
test -f /validation/reviewed-install.sh
test -f /validation/run_lifecycle.py
mkdir -p /work/results
(apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl git python3 tar gzip) > /work/results/setup.txt 2>&1
cp /validation/run_lifecycle.py /work/results/run_lifecycle.py
cd /work
uname -a > /work/results/platform.txt
python3 --version >> /work/results/platform.txt
git --version >> /work/results/platform.txt
cp /etc/os-release /work/results/os-release.txt
python3 /work/results/run_lifecycle.py
'''
    result = call(['create', '--pull=missing', '--platform', args.platform, '--name', name,
                   '--label', 'rhizome.validation=' + nonce, '--init', '--cpus', '2',
                   '--memory', '512m', '--pids-limit', '128', '--security-opt', 'no-new-privileges',
                   '--env', 'DEBIAN_FRONTEND=noninteractive', '--env', 'GIT_CONFIG_NOSYSTEM=1',
                   '--env', 'GIT_CONFIG_GLOBAL=/dev/null', '--env',
                   'RHIZOME_VALIDATION_ARCH=' + ('aarch64' if args.platform == 'linux/arm64' else 'x86_64'),
                   'ubuntu:24.04', '/bin/sh', '-c', command], timeout=90)
    save_log('create.txt', result)
    assert result.returncode == 0, 'Container creation failed'
    container = result.stdout.strip().splitlines()[-1]
    assert re.fullmatch('[a-f0-9]{64}', container)
    report['container_id'] = container
    image = call(['inspect', '--format', '{{.Image}}', container], timeout=10)
    assert image.returncode == 0
    report['image_id'] = image.stdout.strip()
    copied = call(['cp', str(staging) + '/.', container + ':/validation'], timeout=20)
    save_log('copy-inputs.txt', copied)
    assert copied.returncode == 0, 'Docker API input copy failed'
    result = call(['start', '--attach', container], timeout=max(1, 360 - (time.monotonic() - START)))
    save_log('execution.txt', result)
    report['execution_exit_code'] = result.returncode
    copied = call(['cp', container + ':/work/results', str(REPORT / 'results')], timeout=20)
    assert copied.returncode == 0, 'Could not retain container evidence'
    assert result.returncode == 0, 'Linux lifecycle failed; see execution/results logs'
    lifecycle = json.loads((REPORT / 'results/summary.json').read_text())
    assert lifecycle['candidate'] == CANDIDATE
    assert lifecycle['bootstrap_sha256'] == report['reviewed_bootstrap_sha256']
    assert lifecycle['passed'] and lifecycle['sandbox_removed']
    report['passed'] = True
except Exception as error:
    report['error'] = redact(f'{type(error).__name__}: {error}')
finally:
    owner = call(['inspect', '--format', '{{index .Config.Labels "rhizome.validation"}}', container], timeout=10)
    if owner.returncode == 0 and owner.stdout.strip() == nonce:
        stopped = call(['stop', '--time', '10', container], timeout=20)
        report['stop_exit_code'] = stopped.returncode
        running = call(['inspect', '--format', '{{.State.Running}}', container], timeout=10)
        if running.stdout.strip() == 'true':
            killed = call(['kill', container], timeout=10)
            report['kill_exit_code'] = killed.returncode
            running = call(['inspect', '--format', '{{.State.Running}}', container], timeout=10)
        report['stopped'] = running.returncode == 0 and running.stdout.strip() == 'false'
        if report['passed'] and report['stopped']:
            removed = call(['rm', container], timeout=10)
            report['removed'] = removed.returncode == 0
        else:
            call(['cp', container + ':/work/results', str(REPORT / 'retained-results')], timeout=20)
    if report['passed'] and report['removed']:
        shutil.rmtree(staging)
    report['elapsed_seconds'] = round(time.monotonic() - START, 2)
    (REPORT / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2), flush=True)
sys.exit(0 if report['passed'] and report['removed'] else 1)
