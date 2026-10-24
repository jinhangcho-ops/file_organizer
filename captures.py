"""게임 캡처 정리: 게임에서 찍은 스크린샷·녹화를 게임마다 모은다. 스크린샷은 사진\\스크린샷\\게임, 영상은 동영상\\게임 녹화\\게임으로 옮긴다.
- Xbox Game Bar: 동영상\\Captures의 '게임 이름 날짜 시각.png/mp4'
- NVIDIA: 동영상\\게임 이름\\의 '게임 이름 (Screenshot) 2026.10.24 - 14.33.00.01(.DVR).mp4/png'
- AMD: 동영상\\Radeon ReLive\\게임 이름\\ 아래의 파일
- Steam: Steam\\userdata\\계정\\760\\remote\\게임 번호\\screenshots(번호는 설치한 게임이면 이름으로)
- 다운로드 정리: 이름이 Game Bar·NVIDIA 모양이면 종류별 폴더 대신 여기로(organize.plan이 capture_target을 부른다).
옮기기는 미리보기와 같은 목록을 서버에서 다시 만들어 한다(organize.apply). 같은 이름이 있으면 ' (2)'."""
import glob
import os
import re
import winreg
from pathlib import Path

import blacklist
import drives
import exclude
import fence
import games
import kinds
import organize
import undo

PICTURES_ID = "33E28130-4E1E-4676-835A-98395C3BC3BB"
VIDEOS_ID = "18989B1D-99B5-455B-841C-AB7C74E4DDFC"
SHOTS, CLIPS = "스크린샷", "게임 녹화"  # 사진 아래, 동영상 아래에 만드는 폴더
GAME_BAR = re.compile(r"^(?P<game>.+?)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}-\d{2}-\d{2}|\d{1,2}_\d{1,2}_\d{4}\s+\d{1,2}_\d{2}_\d{2}\s*[AP]M)")
NVIDIA = re.compile(r"^(?P<game>.+?)\s+(Screenshot\s+)?\d{4}\.\d{2}\.\d{2}\s+-\s+\d{2}\.\d{2}\.\d{2}\.\d+")
PICTURE_KINDS, VIDEO_KINDS = ("사진", "이미지"), ("동영상",)
UNKNOWN = "알 수 없는 게임"


def folders():
    """찾을 곳과 둘 곳의 바탕: 사진·동영상 폴더(Windows 알려진 폴더)와 Steam 폴더. 검사는 이것을 바꾼다."""
    return {"pictures": drives.known_folder(PICTURES_ID) or str(Path.home() / "Pictures"),
            "videos": drives.known_folder(VIDEOS_ID) or str(Path.home() / "Videos"),
            "steam": games.value(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")}


def safe_name(game):
    """폴더 이름으로 쓸 수 있게: Windows가 받지 않는 글자는 빼고, 끝의 점·공백을 뗀다."""
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", game).strip().rstrip(". ")
    return name or UNKNOWN


def game_from_name(name):
    """파일 이름이 Game Bar·NVIDIA 캡처 모양이면 게임 이름, 아니면 None."""
    stem = os.path.splitext(name)[0]
    found = GAME_BAR.match(stem) or NVIDIA.match(stem)
    return found["game"].strip() if found else None


def target(base, path, game, taken):
    """그 파일(사진이나 영상)을 둘 곳. 사진·영상이 아니면 None."""
    kind = kinds.of(os.path.splitext(path)[1].lower())
    if kind in PICTURE_KINDS:
        folder = os.path.join(base["pictures"], SHOTS, safe_name(game))
    elif kind in VIDEO_KINDS:
        folder = os.path.join(base["videos"], CLIPS, safe_name(game))
    else:
        return None
    return organize.free_name(folder, os.path.basename(path), taken)


def capture_target(path, taken, base):
    """다운로드 정리: 게임 캡처 이름이면 둘 곳, 아니면 None. base: folders()(정리 한 번에 한 번 읽는다)."""
    game = game_from_name(os.path.basename(path))
    return target(base, path, game, taken) if game else None


def files_in(folder):
    """폴더 바로 아래의 파일(링크·숨김·시스템 파일은 뺀다)."""
    try:
        with os.scandir(folder) as entries:
            return sorted((e.path for e in entries if e.is_file(follow_symlinks=False) and not e.stat().st_file_attributes & 0x6), key=str.lower)
    except OSError:
        return []


def found(base):
    """[(경로, 게임 이름, 어디서)] 찾은 캡처들."""
    videos = base["videos"]
    out = [(p, game_from_name(os.path.basename(p)), "Xbox Game Bar") for p in files_in(os.path.join(videos, "Captures"))]
    out = [item for item in out if item[1]]
    relive = os.path.join(videos, "Radeon ReLive")
    for folder in blacklist.subfolders(relive):
        out += [(p, os.path.basename(folder), "AMD") for p in glob.glob(os.path.join(glob.escape(folder), "**", "*"), recursive=True) if os.path.isfile(p)]
    skip = {exclude.key(os.path.join(videos, name)) for name in ("Captures", "Radeon ReLive", CLIPS)}
    for folder in blacklist.subfolders(videos):
        if exclude.key(folder) not in skip:
            out += [(p, game, "NVIDIA") for p in files_in(folder) if (game := game_from_name(os.path.basename(p)))]
    if base.get("steam"):
        names = games.steam_names()
        for shots in glob.glob(os.path.join(glob.escape(os.path.normpath(base["steam"])), "userdata", "*", "760", "remote", "*", "screenshots")):
            number = os.path.basename(os.path.dirname(shots))
            out += [(p, names.get(number) or f"Steam 게임 {number}", "Steam") for p in files_in(shots)]
    return out


def plan():
    """옮길 목록 [{"from", "to", "game", "source"}]. 차단·잠금 폴더 안의 것은 뺀다. 옮기지 않고 보여 주기만 한다."""
    base, fences, taken, moves = folders(), fence.keys(), set(), []
    for path, game, source in found(base):
        if fence.inside(path, fences):
            continue
        to = target(base, path, game, taken)
        if to:  # 둘 곳(사진\스크린샷, 동영상\게임 녹화)은 찾는 곳에 들지 않아 이미 옮긴 것은 다시 나오지 않는다.
            taken.add(exclude.key(to))
            moves.append({"from": path, "to": to, "game": safe_name(game), "source": source})
    return moves


def run(progress=lambda *args: None):
    """미리보기와 같은 목록을 다시 만들어 옮긴다. Ctrl+Z로 되돌릴 수 있게 기록한다."""
    moves = plan()
    done, failed = organize.apply([(m["from"], m["to"]) for m in moves], progress)
    undo.record("move", done, f"게임 캡처 정리 {len(done)}개")
    return {"moved": len(done), "failed": failed}
