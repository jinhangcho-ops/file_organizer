"""차단 폴더: 색인하지 않고 목록·검색·정리·옮기기·휴지통·보관 어디서도 다루지 않는 폴더(시스템·게임 폴더 등).
목록은 %ProgramData%\\file_organizer\\blacklist.json에 두고 관리자 권한으로 실행했을 때만 바꾼다.
저장할 때 그 폴더를 관리자·SYSTEM만 쓸 수 있게(사용자는 읽기만) 맞춰, 일반 권한으로는 파일을 손으로 고칠 수도 없다."""
import ctypes
import json
import os
import subprocess
from pathlib import Path

import exclude
import fence
import index

FILE = "blacklist.json"
ADMINS, SYSTEM, USERS = "*S-1-5-32-544", "*S-1-5-18", "*S-1-5-32-545"
# 후보로 보여 줄 게임 폴더 이름(소문자). 드라이브 바로 아래와 그 한 단계 아래에서 찾는다.
GAMES = {"steamlibrary", "steam", "steamapps", "epic games", "riot games", "xboxgames", "battle.net", "gog games", "ubisoft",
         "ubisoft game launcher", "ea games", "origin games", "blizzard"}


def folder():
    """차단 목록을 두는 폴더. 테스트는 PROGRAMDATA를 임시 폴더로 바꾼다."""
    return Path(os.environ.get("PROGRAMDATA") or r"C:\ProgramData", "file_organizer")


def load(name=FILE):
    """관리자 목록 하나(차단은 blacklist.json, 구조 보호는 protect.py의 protected.json)."""
    try:
        paths = json.loads(Path(folder(), name).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return []
    return [path for path in paths if isinstance(path, str)]


def keys():
    """차단 폴더의 비교용 key들(exclude.key)."""
    return {exclude.key(path) for path in load()}


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def restrict(path):
    """폴더의 주인을 Administrators로, 권한을 관리자·SYSTEM 모두 / 사용자 읽기로 맞춘다(위에서 물려받는 권한은 끊는다)."""
    for args in (["/setowner", ADMINS, "/T"], ["/inheritance:r", "/grant:r", f"{ADMINS}:(OI)(CI)F", f"{SYSTEM}:(OI)(CI)F", f"{USERS}:(OI)(CI)RX"]):
        result = subprocess.run(["icacls", str(path), *args], capture_output=True, text=True, encoding="mbcs", creationflags=0x08000000)
        if result.returncode:
            raise OSError(f"차단 목록 폴더의 권한을 맞추지 못했습니다: {(result.stdout or result.stderr).strip()}")


def save(paths, name=FILE):
    if not is_admin():
        raise ValueError("차단 폴더·구조 보호 폴더는 관리자 권한으로 실행했을 때만 바꿀 수 있습니다.")
    folder().mkdir(parents=True, exist_ok=True)
    restrict(folder())
    path = Path(folder(), name)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(paths, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)
    return paths


def add(path, others):
    """폴더를 차단 목록에 넣는다. others: 겹치면 안 되는 잠금 폴더 key들."""
    path = os.path.realpath(path)  # 정션 경로를 넣으면 가리키는 진짜 폴더가 막히지 않는다
    if not os.path.isdir(path):
        raise ValueError(f"없는 폴더입니다: {path}")
    if os.path.dirname(path) == path:
        raise ValueError(f"드라이브 전체는 차단하지 않습니다. 색인할 폴더에서 빼세요: {path}")
    paths = load()
    if fence.inside(path, {exclude.key(p) for p in paths}):
        raise ValueError(f"이미 차단한 폴더(또는 그 아래)입니다: {path}")
    if fence.overlaps(path, others):
        raise ValueError(f"잠금 폴더와 겹칩니다. 잠금을 먼저 푸세요: {path}")
    return save([p for p in paths if not fence.inside(p, {exclude.key(path)})] + [path])  # 그 아래에 있던 차단 폴더는 합친다.


def remove(path):
    return save([p for p in load() if exclude.key(p) != exclude.key(path)])


def candidates(roots):
    """색인하는 드라이브에서 찾은 게임 폴더 중 아직 차단하지 않은 것들(경로 순)."""
    blocked = keys()
    found = []
    for drive in drive_roots(roots):
        for depth_one in subfolders(drive):
            if os.path.basename(depth_one).lower() in GAMES:
                found.append(depth_one)
                continue
            found += [p for p in subfolders(depth_one) if os.path.basename(p).lower() in GAMES]
    return sorted(p for p in found if not fence.inside(p, blocked))


def library(path):
    """게임 묶음 폴더(GAMES 이름)인지: candidates처럼 드라이브 바로 아래와 그 한 단계 아래만 본다.
    더 깊은 곳의 같은 이름(문서 안의 steam, 사진 안의 Blizzard 등)은 보통 폴더다(안을 색인하고 옮길 수 있게)."""
    if os.path.basename(path).lower() not in GAMES:
        return False
    tops = {exclude.key(d) for d in drive_roots([path])}
    parent = os.path.dirname(path)
    return exclude.key(parent) in tops or exclude.key(os.path.dirname(parent)) in tops


def drive_roots(roots):
    """게임 폴더를 찾기 시작할 곳: 색인하는 폴더들의 드라이브(C:\\ 등). 테스트는 임시 폴더로 바꾼다."""
    return sorted({os.path.splitdrive(os.path.abspath(root))[0] + "\\" for root in roots})


def subfolders(path):
    """바로 아래의 진짜 폴더들(링크·정션·숨김·시스템 폴더는 뺀다). 읽을 수 없으면 빈 목록."""
    try:
        with os.scandir(path) as entries:
            return [e.path for e in entries if e.is_dir(follow_symlinks=False) and not e.is_junction()
                    and not index.hidden(e.stat(follow_symlinks=False))]
    except OSError:
        return []
