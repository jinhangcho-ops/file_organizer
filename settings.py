"""설정 폴더(%APPDATA%\\file_organizer)의 JSON 파일 읽기·쓰기와 화면 설정의 검사."""
import json
import os
from pathlib import Path

import drives
import kinds

DAYS = (1, 3650)  # 모아 보기 기간(일)의 범위


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
            "chip_counts": {}, "archive_dest": "", "cold_days": 365, "theme": "auto"}  # theme: auto(Windows 설정을 따름), light, dark


def load():
    return {**defaults(), **read("settings.json", {})}


def folder_text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}: 폴더를 고르세요.")
    path = os.path.abspath(value.strip())
    if not os.path.isdir(path):
        raise ValueError(f"{name}: 없는 폴더입니다. {path}")
    return path


def check(changes):
    """화면에서 온 바꿀 설정을 검사해 깨끗한 값으로 돌려준다. 모르는 이름이나 잘못된 값은 거부한다."""
    clean = {}
    for name, value in changes.items():
        if name in ("source", "dest", "archive_dest"):
            clean[name] = folder_text(value, {"source": "정리할 곳", "dest": "놓을 곳", "archive_dest": "보관할 곳"}[name])
        elif name == "roots":
            if not isinstance(value, list):
                raise ValueError("색인할 폴더 목록이 잘못됐습니다.")
            clean[name] = list(dict.fromkeys(folder_text(item, "색인할 폴더") for item in value))
        elif name in ("recent_days", "stale_days", "cold_days"):
            if not isinstance(value, int) or isinstance(value, bool) or not DAYS[0] <= value <= DAYS[1]:
                raise ValueError(f"기간은 {DAYS[0]}~{DAYS[1]}일 사이의 정수로 쓰세요.")
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
