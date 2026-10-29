"""Real Linux installer validation using an already-running local Docker engine."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

REPORT = Path(__file__).resolve().parent
START = time.monotonic()
source = REPORT.parent / 'm1-live-installer-19086a3/attempt-2/run_lifecycle.py'
host = subprocess.check_output(['docker', 'context', 'inspect', '--format', '{{.Endpoints.docker.Host}}'], text=True).strip()
assert host.startswith('unix://'), 'Refuse a remote Docker endpoint'
docker = ['docker', '--host', host]
env = {k: v for k, v in os.environ.items() if k not in ('DOCKER_CONTEXT', 'DOCKER_HOST', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH')}
nonce = uuid.uuid4().hex
name = 'rhizome-m1-install-' + nonce[:12]
staging = Path(tempfile.mkdtemp(prefix='rhizome-linux-validation-', dir='/tmp')).resolve()
report = {'candidate': '19086a362dd9ac48bdffdd461816add47ca7c6ed',
          'platform_requested': 'linux/arm64', 'container': name, 'staging': str(staging),
          'passed': False, 'removed': False}
created = False


def call(args, **kwargs):
    return subprocess.run([*docker, *args], env=env, text=True, capture_output=True, **kwargs)


try:
    assert subprocess.check_output(['git', 'rev-parse', 'HEAD'], text=True).strip() == report['candidate']
    subprocess.run(['git', 'bundle', 'create', str(staging / 'candidate.bundle'), 'HEAD'], check=True, timeout=20)
    driver = source.read_text().replace('en_US.UTF-8', 'C.UTF-8').replace('DEADLINE = START + 420', 'DEADLINE = START + 240')
    (staging / 'run_lifecycle.py').write_text(driver)
    command = '''set -eu
mkdir -p /work/results
(apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates curl git python3 tar gzip) > /work/results/setup.txt 2>&1
cp /validation/run_lifecycle.py /work/results/run_lifecycle.py
git clone --quiet --no-checkout /validation/candidate.bundle /work/source
cd /work/source
uname -a > /work/results/platform.txt
python3 --version >> /work/results/platform.txt
git --version >> /work/results/platform.txt
python3 /work/results/run_lifecycle.py
'''
    result = call(['create', '--pull=missing', '--platform', 'linux/arm64', '--name', name,
                   '--label', 'rhizome.validation=' + nonce, '--init', '--cpus', '2',
                   '--memory', '512m', '--pids-limit', '128', '--security-opt', 'no-new-privileges',
                   '--env', 'DEBIAN_FRONTEND=noninteractive', '--env', 'GIT_CONFIG_NOSYSTEM=1',
                   '--env', 'GIT_CONFIG_GLOBAL=/dev/null', '--mount',
                   f'type=bind,source={staging},target=/validation,readonly',
                   'ubuntu:24.04', '/bin/sh', '-c', command], timeout=90)
    (REPORT / 'create.txt').write_text(result.stdout + result.stderr)
    assert result.returncode == 0, 'Container creation failed'
    created = True
    report['container_id'] = result.stdout.strip().splitlines()[-1]
    image = call(['inspect', '--format', '{{.Image}}', name], timeout=10)
    assert image.returncode == 0
    report['image_id'] = image.stdout.strip()
    result = call(['start', '--attach', name], timeout=max(1, 300 - (time.monotonic() - START)))
    (REPORT / 'execution.txt').write_text(result.stdout + result.stderr)
    report['execution_exit_code'] = result.returncode
    copied = call(['cp', name + ':/work/results', str(REPORT / 'results')], timeout=20)
    assert copied.returncode == 0, 'Could not retain container evidence'
    assert result.returncode == 0, 'Linux lifecycle failed; see execution/results logs'
    lifecycle = json.loads((REPORT / 'results/summary.json').read_text())
    assert lifecycle['passed'] and lifecycle['sandbox_removed']
    report['passed'] = True
except Exception as error:
    report['error'] = f'{type(error).__name__}: {error}'
finally:
    if created:
        owner = call(['inspect', '--format', '{{index .Config.Labels "rhizome.validation"}}', name], timeout=10)
        if owner.returncode == 0 and owner.stdout.strip() == nonce:
            stopped = call(['stop', '--time', '10', name], timeout=20)
            report['stop_exit_code'] = stopped.returncode
            if report['passed']:
                removed = call(['rm', name], timeout=10)
                report['removed'] = removed.returncode == 0
            else:
                call(['cp', name + ':/work/results', str(REPORT / 'retained-results')], timeout=20)
    if report['passed'] and report['removed']:
        shutil.rmtree(staging)
    report['elapsed_seconds'] = round(time.monotonic() - START, 2)
    (REPORT / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2), flush=True)
sys.exit(0 if report['passed'] and report['removed'] else 1)
