"""Bounded complete-collection validation; no project code or test mutations."""
import collections
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
START = time.monotonic()
CANDIDATE = '80f5e8f7586ee7e3630b0dbc43aeef00b26eed95'


def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()


def fingerprint():
    paths = git('ls-files', 'src', 'tests', 'scripts', '.github',
                'pyproject.toml', 'uv.lock', 'README.md').splitlines()
    return {p: hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in paths}


assert git('rev-parse', 'HEAD') == CANDIDATE
before = fingerprint()
(HERE / 'candidate-inputs.json').write_text(json.dumps({
    'revision': CANDIDATE, 'sha256': before,
}, indent=2) + '\n')
collected = subprocess.run(
    [sys.executable, '-m', 'pytest', '--collect-only', '-q', '-p', 'no:cacheprovider'],
    text=True, capture_output=True, timeout=45,
)
(HERE / 'collection.txt').write_text(collected.stdout + collected.stderr)
assert collected.returncode == 0, 'Collection failed; see collection.txt'
expected = [line for line in collected.stdout.splitlines()
            if line.startswith('tests/') and '::' in line
            and line.split('::', 1)[0].endswith('.py')]
assert expected and len(set(expected)) == len(expected)
counts = collections.Counter(node.split('::', 1)[0] for node in expected)
groups = [['tests/test_app.py'], [], [], []]
weights = [0, 0, 0, 0]
remaining = [p for p in counts if p != 'tests/test_app.py']
weights_by_path = {p: counts[p] * (4 if 'async def test_' in Path(p).read_text() else 1)
                   for p in remaining}
for path in sorted(remaining, key=lambda p: (-weights_by_path[p], p)):
    chosen = min(range(1, 4), key=lambda i: weights[i])
    groups[chosen].append(path)
    weights[chosen] += weights_by_path[path]
assert set(sum(groups, [])) == set(counts)
assert len(sum(groups, [])) == len(counts)
(HERE / 'partitions.json').write_text(json.dumps(groups, indent=2) + '\n')
print(f'Candidate {CANDIDATE}: {len(expected)} cases in four disjoint partitions.', flush=True)
processes = []
logs = []
timed_out = False
try:
    for index, paths in enumerate(groups):
        log = (HERE / f'partition-{index + 1}.txt').open('w')
        logs.append(log)
        command = [sys.executable, '-m', 'pytest', *paths, '-q', '--tb=short',
                   '-p', 'no:cacheprovider', f'--junitxml={HERE / f"partition-{index + 1}.xml"}']
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                   start_new_session=True)
        processes.append(process)
    while any(p.poll() is None for p in processes):
        if time.monotonic() - START > 600:
            timed_out = True
            break
        time.sleep(1)
finally:
    for p in processes:
        if p.poll() is None:
            # Only the pytest process groups created above; allow fixture cleanup.
            os.killpg(p.pid, signal.SIGINT)
    for p in processes:
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait(timeout=5)
    for log in logs:
        log.close()

actual = []
failures = []
modules = {p[:-3].replace('/', '.'): p for p in counts}
for index in range(4):
    xml_path = HERE / f'partition-{index + 1}.xml'
    if not xml_path.exists():
        failures.append(f'partition-{index + 1}: no JUnit output')
        continue
    tree = ET.parse(xml_path)
    for case in tree.iter('testcase'):
        classname = case.attrib.get('classname', '')
        matching = [prefix for prefix in modules if classname == prefix or classname.startswith(prefix + '.')]
        if not matching:
            failures.append(f'Unmapped JUnit class: {classname}')
            continue
        prefix = max(matching, key=len)
        nested = classname[len(prefix):].strip('.').replace('.', '::')
        node = modules[prefix] + '::' + (nested + '::' if nested else '') + case.attrib['name']
        actual.append(node)
        if any(child.tag in ('failure', 'error', 'skipped') for child in case):
            failures.append(node)
coverage = collections.Counter(actual)
missing = sorted(set(expected) - set(actual))
unexpected = sorted(set(actual) - set(expected))
duplicates = sorted(node for node, count in coverage.items() if count != 1)
unchanged = fingerprint() == before and git('rev-parse', 'HEAD') == CANDIDATE
report = {
    'candidate': CANDIDATE, 'expected': len(expected), 'executed': len(actual),
    'partition_exit_codes': [p.returncode for p in processes],
    'missing': missing, 'unexpected': unexpected, 'duplicates': duplicates,
    'failed_or_skipped': failures, 'timed_out': timed_out,
    'inputs_unchanged': unchanged, 'elapsed_seconds': round(time.monotonic() - START, 2),
}
report['passed'] = (not timed_out and unchanged and len(processes) == 4
                    and all(p.returncode == 0 for p in processes)
                    and not (missing or unexpected or duplicates or failures))
(HERE / 'summary.json').write_text(json.dumps(report, indent=2) + '\n')
print(json.dumps(report, indent=2), flush=True)
sys.exit(0 if report['passed'] else 1)
