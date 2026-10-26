"""열한 번째 감사(화면·창 쪽): 정션인 놓을 곳의 미리보기가 저장된 실제 경로를 돌려주는지, 알려진 폴더를 실제 경로로 보내는지,
창 크기를 첫 화면의 작업 영역에 맞추는지. desktop.py는 불러오지 않고 함수만 떼어 돌린다(창을 띄우지 않는다)."""
import ast
import os
import subprocess
from pathlib import Path

import drives
import index
from conftest import write

ROOT = Path(__file__).resolve().parent.parent
DESKTOP = (ROOT / "desktop.py").read_text(encoding="utf-8")


def junction(link, target):
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


def test_preview_answers_saved_real_paths(home, client, tmp_path):
    """화면은 to에서 놓을 곳의 앞부분을 잘라 '옮길 곳'에 쓴다: 놓을 곳이 정션이면 plan의 to는 실제 경로라 답의 dest로 잘라야 한다."""
    downloads, target = tmp_path / "받은 것", tmp_path / "진짜 놓을 곳"
    write(downloads / "보고서.txt")
    target.mkdir()
    link = tmp_path / "놓을 곳 링크"
    junction(link, target)
    try:
        r = client.post("/api/organize/preview", json={"source": str(downloads), "dest": str(link)}).get_json()
        assert r["dest"] == index.real(str(target)) and r["source"] == index.real(str(downloads))
        assert r["moves"] and all(m["to"].startswith(r["dest"] + os.sep) for m in r["moves"])
        assert all(m["to"][len(r["dest"]):].lstrip(os.sep).endswith("보고서.txt") for m in r["moves"])  # app.js의 자르기
    finally:
        os.rmdir(link)  # 링크만 지운다


def test_archive_preview_saves_real_dest(home, client, tmp_path):
    """화면은 보관 미리보기 뒤 저장된 archive_dest로 확인 문구를 쓴다: 정션을 골라도 실제 경로가 저장돼 있어야 한다."""
    target = tmp_path / "진짜 보관할 곳"
    target.mkdir()
    link = tmp_path / "보관 링크"
    junction(link, target)
    try:
        assert client.post("/api/archive/preview", json={"dest": str(link), "days": 180}).status_code == 200
        assert client.get("/api/state").get_json()["settings"]["archive_dest"] == index.real(str(target))
    finally:
        os.rmdir(link)


def test_known_folders_sent_as_real_paths(home, client, tmp_path, monkeypatch):
    """색인할 폴더(roots)는 실제 경로로 저장되므로 known도 같은 모양이어야 '+ 문서' 칩이 다시 나오지 않고 다운로드 칩이 켜진다."""
    target = tmp_path / "진짜 문서"
    target.mkdir()
    link = tmp_path / "문서 링크"
    junction(link, target)
    try:
        monkeypatch.setattr(drives, "known_folders", lambda: {"다운로드": str(link), "문서": str(home), "바탕 화면": str(home)})
        known = client.get("/api/state").get_json()["known"]
        assert known["다운로드"] == index.real(str(target)) and known["문서"] == index.real(str(home))
    finally:
        os.rmdir(link)


def fit():
    tree = ast.parse(DESKTOP)
    space = {"SCREEN_MARGIN": next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "SCREEN_MARGIN")}
    exec(ast.unparse(next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "fit")), space)
    return space["fit"]


def test_window_size_fits_work_area():
    """pywebview는 크기에 화면 배율을 곱한다: 논리 픽셀의 작업 영역에 들어가게 줄여야 아래쪽·□ ✕가 화면 밖으로 나가지 않는다."""
    f = fit()
    for work in [(1280, 672), (1093, 576), (3413, 1392)]:  # 150%의 1920x1080, 125%의 1366x768, 넓은 화면(작업 표시줄 뺀 논리 픽셀)
        size, low = f((1400, 900), work), f((1160, 660), work)
        assert all(s < w for s, w in zip(size, work)) and all(m <= s for m, s in zip(low, size))
    assert f((1400, 900), (3413, 1392)) == (1400, 900)  # 넉넉하면 그대로
    assert f((1160, 660), (1093, 576)) == (1053, 536)
    # 창은 줄인 크기와 가장 작은 크기로 만든다(가장자리 끌기의 bounds·place도 같은 MIN_SIZE)
    assert "MIN_SIZE = fit((1160, 660), WORK)" in DESKTOP and "width=SIZE[0], height=SIZE[1]" in DESKTOP and "min_size=MIN_SIZE" in DESKTOP
