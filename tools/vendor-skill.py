"""Copy or verify the Codex skill against a pinned claude-brewcode commit."""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = 'openclaw/brewpage-publish'
SKILL = 'skills/brewpage-publish'
PROVENANCE = 'PROVENANCE.json'
MEMBERS = {
    'LICENSE': '100644',
    'README.md': '100644',
    'SKILL.md': '100644',
    'references/custom-domain.md': '100644',
    'references/ownership.md': '100644',
    'references/page-data.md': '100644',
    'references/publishing.md': '100644',
    'scripts/brewpage-lib.sh': '100644',
    'scripts/brewpage-run.sh': '100755',
    'scripts/history.py': '100644',
    'scripts/publish.mjs': '100755',
}


def git(repo, *args):
    return subprocess.run(['git', '--no-replace-objects', '-C', str(repo), *args], check=True, capture_output=True).stdout


def sha(data):
    return hashlib.sha256(data).hexdigest()


def source_files(repo, commit):
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or git(repo, 'rev-parse', '--verify', commit + '^{commit}').decode().strip() != commit:
        raise ValueError('expected a full upstream commit hash')
    source = {}
    listing = git(repo, 'ls-tree', '-rz', commit, '--', UPSTREAM).decode()
    for entry in listing.split('\0')[:-1]:
        attributes, _, path = entry.partition('\t')
        mode, kind, object_id = attributes.split()
        member = path.removeprefix(UPSTREAM + '/')
        if kind != 'blob' or MEMBERS.get(member) != mode:
            raise ValueError('unexpected upstream path or mode: ' + path)
        source[member] = git(repo, 'cat-file', 'blob', object_id)
    if source.keys() != MEMBERS.keys():
        raise ValueError('upstream skill members are missing: ' + ', '.join(sorted(MEMBERS.keys() - source.keys())))
    return source


def adapt(source, version):
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('expected an exact Codex manifest version')
    pattern = rb'^BP_UA="(OpenClaw/\d+\.\d+\.\d+ \(\+https://brewpage\.app\))"$'
    tokens = re.findall(pattern, source['scripts/brewpage-lib.sh'], re.MULTILINE)
    if len(tokens) != 1:
        raise ValueError('expected one complete OpenClaw User-Agent in the upstream library')
    token, = tokens
    agent = f'brewpage-codex/{version} (+https://brewpage.app)'
    files = {name: data.replace(token, agent.encode()) for name, data in source.items()}
    if any(token.split()[0] in data for data in files.values()):
        raise ValueError('upstream User-Agent product occurs outside the full value')
    return agent, files


def sync(upstream, commit, write):
    manifest = ROOT / PROVENANCE
    if manifest.is_symlink() or (manifest.exists() and not manifest.is_file()):
        raise ValueError('expected a regular provenance file')
    version = json.loads((ROOT / '.codex-plugin/plugin.json').read_text(encoding='utf-8'))['version']
    if not write:
        commit = json.loads(manifest.read_text(encoding='utf-8'))['upstream_commit']
    source = source_files(upstream, commit)
    agent, files = adapt(source, version)
    proof = {
        'upstream_repository': 'https://github.com/kochetkov-ma/claude-brewcode',
        'upstream_commit': commit,
        'upstream_directory': UPSTREAM,
        'plugin_version': version,
        'user_agent': agent,
        'files': [{'path': name, 'mode': mode, 'upstream_sha256': sha(source[name]), 'sha256': sha(files[name])}
                  for name, mode in MEMBERS.items()],
    }
    expected = (json.dumps(proof, indent=2) + '\n').encode()
    skill = ROOT / SKILL
    if skill.is_symlink() or skill.parent.is_symlink() or not skill.is_dir():
        raise ValueError('expected an installed skill directory')
    directories = {'references', 'scripts'}
    for path in skill.rglob('*'):
        name = path.relative_to(skill).as_posix()
        valid = path.is_dir() if name in directories else name in MEMBERS and path.is_file()
        if path.is_symlink() or not valid:
            raise ValueError('unexpected Codex skill member: ' + name)

    differences = []
    for name, mode in MEMBERS.items():
        path = skill / name
        expected_mode = int(mode[-3:], 8)
        if path.is_file() and path.read_bytes() == files[name] and (path.stat().st_mode & 0o777) == expected_mode:
            continue
        if write:
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(files[name])
            path.chmod(expected_mode)
        else:
            differences.append(name)
    if write:
        manifest.write_bytes(expected)
    elif manifest.read_bytes() != expected:
        differences.append(PROVENANCE)
    if differences:
        raise ValueError('Codex skill differs from its source: ' + ', '.join(differences))
    print(f'{len(MEMBERS)} Codex skill files match {commit} with User-Agent {agent}')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('vendor', 'check'))
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--commit')
    args = parser.parse_args()
    write = args.command == 'vendor'
    if write != bool(args.commit):
        parser.error('only vendor requires --commit')
    try:
        sync(args.upstream, args.commit, write)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        sys.exit('FAILED: ' + str(error))


if __name__ == '__main__':
    main()
