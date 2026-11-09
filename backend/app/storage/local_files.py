"""Local-disk implementation of the FileStore interface: <root>/<namespace>/<key>/<name>."""
import os
import shutil
from pathlib import Path


class LocalFiles:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def _dir(self, ns: str, key: str) -> Path:
        return self.root / ns / key

    def create(self, ns: str, key: str) -> Path:
        d = self._dir(ns, key)
        d.mkdir(parents=True)
        return d

    def exists(self, ns: str, key: str) -> bool:
        return self._dir(ns, key).is_dir()

    def workdir(self, ns: str, key: str) -> Path:
        d = self._dir(ns, key)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def path(self, ns: str, key: str, name: str) -> Path:
        return self._dir(ns, key) / name

    def has(self, ns: str, key: str, name: str) -> bool:
        return self.path(ns, key, name).exists()

    def names(self, ns: str, key: str) -> list[str]:
        d = self._dir(ns, key)
        return sorted(p.name for p in d.iterdir()) if d.is_dir() else []

    def write_bytes(self, ns: str, key: str, name: str, data: bytes) -> None:
        self.path(ns, key, name).write_bytes(data)

    def write_text(self, ns: str, key: str, name: str, text: str) -> None:
        self.path(ns, key, name).write_text(text, encoding="utf-8")

    def read_bytes(self, ns: str, key: str, name: str) -> bytes:
        return self.path(ns, key, name).read_bytes()

    def read_text(self, ns: str, key: str, name: str) -> str:
        return self.path(ns, key, name).read_text(encoding="utf-8")

    def publish(self, ns: str, key: str) -> None:
        """Nothing to upload: the files are already where they are kept."""

    def remove(self, ns: str, key: str) -> int:
        freed = self.size(ns, key)
        shutil.rmtree(self._dir(ns, key), ignore_errors=True)
        return freed

    def remove_subdir(self, ns: str, key: str, name: str) -> None:
        shutil.rmtree(self.path(ns, key, name), ignore_errors=True)

    def size(self, ns: str, key: str) -> int:
        return _tree_bytes(self._dir(ns, key))

    def total_bytes(self, ns: str) -> int:
        return _tree_bytes(self.root / ns)

    def free_bytes(self) -> int:
        return shutil.disk_usage(self.root).free

    def total_free(self) -> tuple[int, int]:
        u = shutil.disk_usage(self.root)
        return u.total, u.free


def _tree_bytes(path: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(path):
        for f in names:
            try:
                total += (Path(root) / f).stat().st_size
            except OSError:
                pass
    return total
