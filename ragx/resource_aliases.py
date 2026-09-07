"""Client resource-name aliases, including server map names sharing source art."""
from .grf import normalize_path
from .incremental import digest


class ResourceAliases:
    def __init__(self, archive):
        self.archive = archive
        self.entries = set(archive.namelist())
        self.aliases = {}
        try:
            table = archive.read('data\\resnametable.txt')
        except (KeyError, FileNotFoundError):
            table = b''
        self.table_hash = digest(table)
        for line in table.decode('cp949', errors='replace').splitlines():
            fields = line.split('//', 1)[0].split('#')
            if len(fields) >= 3:
                src, dst = (normalize_path('data\\' + value.strip()) for value in fields[:2])
                if fields[0].strip() and fields[1].strip():
                    self.aliases[src] = dst

    def resolve(self, path):
        path = normalize_path(path)
        seen = set()
        while path not in self.entries and path in self.aliases:
            if path in seen:
                raise ValueError('resource alias cycle: ' + path)
            seen.add(path)
            path = self.aliases[path]
        return path

    def read(self, path):
        return self.archive.read(self.resolve(path))

    def fingerprint(self, path):
        resolved = self.resolve(path)
        if resolved not in self.entries:
            return None
        value = self.archive.fingerprint(resolved)
        return digest((self.table_hash + str(value)).encode())

    def namelist(self):
        names = set(self.entries)
        for key in self.aliases:
            try:
                if self.resolve(key) in self.entries:
                    names.add(key)
            except ValueError:
                # Unused broken aliases must not prevent archive discovery.
                # Reading a requested cyclic alias still fails explicitly.
                continue
        return sorted(names)

    def close(self):
        self.archive.close()
