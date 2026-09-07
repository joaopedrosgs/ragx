import unittest
from ragx.resource_aliases import ResourceAliases


class Archive:
    def __init__(self, table):
        self.data = {'data\\resnametable.txt': table, 'data\\base.rsw': b'art'}

    def namelist(self):
        return list(self.data)

    def read(self, key):
        return self.data[key]

    def fingerprint(self, key):
        return self.data[key].hex()


class AliasTests(unittest.TestCase):
    def test_aliases_and_cache_invalidation(self):
        archive = Archive(b'city.rsw#base.rsw#\n')
        source = ResourceAliases(archive)
        self.assertEqual(source.read('data/city.rsw'), b'art')
        self.assertIn('data\\city.rsw', source.namelist())
        before = source.fingerprint('data/city.rsw')
        archive.data['data\\base.rsw'] = b'changed'
        self.assertNotEqual(before, source.fingerprint('data/city.rsw'))
        self.assertIsNone(source.fingerprint('data/missing.rsw'))

    def test_direct_entry_wins_and_cycles_fail(self):
        archive = Archive(b'base.rsw#missing.rsw#\na.rsw#b.rsw#\nb.rsw#a.rsw#')
        source = ResourceAliases(archive)
        self.assertEqual(source.read('data/base.rsw'), b'art')
        with self.assertRaises(ValueError):
            source.resolve('data/a.rsw')
