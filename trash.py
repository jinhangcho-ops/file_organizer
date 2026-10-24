"""휴지통으로 보내기. 프로그램에서 사용자가 지우는 것(파일·폴더·보관 뒤 원본)은 모두 여기를 거친다. 영구 삭제는 하지 않는다.
send2trash는 휴지통이 없거나 꺼져 있는 곳, 휴지통보다 큰 파일을 경고 없이 영구 삭제할 수 있어서(Windows에 '확인 없이'로 부탁한다),
보내기 전에 ① 고정 디스크인지 ② 그 디스크의 휴지통이 켜져 있는지 ③ 휴지통 최대 크기보다 작은지 확인하고, 아니면 보내지 않는다."""
import ctypes
import os
import shutil
import winreg

from send2trash import send2trash

import drives

FIXED = 3  # GetDriveTypeW: 고정 디스크
BITBUCKET = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
send = send2trash  # 검사는 이것을 바꿔 끼워 실제 휴지통에 넣지 않는다.


def volume_id(path):
    """경로가 있는 디스크의 볼륨 번호({GUID}). 모르면 None."""
    buffer = ctypes.create_unicode_buffer(64)
    if not ctypes.windll.kernel32.GetVolumeNameForVolumeMountPointW(drives.volume_root(path), buffer, 64):
        return None
    value = buffer.value  # \\?\Volume{...}\
    return value[value.find("{"):value.find("}") + 1] or None


def recycle_settings(path):
    """그 디스크 휴지통의 (꺼짐 여부, 최대 크기 바이트 또는 None). 설정이 없으면 Windows 기본(켜짐, 크기 모름)."""
    volume = volume_id(path)
    if not volume:
        return False, None
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BITBUCKET + "\\" + volume) as key:
            values = {}
            for name in ("NukeOnDelete", "MaxCapacity"):
                try:
                    values[name] = winreg.QueryValueEx(key, name)[0]
                except OSError:
                    pass
    except OSError:
        return False, None
    capacity = values.get("MaxCapacity")
    return bool(values.get("NukeOnDelete")), capacity * 1024 * 1024 if capacity else None


def policy_off():
    """'휴지통으로 옮기지 않기' 정책(NoRecycleFiles)이 켜져 있는지. 켜져 있으면 send2trash가 영구 삭제한다."""
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer") as key:
                if winreg.QueryValueEx(key, "NoRecycleFiles")[0]:
                    return True
        except OSError:
            continue
    return False


MAX_PATH = 260  # 이 길이 이상인 경로는 휴지통에 넣지 못하고, send2trash(옛 방식)는 묻지 않고 그대로 지운다(260자 넘는 파일로 확인).


def size_of(path):
    """휴지통에 들어갈 (크기, 가장 긴 경로의 글자 수). 링크·정션은 따라가지 않고(링크만 간다), 읽을 수 없는 파일(깨진 링크 등)은 건너뛴다."""
    link = lambda p: os.path.islink(p) or os.path.isjunction(p)  # noqa: E731
    path = os.path.abspath(path)
    if link(path) or not os.path.isdir(path):
        return os.lstat(path).st_size, len(path)
    total, longest = 0, len(path)
    for folder, dirs, names in os.walk(path):
        longest = max([longest, *(len(os.path.join(folder, n)) for n in dirs + names)])
        dirs[:] = [d for d in dirs if not link(os.path.join(folder, d))]
        for name in names:
            try:
                total += os.lstat(os.path.join(folder, name)).st_size
            except OSError:
                pass
    return total, longest


def problem(path):
    """휴지통으로 보낼 수 없는 까닭(없으면 None). 영구 삭제될 수 있는 경우를 모두 막는다."""
    if not os.path.lexists(path):
        return "없는 파일입니다"
    place = path  # 볼륨은 든 폴더로 묻는다(drives.volume_root): 링크·정션도 링크가 있는 곳의 디스크로 본다
    if drives.drive_type(place) != FIXED:
        return "휴지통이 없는 곳(네트워크·이동식 디스크)입니다"
    if drives.is_subst(place) or not volume_id(place):  # subst로 만든 드라이브 등: 휴지통이 없어 그대로 지워질 수 있다.
        return "휴지통을 확인할 수 없는 디스크입니다"
    if policy_off():
        return "휴지통을 쓰지 않도록 정책이 설정되어 있습니다"
    off, capacity = recycle_settings(place)
    if off:
        return "이 디스크는 휴지통을 쓰지 않도록 설정되어 있습니다"
    if capacity is None:  # 설정 값이 없는 디스크(새로 단 디스크 등)는 Windows 기본값보다 작게 디스크 크기의 5%로 본다.
        capacity = shutil.disk_usage(drives.volume_root(place)).total // 20  # 크기를 모른 채 보내면 '확인 없이'라 큰 것은 영구 삭제될 수 있다.
    size, longest = size_of(path)
    if longest >= MAX_PATH:
        return "경로가 너무 길어(260자 이상) 휴지통에 넣을 수 없습니다"
    if size > capacity:
        return "휴지통 최대 크기보다 큽니다"
    return None


def to_trash(paths):
    """paths를 휴지통으로 보낸다. 보낼 수 없는 것은 보내지 않고 (보낸 것, [(경로, 까닭)])으로 돌려준다."""
    sent, refused = [], []
    for path in paths:
        reason = problem(path)
        if reason:
            refused.append((path, reason))
            continue
        try:
            send(path)
        except OSError as e:
            refused.append((path, e.strerror or str(e)))
            continue
        if os.path.lexists(path):
            refused.append((path, "휴지통으로 보내지 못했습니다"))
        else:
            sent.append(path)
    return sent, refused
