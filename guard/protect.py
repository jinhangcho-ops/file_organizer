"""구조 보호 폴더: 작업 폴더처럼 안의 구조를 바꾸면 안 되는 폴더. 색인·목록·검색은 그대로 하고, 이것만 막는다(fence.refuse_holding):
- 보호 폴더 자신(과 그것을 품은 위 폴더)을 옮기기·이름 바꾸기·휴지통
- 안의 파일·폴더 이름 바꾸기·휴지통, 오래된 파일 보관(원본을 휴지통으로 보내므로 대상에서 뺀다)
안의 것을 밖으로 옮기거나 밖의 것을 안으로 넣는 것은 막지 않는다(사용자 결정).
목록은 차단 폴더와 같은 곳(%ProgramData%\\file_organizer\\protected.json)에 두고 관리자 권한으로 실행했을 때만 바꾼다."""
import os

from guard import blacklist
from guard import exclude
from guard import fence
from catalog import index

FILE = "protected.json"


def load():
    return blacklist.load(FILE)


def keys():
    return fence.both(load())


def add(path):
    path = index.real(path)  # 정션 경로를 넣으면 가리키는 진짜 폴더가 보호되지 않는다(네트워크 드라이브는 글자 그대로)
    if not os.path.isdir(path):
        raise ValueError(f"없는 폴더입니다: {path}")
    if fence.inside(path, blacklist.keys()):
        raise ValueError(f"차단 폴더 안이라 이미 다루지 않습니다: {path}")
    if fence.inside(path, keys()):
        raise ValueError(f"이미 구조 보호 폴더(또는 그 아래)입니다: {path}")
    return blacklist.save([p for p in load() if not fence.inside(p, {exclude.key(path)})] + [path], FILE)  # 그 아래의 보호 폴더는 합친다.


def remove(path):
    return blacklist.save([p for p in load() if exclude.key(p) != exclude.key(path)], FILE)
