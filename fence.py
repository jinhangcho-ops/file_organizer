"""색인과 작업에서 빼는 폴더: 차단 폴더(blacklist)와 잠금 폴더·금고(lock). 그 폴더와 그 아래 전부를 다루지 않는다."""
import os
import tempfile

import blacklist
import exclude
import games
import index
import lock
import protect


def keys():
    """비교용 key들(exclude.key)."""
    return {exclude.key(path) for path in blacklist.load() + lock.folders()}


def inside(path, fences):
    """path가 fences(key들) 중 하나이거나 그 아래인지."""
    key = exclude.key(path)
    return any(key == fence or key.startswith(os.path.join(fence, "")) for fence in fences)


def holds(path, fences):
    """path가 fences 중 하나를 품는지(그 위 폴더인지)."""
    return any(inside(fence, {exclude.key(path)}) for fence in fences)


def overlaps(path, fences):
    """path가 fences 중 하나와 같거나, 그 안에 있거나, 그것을 품는지(새로 차단·잠금할 때 겹치지 않게)."""
    return inside(path, fences) or holds(path, fences)


def refuse(path, fences=None):
    """차단·잠금 폴더(또는 그 아래)면 거부한다. fences: 미리 만든 keys()(여러 개를 볼 때)."""
    if inside(path, keys() if fences is None else fences):
        raise ValueError(f"차단하거나 잠근 폴더라 다루지 않습니다: {path}")
    return path


SYSTEM_VARS = ("WINDIR", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "PROGRAMDATA", "APPDATA", "LOCALAPPDATA")


def system_folder(path):
    """Windows·프로그램 폴더(그 안 포함)인지: 드라이브 바로 아래의 index.SKIP 이름(Windows, Program Files, $Recycle.Bin …)과
    환경 변수가 가리키는 실제 위치(Windows, Program Files, ProgramData, AppData). 임시 폴더(%TEMP%)는 AppData 안이지만 뺀다."""
    parts = exclude.key(path).split(os.sep)
    if len(parts) > 1 and parts[1] in index.SKIP:
        return True
    return inside(path, system_places()) and not inside(path, {exclude.key(tempfile.gettempdir())})


def system_places():
    """환경 변수가 가리키는 Windows·프로그램 폴더의 key들."""
    return {exclude.key(os.environ[name]) for name in SYSTEM_VARS if os.environ.get(name)}


def refuse_system(path, game_keys=None):
    """Windows·프로그램 폴더(system_folder)나 게임 폴더(games.keys)와 그 안이면 거부한다.
    옮기기·이름 바꾸기·휴지통·정리처럼 사용자가 고른 것을 바꾸는 일 앞에서 부른다. game_keys: 여러 개를 볼 때 미리 만든 games.keys()."""
    if system_folder(path):
        raise ValueError(f"Windows·프로그램 폴더라 다루지 않습니다: {path}")
    if inside(path, games.keys() if game_keys is None else game_keys):
        raise ValueError(f"게임 폴더라 다루지 않습니다: {path}")
    return path


def holding_lists():
    """refuse_holding이 보는 목록들. 여러 개를 볼 때 한 번 만들어 넘긴다(잠금 목록은 읽을 때마다 비밀 파일을 푼다)."""
    return {"blocked": blacklist.keys(), "system": system_places(), "games": games.keys(), "kept": protect.keys(),
            "locks": {exclude.key(p) for p in lock.folders()}}


def refuse_holding(path, locks_ok, moving=False, lists=None):
    """옮기기(moving)·이름 바꾸기·휴지통 전에: 차단 폴더를 품은 폴더는 늘 거부한다(차단 목록은 관리자만 고칠 수 있어 위치를 따라 고칠 수 없다).
    구조 보호 폴더는 그 자신과 그것을 품은 폴더를 거부하고, 이름 바꾸기·휴지통이면 그 안의 것도 거부한다(안의 것을 밖으로 옮기기는 된다).
    잠금 폴더·금고를 품은 폴더는 locks_ok(같은 디스크 안 옮기기·이름 바꾸기: lock.moved가 위치를 고친다)일 때만 받는다.
    다른 디스크로 옮기기는 복사가 잠긴 내용을 읽지 못하고, 휴지통은 비우면 잠긴 내용까지 사라지기 때문이다.
    Windows·프로그램 폴더(AppData 등)를 품은 폴더(사용자 폴더 등)도 늘 거부한다. lists: 미리 만든 holding_lists()."""
    lists = lists or holding_lists()
    if holds(path, lists["blocked"]):
        raise ValueError(f"차단 폴더가 들어 있어 옮기거나 지울 수 없습니다: {path}")
    if holds(path, lists["system"]):
        raise ValueError(f"Windows·프로그램 폴더가 들어 있어 옮기거나 지울 수 없습니다: {path}")
    if holds(path, lists["games"]):
        raise ValueError(f"게임 폴더가 들어 있어 옮기거나 지울 수 없습니다: {path}")
    kept = lists["kept"]
    if holds(path, kept) or (exclude.key(path) in kept if moving else inside(path, kept)):
        raise ValueError(f"구조 보호 폴더라 {'옮길' if moving else '이름을 바꾸거나 지울'} 수 없습니다: {path}")
    if not locks_ok and holds(path, lists["locks"]):
        raise ValueError(f"잠금 폴더(또는 금고)가 들어 있어 휴지통으로 보내거나 다른 디스크로 옮길 수 없습니다: {path}")
