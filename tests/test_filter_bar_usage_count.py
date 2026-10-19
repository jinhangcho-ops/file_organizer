"""종류 칩 사용 횟수: 누를 때마다 쌓이고, 많이 누른 순서로 칩이 놓이며(같으면 종류 표 순서), 다시 켜도(설정 파일에서 다시 읽어도) 그대로인지.
기존 설정은 잃지 않고 이 값만 더하는지, 예전 종류 이름은 버리는지."""
import json

import kinds
import search
import settings


def order():
    return [name for name, _ in search.listing({})["kinds"]]


def test_counts_accumulate_and_sort_descending(home, client):
    for name in ["PDF", "PDF", "PDF", "발표 자료", "발표 자료", "문서"]:
        assert client.post("/api/chip", json={"kind": name}).status_code == 200
    assert settings.load()["chip_counts"] == {"PDF": 3, "발표 자료": 2, "문서": 1}
    rest = [name for name in kinds.NAMES if name not in ("PDF", "발표 자료", "문서")]
    assert order() == ["PDF", "발표 자료", "문서"] + rest


def test_order_survives_restart(home):
    for name in ["압축", "압축", "사진"]:
        settings.count_chip(name)
    saved = json.loads((settings.folder() / "settings.json").read_text(encoding="utf-8"))
    assert saved["chip_counts"] == {"압축": 2, "사진": 1}  # 파일에 남아 있다
    assert order()[:2] == ["압축", "사진"]  # 설정은 부를 때마다 파일에서 읽으므로 다시 켠 것과 같다


def test_existing_settings_are_kept_and_old_names_dropped(home):
    path = settings.folder() / "settings.json"
    old = json.loads(path.read_text(encoding="utf-8")) | {"stale_days": 30, "chip_counts": {"없어진종류": 9, "PDF": 1}}
    path.write_text(json.dumps(old, ensure_ascii=False), encoding="utf-8")
    settings.count_chip("PDF")
    now = settings.load()
    assert now["stale_days"] == 30 and now["roots"] == [str(home)]  # 다른 설정은 그대로
    assert now["chip_counts"] == {"PDF": 2}


def test_unknown_chip_is_refused(home, client):
    answer = client.post("/api/chip", json={"kind": "그림"})
    assert answer.status_code == 400
    assert settings.load()["chip_counts"] == {}
