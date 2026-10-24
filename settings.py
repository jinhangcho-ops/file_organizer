"""설정 폴더(%APPDATA%\\file_organizer)의 JSON 파일 읽기·쓰기와 화면 설정의 검사."""
import json
import os
import re
import winreg
from pathlib import Path

import cache
import drives
import fence
import kinds

DAYS = (1, 3650)  # 모아 보기 기간(일)의 범위
# 왼쪽 폴더 목록·오른쪽 상세 칸의 너비(px) 범위와 기본값. 0을 저장하면 기본으로 돌아간다. 화면은 /api/state의 panes로 받는다.
PANES = {"pane_left": {"min": 200, "max": 700, "default": 380}, "pane_detail": {"min": 260, "max": 700, "default": 340}}
# 색인의 ① 파일 목록 다음 단계들(화면 이름). 순서·켜기는 설정 index_steps, 처음에는 이 순서이고 인식만 꺼져 있다(모델을 받아야 한다).
STEPS = {"hash": "중복 확인", "text": "파일 내용 읽기", "doc": "문서 비교", "media": "사진·동영상 비교", "recognize": "사진·동영상 인식"}
NUMBERS = {"video_interval": (30, 600, "동영상 장면 간격(초)"), "video_similar": (50, 100, "같다고 볼 유사도(%)"),
           "cache_days": (1, 365, "캐시를 지울 기준(일)")}


def folder():
    """설정 폴더. 테스트는 APPDATA를 임시 폴더로 바꿔 실제 설정을 건드리지 않는다."""
    path = Path(os.environ.get("APPDATA") or Path.home(), "file_organizer")
    path.mkdir(parents=True, exist_ok=True)
    return path


def read(name, default):
    try:
        return json.loads(Path(folder(), name).read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return default


def write(name, data):
    """임시 파일에 다 쓴 뒤 바꿔치기해서, 쓰다 멈춰도 원래 파일이 깨지지 않게 한다."""
    path = Path(folder(), name)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(temp, path)


def defaults():
    known = drives.known_folders()
    # chip_counts: 종류 칩을 누른 횟수(많이 누른 칩을 왼쪽에 놓는다). archive_dest·cold_days: 오래된 파일 보관의 보관할 곳과 기간.
    return {"source": known["다운로드"], "dest": "", "roots": list(known.values()), "recent_days": 7, "stale_days": 90,
            "chip_counts": {}, "archive_dest": "", "cold_days": 365, "theme": "auto",  # theme: auto(Windows 설정을 따름), light, dark
            "pane_left": 0, "pane_detail": 0,  # 칸 너비(0: 기본)
            # 목록 보기: 왼쪽 폴더 목록의 드라이브("" 전체, "D:\" 등), 여러 폴더가 섞인 목록을 폴더별로 묶기, 설치파일 맨 위, 상세의 이름이 같은 파일 칸
            "drive": "", "list_group": "none", "pin_installers": False, "show_same_name": True,
            "index_steps": [{"name": name, "on": name != "recognize"} for name in STEPS], "video_interval": 120, "video_similar": 70,
            # 인식: 얼굴로 같은 사람 찾기, 내 분류 모델 파일과 분류마다 보여 줄 이름·기준 점수(%)·칩으로 보기
            "face_on": True, "custom_model_path": "", "custom_labels": [],
            # 캐시 정리: 이 일수보다 오래된 것만, 체크를 끈 캐시(cache.IDS)
            "cache_days": 7, "cache_skip": []}


def dark(config=None):
    """화면을 다크로 그릴지: 설정이 dark이거나, auto(Windows 설정을 따름)이고 Windows 앱 모드가 다크. 창과 첫 화면의 색에 쓴다."""
    theme = (config or load())["theme"]
    if theme != "auto":
        return theme == "dark"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            return winreg.QueryValueEx(key, "AppsUseLightTheme")[0] == 0
    except OSError:
        return False


def load():
    return {**defaults(), **read("settings.json", {})}


def step_list(config):
    """단계 목록 [{"name", "on"}] 순서대로. 저장된 목록에 없는 단계(새로 생긴 것)는 기본값으로 끝에 붙인다."""
    saved = [s for s in config["index_steps"] if s["name"] in STEPS]
    named = {s["name"] for s in saved}
    return saved + [s for s in defaults()["index_steps"] if s["name"] not in named]


def steps(config):
    """켜 둔 단계 이름들을 순서대로."""
    return [s["name"] for s in step_list(config) if s["on"]]


def folder_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: 폴더를 고르세요.")
    path = os.path.abspath(value.strip())
    if not os.path.isdir(path):
        raise ValueError(f"{name}: 없는 폴더입니다. {path}")
    return fence.refuse_system(fence.refuse(path))


def check_drive(value):
    """'D:\\' 모양의 드라이브만 받는다(설정과 목록 조회가 같이 쓴다)."""
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z]:\\", value):
        raise ValueError(f"드라이브는 'D:\\' 모양으로 쓰세요: {value}")
    return value


def whole(value):
    """정수인지(True·False는 int이지만 뺀다)."""
    return isinstance(value, int) and not isinstance(value, bool)


def check(changes):
    """화면에서 온 바꿀 설정을 검사해 깨끗한 값으로 돌려준다. 모르는 이름이나 잘못된 값은 거부한다."""
    clean = {}
    for name, value in changes.items():
        if name in ("source", "dest", "archive_dest"):
            clean[name] = folder_text(value, {"source": "정리할 곳", "dest": "놓을 곳", "archive_dest": "보관할 곳"}[name])
        elif name == "roots":
            if not isinstance(value, list):
                raise ValueError("색인할 폴더 목록이 잘못됐습니다.")
            saved = {k.lower(): k for k in load()["roots"]}
            clean[name] = list(dict.fromkeys(saved[item.lower()] if isinstance(item, str) and item.lower() in saved and not os.path.isdir(item)
                                             else folder_text(item, "색인할 폴더") for item in value))
        elif name in ("recent_days", "stale_days", "cold_days"):
            if not whole(value) or not DAYS[0] <= value <= DAYS[1]:
                raise ValueError(f"기간은 {DAYS[0]}~{DAYS[1]}일 사이의 정수로 쓰세요.")
            clean[name] = value
        elif name in PANES:
            low, high = PANES[name]["min"], PANES[name]["max"]
            if not whole(value) or not (value == 0 or low <= value <= high):
                raise ValueError(f"칸 너비는 {low}~{high}px 사이의 정수로 쓰세요(0은 기본).")
            clean[name] = value
        elif name == "index_steps":
            if (not isinstance(value, list) or sorted(s.get("name") for s in value if isinstance(s, dict)) != sorted(STEPS)
                    or any(not isinstance(s.get("on"), bool) or len(s) != 2 for s in value)):
                raise ValueError("색인 단계 목록이 잘못됐습니다.")
            clean[name] = [{"name": s["name"], "on": s["on"]} for s in value]
        elif name in NUMBERS:
            low, high, label = NUMBERS[name]
            if not whole(value) or not low <= value <= high:
                raise ValueError(f"{label}은 {low}~{high} 사이의 정수로 쓰세요.")
            clean[name] = value
        elif name == "face_on":
            if not isinstance(value, bool):
                raise ValueError("얼굴 찾기 설정이 잘못됐습니다.")
            clean[name] = value
        elif name == "custom_model_path":
            if not isinstance(value, str) or (value and not (os.path.isfile(value) and value.lower().endswith(".onnx"))):
                raise ValueError("내 분류 모델은 .onnx 파일을 고르세요.")
            clean[name] = os.path.abspath(value) if value else ""
        elif name == "custom_labels":
            if not isinstance(value, list) or len(value) > 50 or any(
                    not isinstance(item, dict) or set(item) != {"name", "threshold", "show"} or not isinstance(item["name"], str)
                    or len(item["name"]) > 30 or not isinstance(item["threshold"], int) or isinstance(item["threshold"], bool)
                    or not 1 <= item["threshold"] <= 100 or not isinstance(item["show"], bool) for item in value):
                raise ValueError("내 분류 모델의 분류 설정이 잘못됐습니다(이름 30자, 기준 1~100%).")
            clean[name] = [{"name": item["name"].strip(), "threshold": item["threshold"], "show": item["show"]} for item in value]
        elif name == "cache_skip":
            clean[name] = cache.check_ids(value)
        elif name == "drive":
            clean[name] = "" if value == "" else check_drive(value).upper()
        elif name == "list_group":
            if value not in ("none", "folder"):
                raise ValueError(f"모르는 묶기 방식입니다: {value}")
            clean[name] = value
        elif name in ("pin_installers", "show_same_name"):
            if not isinstance(value, bool):
                raise ValueError(f"켜기·끄기 값이 잘못됐습니다: {name}")
            clean[name] = value
        elif name == "theme":
            if value not in ("auto", "light", "dark"):
                raise ValueError(f"모르는 화면 색입니다: {value}")
            clean[name] = value
        elif name == "chip_counts":
            if not isinstance(value, dict) or any(k not in kinds.NAMES or not isinstance(n, int) or n < 0 for k, n in value.items()):
                raise ValueError("칩 사용 횟수가 잘못됐습니다.")
            clean[name] = value
        else:
            raise ValueError(f"모르는 설정입니다: {name}")
    return clean


def count_chip(kind):
    """종류 칩을 한 번 누른 것을 센다. 저장된 다른 설정은 그대로 두고 이 칸만 고친다."""
    if kind not in kinds.NAMES:
        raise ValueError(f"모르는 종류입니다: {kind}")
    counts = dict(read("settings.json", {}).get("chip_counts") or {})
    counts = {k: n for k, n in counts.items() if k in kinds.NAMES and isinstance(n, int)}  # 예전 종류 이름(없어진 것)은 버린다.
    counts[kind] = counts.get(kind, 0) + 1
    return save({"chip_counts": counts})


def save(changes):
    current = read("settings.json", {})
    current.update(check(changes))
    write("settings.json", current)
    return load()
