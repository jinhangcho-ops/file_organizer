"""상세의 내용 미리보기: 색인이 읽어 둔 글자의 앞부분(공백 하나로, 길면 …), 읽지 않는 형식은 readable=False."""
import search
from conftest import build, write


def test_preview_from_index(home, client):
    short = write(home / "메모.txt", "첫 줄\n\n  둘째   줄")
    long = write(home / "긴 글.md", "가나다 " * 200)
    photo = home / "사진.jpg"
    photo.write_bytes(b"\xff\xd8\xff" + b"0" * 100)
    build(home)
    assert client.get("/api/preview?path=" + short).json == {"text": "첫 줄 둘째 줄", "readable": True}
    text = client.get("/api/preview?path=" + long).json["text"]
    assert len(text) == search.PREVIEW + 1 and text.endswith("…")
    assert client.get("/api/preview?path=" + str(photo)).json == {"text": "", "readable": False}
    assert client.get("/api/preview?path=" + str(home / "없음.txt")).status_code == 400  # 색인에 없는 경로
