"""Dependency-aware conversion cache and stable writes, shared by CLI/adapters.

Only completed tasks are cached. Dependencies include failed lookups, so adding
an override or repairing a missing source invalidates the appropriate outputs.
Caches are disposable build metadata and never used by the game.
"""
from __future__ import annotations

import contextvars
import dataclasses
import hashlib
import json
import os
import sys
import time
import uuid
from collections import OrderedDict
from pathlib import Path
from typing import Callable

_frames = contextvars.ContextVar('ragx_build_frames', default=())


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest() if hasattr(hashlib, 'file_digest') else digest(stream.read())


def code_signature(root: Path | None = None) -> str:
    root = root or Path(__file__).parent
    sha = hashlib.sha256()
    for path in sorted(root.rglob('*.py')):
        sha.update(path.relative_to(root).as_posix().encode())
        sha.update(path.read_bytes())
    return sha.hexdigest()


def write_bytes(path: Path, data: bytes, *, record: bool = True) -> bool:
    """Replace only changed bytes; never hide publication errors from the caller."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    expected = digest(data)
    changed = not path.is_file() or file_digest(path) != expected
    if changed:
        # On Windows os.open(O_EXCL), used by mkstemp, dominated the profile.
        # A unique per-write name keeps publication atomic without that slow path.
        temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
        try:
            temporary.write_bytes(data)
            for attempt in range(8):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError as error:
                    # Windows denies replacement while another worker reads
                    # the shared target. Bound retries; real publication errors
                    # must still fail the task instead of marking it complete.
                    if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 7:
                        raise
                    time.sleep(0.01 * (2 ** attempt))
        finally:
            Path(temporary).unlink(missing_ok=True)
    if record:
        for frame in _frames.get():
            relative = path.resolve().relative_to(frame['root']).as_posix()
            frame['outputs'][relative] = expected
    return changed


def write_json(path: Path, value, *, record: bool = True) -> bool:
    return write_bytes(path, json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(',', ':')).encode('utf-8'), record=record)


def save_png(path: Path, image) -> None:
    import io
    buffer = io.BytesIO()
    image.save(buffer, format='PNG', compress_level=1, optimize=False)
    write_bytes(path, buffer.getvalue())


class TrackedSource:
    """Record resolved archive fingerprints without decoding during warm checks."""
    def __init__(self, reader):
        self.reader = reader
        self._fingerprints = {}
        self._texture_stems = None

    def fingerprint(self, path: str):
        path = path.lower().replace('/', '\\')
        if path not in self._fingerprints:
            if path.startswith('@stem:'):
                if self._texture_stems is None:
                    self._texture_stems = {}
                    for name in self.reader.namelist():
                        if name.startswith('data\\texture\\') and '.' in name:
                            self._texture_stems.setdefault(name.rsplit('.', 1)[0], []).append(name)
                self._fingerprints[path] = digest('\n'.join(sorted(self._texture_stems.get(path[6:], []))).encode())
                return self._fingerprints[path]
            try:
                self._fingerprints[path] = self.reader.fingerprint(path) if hasattr(self.reader, 'fingerprint') else digest(self.reader.read(path))
            except (FileNotFoundError, KeyError):
                self._fingerprints[path] = None
        return self._fingerprints[path]

    def touch(self, path: str) -> None:
        path = path.lower().replace('/', '\\')
        value = self.fingerprint(path)
        for frame in _frames.get():
            frame['dependencies'][path] = value

    def read(self, path: str) -> bytes:
        self.touch(path)
        return self.reader.read(path)

    def __contains__(self, path: str) -> bool:
        self.touch(path)
        return self.fingerprint(path) is not None

    def close(self) -> None:
        self.reader.close()

    def namelist(self) -> list[str]:
        # Discovery is not a dependency on every unrelated file in the archive.
        # Callers record actual reads, misses, or a scoped @stem filename query.
        return self.reader.namelist()

    def matches(self, dependencies: dict) -> bool:
        for path, expected in dependencies.items():
            actual = self.fingerprint(path)
            if actual != expected:
                return False
        return True


class BuildCache:
    def __init__(self, root: Path, source: TrackedSource, signature: str | None = None):
        self.root = Path(root).resolve()
        self.source = source
        self.signature = signature or code_signature()
        self.hits = 0
        self.builds = 0
        self._verified_outputs = {}
        self._completed = {}

    def run(self, key: str, settings: dict, build: Callable):
        path = self.root / '.ragx-cache' / (digest(key.encode()) + '.json')
        stamp = {'schema': 1, 'code': self.signature, 'settings': settings}
        try:
            previous = self._completed.get(key)
            if previous is None:
                previous = json.loads(path.read_text(encoding='utf-8'))
            valid = previous['stamp'] == stamp and self.source.matches(previous['dependencies'])
            if valid:
                for relative, expected in previous['outputs'].items():
                    output = (self.root / relative).resolve()
                    output.relative_to(self.root)
                    if not output.is_file():
                        valid = False
                        break
                    stat = output.stat()
                    observed = (stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
                    remembered = self._verified_outputs.get(output)
                    actual = remembered[1] if remembered and remembered[0] == observed else file_digest(output)
                    self._verified_outputs[output] = (observed, actual)
                    if actual != expected:
                        valid = False
                        break
            if valid:
                self._completed[key] = previous
                self.hits += 1
                for frame in _frames.get():
                    frame['dependencies'].update(previous['dependencies'])
                    frame['outputs'].update(previous['outputs'])
                return previous['result']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.builds += 1
        path.unlink(missing_ok=True)
        frame = {'root': self.root, 'dependencies': {}, 'outputs': {}}
        token = _frames.set((*_frames.get(), frame))
        try:
            result = build()
            entry = {'stamp': stamp, 'dependencies': frame['dependencies'],
                     'outputs': frame['outputs'], 'result': result}
            write_json(path, entry, record=False)
            self._completed[key] = entry
            return result
        finally:
            _frames.reset(token)


def retained_size(value, seen: set | None = None) -> int:
    """Conservative owned-object size; ndarray.__sizeof__ includes owned storage."""
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    total = sys.getsizeof(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        total += sum(retained_size(getattr(value, f.name), seen) for f in dataclasses.fields(value))
    elif isinstance(value, dict):
        total += sum(retained_size(k, seen) + retained_size(v, seen) for k, v in value.items())
    elif isinstance(value, (tuple, list)):
        total += sum(retained_size(v, seen) for v in value)
    elif getattr(value, 'base', None) is not None:
        total += retained_size(value.base, seen)
    return total


class ByteLRU:
    """Evict least-recent entries incrementally rather than clearing every hit."""
    def __init__(self, budget: int):
        self.budget = budget
        self.bytes = 0
        self.entries = OrderedDict()

    def __contains__(self, key):
        return key in self.entries

    def __getitem__(self, key):
        value, size = self.entries[key]
        self.entries.move_to_end(key)
        return value

    def __setitem__(self, key, value):
        if key in self.entries:
            self.bytes -= self.entries.pop(key)[1]
        size = retained_size(value) + retained_size(key)
        if size > self.budget:
            return
        while self.entries and self.bytes + size > self.budget:
            self.bytes -= self.entries.popitem(last=False)[1][1]
        if size <= self.budget:
            self.entries[key] = (value, size)
            self.bytes += size

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def __len__(self):
        return len(self.entries)
