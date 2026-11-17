from contextlib import contextmanager
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import os
from pathlib import Path
import re
import subprocess
from tempfile import NamedTemporaryFile, mkdtemp


CAMPAIGN_FILE = re.compile(r'SG8_(\d+)\.bytes', re.IGNORECASE)


def _write(file, data: bytes) -> None:
    file.write(data)
    file.flush()
    os.fsync(file.fileno())


def _temporary_copy(directory: Path, data: bytes) -> Path:
    with NamedTemporaryFile(dir=directory, suffix='.tmp', delete=False) as output:
        path = Path(output.name)
        try:
            _write(output, data)
        except OSError:
            output.close()
            path.unlink(missing_ok=True)
            raise
    return path


@contextmanager
def _save_lock(path: Path):
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    create = kernel.CreateMutexW
    create.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    create.restype = wintypes.HANDLE
    kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    digest = sha256(os.path.normcase(str(path)).encode('utf-8')).hexdigest()
    handle = create(None, True, f'Local\\SG8Editor-{digest}')
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    try:
        if error == 183:  # ERROR_ALREADY_EXISTS
            raise ValueError('另一个修改器正在保存此存档。')
        try:
            yield
        finally:
            kernel.ReleaseMutex(handle)
    finally:
        kernel.CloseHandle(handle)


def game_is_running() -> bool:
    shell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'WindowsPowerShell' / 'v1.0' / 'powershell.exe'
    result = subprocess.check_output(
        [shell, '-NoProfile', '-NonInteractive', '-Command',
         'if (Get-Process -Name SG8 -ErrorAction SilentlyContinue) { Write-Output running }'],
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=8)
    return result.strip() == b'running'


@dataclass(frozen=True)
class SaveSnapshot:
    path: Path
    data: bytes
    metadata_path: Path
    metadata: bytes | None

    @classmethod
    def open(cls, path: Path) -> 'SaveSnapshot':
        path = path.resolve(strict=True)
        match = CAMPAIGN_FILE.fullmatch(path.name)
        if match is None:
            raise ValueError('请选择 SG8_数字.bytes 战役存档，例如 SG8_0.bytes。')
        metadata_path = path.with_name(f'{path.stem}t{path.suffix}')
        try:
            metadata = metadata_path.read_bytes()
        except FileNotFoundError:
            metadata = None
        return cls(path, path.read_bytes(), metadata_path, metadata)

    def export(self, parent: Path, edited: bytes) -> Path:
        destination = Path(mkdtemp(prefix=f'{self.path.stem}_修改版_', dir=parent))
        files = {destination / self.path.name: edited}
        if self.metadata is not None:
            files[destination / self.metadata_path.name] = self.metadata
        try:
            for path, data in files.items():
                with path.open('xb') as output:
                    _write(output, data)
        except OSError:
            for path in files:
                path.unlink(missing_ok=True)
            destination.rmdir()
            raise
        return destination

    def replace(self, edited: bytes) -> Path:
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
        backup = self.path.with_name(f'{self.path.name}.{timestamp}.bak')
        with _save_lock(self.path):
            if self.path.read_bytes() != self.data:
                raise ValueError('存档在打开后已变更。请重新打开存档，再选一次武将。')
            temporary = _temporary_copy(self.path.parent, edited)
            try:
                backup_copy = _temporary_copy(self.path.parent, self.data)
                try:
                    os.replace(backup_copy, backup)
                finally:
                    backup_copy.unlink(missing_ok=True)
                if self.path.read_bytes() != self.data:
                    raise ValueError('存档在保存时已变更。请重新打开存档。')
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
        return backup


def find_save_directory() -> Path:
    root = Path.home() / 'AppData' / 'LocalLow' / 'UserJoy' / 'SG8' / 'Save'
    try:
        candidates = [path for path in root.glob('*/*.bytes') if CAMPAIGN_FILE.fullmatch(path.name)]
        if candidates:
            return max(candidates, key=lambda path: path.stat().st_mtime).parent
        if root.is_dir():
            return root
    except OSError:
        pass
    return Path.home()
