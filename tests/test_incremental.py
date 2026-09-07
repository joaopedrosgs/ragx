"""Cache correctness uses changed inputs, corrupt outputs and interrupted writes."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ragx.incremental import BuildCache, ByteLRU, TrackedSource, write_bytes


class Source:
    def __init__(self):
        self.files = {'a': b'first', 'b': b'second'}

    def read(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def namelist(self):
        return list(self.files)


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.reader = Source()

    def cache(self, signature='test'):
        return BuildCache(self.root, TrackedSource(self.reader), signature)

    def produce(self, cache, name, extra=None):
        def build():
            raw = cache.source.read(name)
            if extra is not None:
                try:
                    raw += cache.source.read(extra)
                except FileNotFoundError:
                    pass
            write_bytes(self.root / (name + '.bin'), raw)
            return {'name': name}
        return cache.run(name, {}, build)

    def test_noop_retains_bytes_and_mtime(self):
        self.produce(self.cache(), 'a')
        before = (self.root / 'a.bin').stat().st_mtime_ns
        cache = self.cache()
        self.produce(cache, 'a')
        self.assertEqual((cache.hits, cache.builds), (1, 0))
        self.assertEqual((self.root / 'a.bin').stat().st_mtime_ns, before)

    def test_changes_only_rebuild_dependent_output(self):
        cache = self.cache()
        self.produce(cache, 'a')
        self.produce(cache, 'b')
        before = (self.root / 'b.bin').stat().st_mtime_ns
        self.reader.files['a'] = b'changed'
        cache = self.cache()
        self.produce(cache, 'a')
        self.produce(cache, 'b')
        self.assertEqual((cache.hits, cache.builds), (1, 1))
        self.assertEqual((self.root / 'a.bin').read_bytes(), b'changed')
        self.assertEqual((self.root / 'b.bin').stat().st_mtime_ns, before)

    def test_unrelated_added_entry_does_not_invalidate(self):
        self.produce(self.cache(), 'a')
        self.reader.files['unrelated'] = b'new'
        cache = self.cache()
        self.produce(cache, 'a')
        self.assertEqual(cache.hits, 1)

    def test_texture_filename_collision_invalidates_only_that_stem(self):
        self.reader.files['data\\texture\\wall.bmp'] = b'image'
        cache = self.cache()
        def build():
            cache.source.touch('@stem:data\\texture\\wall')
            return 'wall.png'
        cache.run('texture-name', {}, build)
        self.reader.files['data\\texture\\wall.tga'] = b'other image'
        cache = self.cache()
        cache.run('texture-name', {}, build)
        self.assertEqual(cache.builds, 1)

    def test_missing_dependency_added_invalidates(self):
        self.produce(self.cache(), 'a', 'optional')
        self.reader.files['optional'] = b'new'
        cache = self.cache()
        self.produce(cache, 'a', 'optional')
        self.assertEqual(cache.builds, 1)
        self.assertEqual((self.root / 'a.bin').read_bytes(), b'firstnew')

    def test_same_size_corruption_is_repaired(self):
        self.produce(self.cache(), 'a')
        (self.root / 'a.bin').write_bytes(b'xxxxx')
        cache = self.cache()
        self.produce(cache, 'a')
        self.assertEqual(cache.builds, 1)
        self.assertEqual((self.root / 'a.bin').read_bytes(), b'first')

    def test_converter_revision_invalidates_but_unchanged_bytes_are_preserved(self):
        self.produce(self.cache(), 'a')
        before = (self.root / 'a.bin').stat().st_mtime_ns
        cache = self.cache('new-version')
        self.produce(cache, 'a')
        self.assertEqual(cache.builds, 1)
        self.assertEqual((self.root / 'a.bin').stat().st_mtime_ns, before)

    def test_failed_publication_is_loud_and_not_cached(self):
        with patch('ragx.incremental.os.replace', side_effect=PermissionError('locked')):
            with self.assertRaises(PermissionError):
                self.produce(self.cache(), 'a')
        self.assertEqual(list(self.root.rglob('*.tmp')), [])
        self.assertEqual(list(self.root.rglob('*.json')), [])
        self.produce(self.cache(), 'a')

    def test_nested_hit_retains_dependencies_and_outputs(self):
        cache = self.cache()
        self.produce(cache, 'a')
        cache.run('parent', {}, lambda: self.produce(cache, 'a'))
        (self.root / 'a.bin').unlink()
        cache = self.cache()
        cache.run('parent', {}, lambda: self.produce(cache, 'a'))
        self.assertEqual(cache.builds, 2)
        self.assertEqual((self.root / 'a.bin').read_bytes(), b'first')

    def test_interrupted_build_does_not_create_completion_record(self):
        def interrupted():
            write_bytes(self.root / 'partial', b'partial')
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            self.cache().run('interrupt', {}, interrupted)
        self.assertEqual(list((self.root / '.ragx-cache').glob('*.json')), [])

    def test_lru_evicts_only_old_entries_and_declines_oversized_values(self):
        lru = ByteLRU(400)
        lru['a'] = b'a' * 100
        lru['b'] = b'b' * 100
        self.assertEqual(lru['a'], b'a' * 100)
        lru['c'] = b'c' * 100
        self.assertIn('a', lru)
        self.assertNotIn('b', lru)
        lru['large'] = b'x' * 1000
        self.assertNotIn('large', lru)
        self.assertLessEqual(lru.bytes, lru.budget)


if __name__ == '__main__':
    unittest.main()
