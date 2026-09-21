import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from ragx.incremental import file_digest, unlink_file, write_bytes


class PublicationTests(unittest.TestCase):
    def test_transient_windows_sharing_error_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared.bin'
            error = PermissionError('busy')
            error.winerror = 32
            import os
            replace = os.replace
            calls = []

            def busy_once(source, target):
                calls.append(target)
                if len(calls) == 1:
                    raise error
                replace(source, target)

            with patch('ragx.incremental.os.replace', side_effect=busy_once), \
                 patch('ragx.incremental.time.sleep'):
                write_bytes(path, b'complete')
            self.assertEqual(path.read_bytes(), b'complete')
            self.assertEqual(len(calls), 2)

    def test_persistent_error_still_fails_and_cleans_temporary(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared.bin'
            path.write_bytes(b'original')
            error = PermissionError('busy')
            error.winerror = 5
            with patch('ragx.incremental.os.replace', side_effect=error) as replace, \
                 patch('ragx.incremental.time.sleep'):
                with self.assertRaises(PermissionError):
                    write_bytes(path, b'new')
                self.assertEqual(replace.call_count, 8)
            self.assertEqual(path.read_bytes(), b'original')
            self.assertEqual(list(Path(directory).glob('*.tmp')), [])

    def test_transient_shared_read_retries(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared.bin'
            path.write_bytes(b'complete')
            error = PermissionError('busy')
            error.winerror = 32
            original = Path.open
            calls = []

            def busy_once(subject, *args, **kwargs):
                calls.append(subject)
                if len(calls) == 1:
                    raise error
                return original(subject, *args, **kwargs)

            with patch.object(Path, 'open', busy_once), patch('ragx.incremental.time.sleep'):
                self.assertEqual(file_digest(path), file_digest(path))
            self.assertEqual(len(calls), 3)

    def test_transient_cache_unlink_retries(self):
        path = Path('cache.json')
        error = PermissionError('busy')
        error.winerror = 5
        calls = []

        def busy_once(subject, *, missing_ok):
            self.assertEqual(subject, path)
            calls.append(missing_ok)
            if len(calls) == 1:
                raise error

        with patch.object(Path, 'unlink', busy_once), \
                patch('ragx.incremental.time.sleep'):
            unlink_file(path)
        self.assertEqual(calls, [True, True])

    def test_busy_shared_target_is_published_without_a_pre_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'shared.bin'
            path.write_bytes(b'old')
            error = PermissionError('continuously replaced')
            error.winerror = 32
            with patch('ragx.incremental.file_digest', side_effect=error):
                write_bytes(path, b'new')
            self.assertEqual(path.read_bytes(), b'new')
