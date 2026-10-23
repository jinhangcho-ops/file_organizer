"""사진·동영상 인식: 종류(CLIP, 받아 둔 모델이 있을 때만), 내 분류 모델 점수와 칩(가짜 모델), 같은 사람 묶기(지어낸 얼굴 지문),
모델 받기(가짜 받기: 크기·SHA-256이 틀리면 버림), 추정 칩 조건, 설정 검사. 그림은 테스트 안에서 그린다."""
import hashlib

import numpy as np
import pytest
from PIL import Image, ImageDraw

import index
import models
import recognize
import settings
from conftest import build


def text_picture(path):
    img = Image.new("RGB", (600, 400), "white")
    d = ImageDraw.Draw(img)
    for y in range(20, 380, 22):
        d.text((20, y), "Quarterly report: revenue increased by 12 percent compared to last year", fill="black")
    img.save(path)
    return str(path)


def recognize_steps():
    return [{"name": name, "on": name in ("hash", "recognize")} for name in settings.STEPS]


@pytest.mark.skipif(not models.ready("clip"), reason="종류 인식 모델을 받아 두지 않았다")
def test_document_picture_is_recognized_and_listed(home, client):
    doc = text_picture(home / "보고서 사진.png")
    sky = Image.new("RGB", (600, 400))
    d = ImageDraw.Draw(sky)
    for y in range(400):  # 위는 하늘, 아래는 풀밭
        d.line([(0, y), (600, y)], fill=(90 + y // 4, 150 + y // 6, 255) if y < 260 else (40, 120 + y % 7, 40))
    sky.save(home / "하늘.png")
    settings.save({"index_steps": recognize_steps(), "face_on": False})
    build(home)
    assert client.get("/api/recognized?path=" + doc).json["tags"][0][0] == "group:document"
    found = client.post("/api/files", json={"kind": "이미지"}).json
    assert ["group:document", 1] in found["tags"]
    rows = client.post("/api/files", json={"kind": "이미지", "tag": "group:document"}).json["rows"]
    assert [r["name"] for r in rows] == ["보고서 사진.png"]
    db = index.connect()
    assert db.execute("select count(*) from recognized").fetchone()[0] == 2
    db.close()
    build(home)  # 바뀌지 않은 파일은 다시 인식하지 않는다
    assert client.post("/api/files", json={"kind": "이미지", "tag": "bad"}).status_code == 400


class FakeCustom:
    """내 분류 모델 대신: 분류 3개, 밝은 그림일수록 1번 점수가 높다."""

    def get_inputs(self):
        return [type("I", (), {"name": "pixel_values"})()]

    def run(self, _, feed):
        bright = float(feed["pixel_values"].mean())
        return [np.array([[0.0, bright * 10, -5.0]], dtype=np.float32)]


class FakeEngine:
    def __init__(self):
        self.np, self.faces = np, None
        self.custom, self.custom_input = FakeCustom(), {"size": (8, 8), "mean": (0.5,) * 3, "std": (0.5,) * 3}

    custom_scores = recognize.Engine.custom_scores

    def image_vector(self, image):
        return np.ones(4) / 2

    def kinds(self, vector):
        return [("group", "other", 90)]


def test_custom_model_scores_and_chips(home, client, monkeypatch, tmp_path):
    Image.new("RGB", (64, 64), "white").save(home / "밝음.png")
    Image.new("RGB", (64, 64), "black").save(home / "어두움.png")
    model = tmp_path / "모델" / "m.onnx"
    model.parent.mkdir()
    model.write_bytes(b"fake")
    (model.parent / "config.json").write_text('{"id2label": {"0": "x", "1": "y", "2": "z"}}', encoding="utf-8")
    answer = client.post("/api/custom_model", json={"path": str(model)}).json
    assert answer["custom_count"] == 3 and [label["name"] for label in answer["settings"]["custom_labels"]] == ["분류 1", "분류 2", "분류 3"]
    labels = [{"name": "가", "threshold": 80, "show": False}, {"name": "밝은 것", "threshold": 60, "show": True}, {"name": "다", "threshold": 80, "show": False}]
    settings.save({"custom_labels": labels, "index_steps": [{"name": n, "on": n == "hash"} for n in settings.STEPS]})
    build(home)
    db = index.connect()
    config = settings.load()
    for row in db.execute("select id, path, ext, stamp from files").fetchall():
        recognize.recognize_one(db, FakeEngine(), row, config, {})
    db.commit()
    db.close()
    found = client.post("/api/files", json={"kind": "이미지"}).json["tags"]
    assert ["custom:1", 1] in found and not any(tag.startswith("custom:0") for tag, _ in found)  # 칩으로 보기를 끈 분류는 세지 않는다
    rows = client.post("/api/files", json={"kind": "이미지", "tag": "custom:1"}).json["rows"]
    assert [r["name"] for r in rows] == ["밝음.png"]
    assert client.get("/api/recognized?path=" + str(home / "밝음.png")).json["tags"] == [["group:other", 90], ["custom:1", 100]]
    assert client.post("/api/custom_model", json={"path": str(tmp_path / "없음.onnx")}).status_code == 400
    assert client.post("/api/settings", json={"custom_labels": [{"name": "x", "threshold": 0, "show": True}]}).status_code == 400


def test_faces_are_grouped_by_person(home, client):
    db = index.connect()
    rng = np.random.default_rng(1)
    a, b = rng.normal(size=128), rng.normal(size=128)
    unit = lambda v: (v / np.linalg.norm(v)).astype(np.float32)  # noqa: E731
    centers = {}
    recognize.assign(db, 1, [unit(a), unit(b)], centers)
    recognize.assign(db, 2, [unit(a + rng.normal(scale=0.3, size=128))], centers)  # 같은 사람(조금 다른 사진)
    recognize.assign(db, 3, [unit(rng.normal(size=128))], centers)  # 다른 사람
    people = dict(((i, n), p) for i, n, p in db.execute("select id, n, person from faces"))
    assert people[(1, 0)] == people[(2, 0)] and len({people[(1, 0)], people[(1, 1)], people[(3, 0)]}) == 3
    db.commit()
    db.close()
    assert client.post("/api/faces/forget").json["settings"]["face_on"] is False
    db = index.connect()
    assert db.execute("select count(*) from faces").fetchone()[0] == 0
    db.close()


def test_model_download_checks_size_and_hash(tmp_path, monkeypatch):
    monkeypatch.setenv("FILE_ORGANIZER_MODELS", str(tmp_path / "models"))
    good = b"good model bytes"
    monkeypatch.setitem(models.FILES, "face", [("face/a.onnx", "https://example.invalid/a", len(good), hashlib.sha256(good).hexdigest()),
                                               ("face/b.onnx", "https://example.invalid/b", 3, "0" * 64)])

    def fetch(url, target, progress):
        target.write_bytes(good if url.endswith("/a") else b"bad")
        progress(target.stat().st_size)

    monkeypatch.setattr(models, "fetch", fetch)
    result = models.download(["face"])
    assert result["done"] == ["face/a.onnx"] and result["failed"][0]["from"] == "face/b.onnx"
    assert (tmp_path / "models" / "face" / "a.onnx").read_bytes() == good
    assert not (tmp_path / "models" / "face" / "b.onnx").exists() and not list((tmp_path / "models" / "face").glob("*.part"))
    assert models.ready("face") is False


def test_recognize_step_without_models_says_what_to_do(home, monkeypatch, tmp_path):
    monkeypatch.setenv("FILE_ORGANIZER_MODELS", str(tmp_path / "없는 곳"))
    db = index.connect()
    with pytest.raises(ValueError, match="모델 받기"):
        recognize.run(db)
    db.close()


def test_tag_condition_rules():
    config = {"custom_labels": [{"name": "a", "threshold": 70, "show": True}]}
    assert recognize.LABELS["animal"]["dog"] == "강아지"
    from search import tag_condition
    assert tag_condition("custom:0", config)[1] == ["0", 70]
    assert tag_condition("person:4", config)[1] == [4]
    for bad in ("custom:x", "person:", "몰라:1", "group:"):
        with pytest.raises(ValueError):
            tag_condition(bad, config)


@pytest.mark.skipif(not models.ready("clip"), reason="종류 인식 모델을 받아 두지 않았다")
def test_recognition_runs_in_its_own_process_and_can_stop(home, client):
    import sys

    for n in range(40):
        Image.new("RGB", (200, 150), (n * 6, 100, 255 - n * 6)).save(home / f"{n:02}.png")
    settings.save({"index_steps": [{"name": n, "on": n == "hash"} for n in settings.STEPS], "face_on": False})
    build(home)
    assert client.post("/api/index", json={"only": "recognize"}).status_code == 200
    for _ in range(600):  # 인식을 시작해 파일을 다루기 시작하면 멈춘다
        if index.status["current"].endswith(".png"):
            break
        index.stop.wait(0.1)
    index.stop.set()
    index.worker.join(120)
    db = index.connect()
    done = db.execute("select count(*) from recognized").fetchone()[0]
    db.close()
    assert not index.status["error"] and index.status["stopped"] and 0 < done < 40
    assert "onnxruntime" not in sys.modules  # 모델·라이브러리는 인식 프로세스에만 올라갔다
    assert not (settings.folder() / recognize.STOP).exists()
