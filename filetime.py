"""파일 시각과 다운로드 표시를 다루는 Windows API(ctypes).
- 다운로드 표시: 브라우저가 받은 파일에 붙이는 Zone.Identifier 스트림.
- 연 시각 되돌리기: 색인이 내용을 읽어도 '오래 안 본 파일'이 '방금 본 파일'이 되지 않게 한다.
- 복사: 다른 디스크로 옮길 때 다운로드 표시와 만든·연·고친 시각을 그대로 가져간다(파이썬 기본 복사는 표시를 잃는다)."""
import ctypes
import os
from ctypes import wintypes

EPOCH = 116444736000000000  # 1601-01-01부터 1970-01-01까지(100나노초 단위). FILETIME의 기준이 1601년이다.
WRITE_ATTRIBUTES = 0x100
SHARE_ALL = 7  # 읽기·쓰기·지우기 공유
OPEN_EXISTING = 3
BACKUP_SEMANTICS = 0x02000000  # 폴더도 열 수 있게 한다.
INVALID = wintypes.HANDLE(-1).value

kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.CreateFileW.restype = wintypes.HANDLE
kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
kernel.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CopyFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.BOOL]


def downloaded(path):
    """인터넷에서 받은 파일인지(Zone.Identifier 스트림이 있는지)."""
    try:
        with open(path + ":Zone.Identifier", "rb") as stream:
            return b"ZoneId" in stream.read(1024)
    except OSError:
        return False


def filetime(ns):
    """1970년 기준 나노초 → FILETIME(64비트 정수). None이면 바꾸지 않는다는 뜻으로 NULL을 넘긴다."""
    return None if ns is None else ctypes.byref(ctypes.c_ulonglong(ns // 100 + EPOCH))


def set_times(path, created=None, accessed=None, modified=None):
    """만든·연·고친 시각(1970년 기준 나노초)을 바꾼다. None인 것은 그대로 둔다."""
    handle = kernel.CreateFileW(path, WRITE_ATTRIBUTES, SHARE_ALL, None, OPEN_EXISTING, BACKUP_SEMANTICS, None)
    if handle == INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.SetFileTime(handle, filetime(created), filetime(accessed), filetime(modified)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def restore_access(path, stat):
    """파일을 읽은 뒤 연 시각을 읽기 전(stat)으로 되돌린다. 읽기 전용 파일 등 되돌릴 수 없으면 그냥 둔다."""
    try:
        set_times(path, accessed=stat.st_atime_ns)
    except OSError:
        pass


def copy(source, target):
    """source를 target으로 복사한다(target이 있으면 실패). 다운로드 표시 같은 추가 스트림과 만든·연·고친 시각을 그대로 가져간다."""
    stat = os.stat(source)
    if not kernel.CopyFileW(source, target, True):
        raise ctypes.WinError(ctypes.get_last_error())
    set_times(target, stat.st_birthtime_ns, stat.st_atime_ns, stat.st_mtime_ns)
