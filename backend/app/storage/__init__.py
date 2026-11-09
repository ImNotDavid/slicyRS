"""Storage layer (A.9). The rest of the app uses `db` and `files` only. To add a backend, write a class that satisfies the
interfaces, register a factory below, and select it with DB_BACKEND / FILE_BACKEND."""
import os
from pathlib import Path
from typing import Callable

from ..config import DATA_DIR
from .interfaces import Collection, Database, FileStore, TaskStore  # noqa: F401
from .local_files import LocalFiles
from .memory_db import MemoryDatabase
from .sqlite_db import SqliteDatabase

DB_BACKENDS: dict[str, Callable[[Path], Database]] = {"sqlite": lambda root: SqliteDatabase(root / "slicyrs.db"),
                                                    "memory": lambda root: MemoryDatabase()}   # tests and throwaway runs
FILE_BACKENDS: dict[str, Callable[[Path], FileStore]] = {"local": LocalFiles}


def _pick(registry: dict, env: str, default: str):
    name = os.environ.get(env, default)
    if name not in registry:
        raise RuntimeError(f"Unknown {env}={name!r}; available: {', '.join(sorted(registry))}")
    return registry[name]


db: Database = _pick(DB_BACKENDS, "DB_BACKEND", "sqlite")(DATA_DIR)
files: FileStore = _pick(FILE_BACKENDS, "FILE_BACKEND", "local")(DATA_DIR)
JOBS, MODELS = "jobs", "models"     # file namespaces
