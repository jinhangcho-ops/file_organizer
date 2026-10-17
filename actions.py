"""상세 패널의 동작: 다른 폴더로 옮기기, 이름 바꾸기, 휴지통으로 보내기. 셋 다 덮어쓰지 않고, 끝나면 색인에 반영한다."""
import ctypes
import os
from ctypes import wintypes

import exclude
import index
import organize

BAD = set('\\/:*?"<>|')  # 파일 이름에 쓸 수 없는 글자
FO_DELETE = 3
FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_WANTNUKEWARNING = 0x4, 0x10, 0x40, 0x4000
shell_delete = ctypes.windll.shell32.SHFileOperationW  # 검사는 이것을 바꿔 끼워 실제 휴지통에 넣지 않는다.


class FileOp(ctypes.Structure):  # SHFILEOPSTRUCTW
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR), ("pTo", wintypes.LPCWSTR),
                ("fFlags", wintypes.WORD), ("fAnyOperationsAborted", wintypes.BOOL), ("hNameMappings", wintypes.LPVOID),
                ("lpszProgressTitle", wintypes.LPCWSTR)]


def move_to(path, folder):
    """파일을 folder로 옮긴다. 같은 이름이 있으면 거부한다. 옮긴 경로를 돌려준다."""
    if not os.path.isdir(folder):
        raise ValueError(f"없는 폴더입니다: {folder}")
    target = os.path.join(folder, os.path.basename(path))
    if exclude.key(target) == exclude.key(path):
        raise ValueError("이미 그 폴더에 있습니다.")
    if os.path.lexists(target):
        raise ValueError(f"그 폴더에 같은 이름의 파일이 있습니다: {os.path.basename(path)}")
    organize.move(path, target)
    index.moved([(path, target)])
    return target


def rename(path, name):
    """같은 폴더 안에서 이름을 바꾼다. 같은 이름이 있으면 거부한다(대소문자만 바꾸는 것은 된다). 바뀐 경로를 돌려준다."""
    name = (name or "").strip()
    if not name or name in (".", "..") or BAD & set(name) or name.endswith("."):
        raise ValueError('쓸 수 없는 이름입니다. \\ / : * ? " < > | 는 쓸 수 없습니다.')
    target = os.path.join(os.path.dirname(path), name)
    if target == path:
        return path
    if os.path.lexists(target) and exclude.key(target) != exclude.key(path):
        raise ValueError(f"같은 이름의 파일이 있습니다: {name}")
    os.rename(path, target)
    index.moved([(path, target)])
    return target


def trash(path):
    """Windows 휴지통으로 보낸다. 휴지통이 없는 곳(네트워크 등)이라 영구 삭제가 될 때는 Windows가 경고하고 취소할 수 있다."""
    operation = FileOp(wFunc=FO_DELETE, pFrom=path + "\0", fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_WANTNUKEWARNING)
    code = shell_delete(ctypes.byref(operation))
    if code or operation.fAnyOperationsAborted or os.path.lexists(path):
        raise ValueError("휴지통으로 보내지 못했습니다." + (f" (오류 {code})" if code else ""))
    index.removed(path)
