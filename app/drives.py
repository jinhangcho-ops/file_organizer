"""디스크 목록과 Windows의 알려진 폴더(다운로드·문서·바탕 화면) 위치, 연 시각 기록이 켜져 있는지."""
import ctypes
import os
import string
import time
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


ROOTS = {}  # 폴더 → (볼륨 맨 위, 물은 시각)
ROOTS_FRESH = 10


def volume_root(path):
    """경로가 든 폴더가 있는 볼륨의 맨 위(C:\\, 폴더에 탑재한 디스크면 그 폴더\\). 모르면 드라이브 맨 위.
    파일·폴더 자신이 아니라 든 폴더로 묻는다(옮기거나 지울 수 있는지는 든 곳의 디스크로 정해진다). ROOTS_FRESH초 기억한다."""
    path = os.path.abspath(path)
    folder = os.path.dirname(path) or path
    found = ROOTS.get(folder)
    if found and time.time() - found[1] < ROOTS_FRESH:
        return found[0]
    buffer = ctypes.create_unicode_buffer(1024)
    root = buffer.value if ctypes.windll.kernel32.GetVolumePathNameW(folder, buffer, 1024) else os.path.splitdrive(path)[0] + "\\"
    if len(ROOTS) > 4096:
        ROOTS.clear()
    ROOTS[folder] = (root, time.time())
    return root


def profile_folders():
    """이 PC의 사용자 폴더들(레지스트리 ProfileList). 읽지 못하면 빈 목록."""
    base = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList"
    found = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, base) as key:
            for i in range(winreg.QueryInfoKey(key)[0]):
                try:
                    with winreg.OpenKey(key, winreg.EnumKey(key, i)) as sub:
                        found.append(os.path.expandvars(winreg.QueryValueEx(sub, "ProfileImagePath")[0]))
                except OSError:
                    continue
    except OSError:
        pass
    return found


def is_subst(path):
    """subst로 만든 드라이브인지(QueryDosDevice가 '\\??\\실제 경로'를 돌려준다). 그런 드라이브에는 휴지통이 없다."""
    drive = os.path.splitdrive(os.path.abspath(path))[0]
    if len(drive) != 2:  # \\server\share 등
        return False
    return dos_device(drive).startswith("\\??\\")


def dos_device(drive):
    """드라이브 글자가 가리키는 장치 이름(QueryDosDevice). 모르면 ''."""
    buffer = ctypes.create_unicode_buffer(1024)
    return buffer.value if ctypes.windll.kernel32.QueryDosDeviceW(drive, buffer, 1024) else ""


def drive_type(path):
    """경로가 있는 디스크의 종류 번호(GetDriveTypeW: 2 이동식, 3 고정, 4 네트워크 …)."""
    return ctypes.windll.kernel32.GetDriveTypeW(volume_root(path))


def same_drive(a, b):
    """두 경로가 같은 디스크에 있는지. 같으면 이름 바꾸기로 옮길 수 있다."""
    return volume_root(a).lower() == volume_root(b).lower()

