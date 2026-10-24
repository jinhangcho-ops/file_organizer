"""디스크 목록과 Windows의 알려진 폴더(다운로드·문서·바탕 화면) 위치, 연 시각 기록이 켜져 있는지."""
import ctypes
import os
import string
import uuid
import winreg
from pathlib import Path

TYPES = {2: "이동식", 3: "고정", 4: "네트워크", 5: "CD"}  # GetDriveTypeW의 값
KNOWN = {  # 이름 → Windows 알려진 폴더 번호(KNOWNFOLDERID)
    "다운로드": "374DE290-123F-4565-9164-39C4925E467B",
    "문서": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "바탕 화면": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
}


def list_drives():
    """연결된 디스크. [{"path": "C:\\", "type": "고정"}, …]"""
    kernel = ctypes.windll.kernel32
    mask = kernel.GetLogicalDrives()
    found = []
    for i, letter in enumerate(string.ascii_uppercase):
        if mask >> i & 1:
            path = f"{letter}:\\"
            kind = kernel.GetDriveTypeW(path)
            if kind in TYPES:
                found.append({"path": path, "type": TYPES[kind]})
    return found


def known_folder(guid):
    """SHGetKnownFolderPath로 폴더 위치를 묻는다. 사용자가 다운로드 폴더를 옮겼어도 실제 위치가 나온다."""
    key = (ctypes.c_byte * 16).from_buffer_copy(uuid.UUID(guid).bytes_le)
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(key), 0, None, ctypes.byref(out)) != 0:
        return None
    try:
        return out.value
    finally:
        ctypes.windll.ole32.CoTaskMemFree(out)


def known_folders():
    home = Path.home()
    fallback = {"다운로드": home / "Downloads", "문서": home / "Documents", "바탕 화면": home / "Desktop"}
    return {name: known_folder(guid) or str(fallback[name]) for name, guid in KNOWN.items()}


def last_access_on():
    """NTFS가 연 시각을 기록하는지. NtfsDisableLastAccessUpdate의 맨 아래 비트가 1이면 꺼져 있다(fsutil behavior query disablelastaccess와 같다)."""
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as key:
            value, _ = winreg.QueryValueEx(key, "NtfsDisableLastAccessUpdate")
    except OSError:
        return True  # 값이 없으면 Windows 기본(기록함)
    return not value & 1


def volume_root(path):
    """경로가 있는 볼륨의 맨 위(C:\\, 폴더에 탑재한 디스크면 그 폴더\\). 모르면 드라이브 맨 위."""
    buffer = ctypes.create_unicode_buffer(1024)
    if ctypes.windll.kernel32.GetVolumePathNameW(os.path.abspath(path), buffer, 1024):
        return buffer.value
    return os.path.splitdrive(os.path.abspath(path))[0] + "\\"


def drive_type(path):
    """경로가 있는 디스크의 종류 번호(GetDriveTypeW: 2 이동식, 3 고정, 4 네트워크 …)."""
    return ctypes.windll.kernel32.GetDriveTypeW(volume_root(path))


def same_drive(a, b):
    """두 경로가 같은 디스크에 있는지. 같으면 이름 바꾸기로 옮길 수 있다."""
    return Path(a).anchor.lower() == Path(b).anchor.lower()

