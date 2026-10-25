"""예외 목록(exclude.json). 예외인 파일은 모아 보기(최근 받은·최근 고친·오래 안 본 파일)에만 나오지 않는다.
폴더를 예외로 하면 그 아래 전부, 파일을 예외로 하면 그 파일 하나만."""
import os

import index
import settings

FILE = "exclude.json"
fixed = set()  # 옛 판이 정션 경로로 적어 둔 것을 실제 경로로 바꿔 둔 설정 폴더들(한 번만 본다)


def key(path):
    """대소문자·구분자를 맞춘 비교용 경로. 색인의 key 칸과 같다."""
    return os.path.normcase(os.path.abspath(path))


def real(path, folder):
    """색인에 적히는 모양(index.real): 폴더는 그 자신까지, 파일은 든 폴더만 따라간다(파일 링크는 따라가지 않게, index.moved와 같다)."""
    return index.real(path) if folder else os.path.join(index.real(os.path.dirname(path)), os.path.basename(path))


def once(name, data, convert):
    """설정 폴더마다 처음 읽을 때 한 번 convert(data)로 정션 경로를 실제 경로로 바꾸고, 바뀐 것이 있으면 저장한다."""
    place = (str(settings.folder()), name)
    if place in fixed:
        return data
    fixed.add(place)
    new = convert(data)
    if new != data:
        settings.write(name, new)
    return new


def load():
    return once(FILE, settings.read(FILE, []), lambda rules: [{**r, "path": real(r["path"], r["kind"] == "folder")} for r in rules])


def add(path):
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise ValueError(f"없는 파일·폴더입니다: {path}")
    rules = [rule for rule in load() if key(rule["path"]) != key(path)]
    rules.append({"path": path, "kind": "folder" if os.path.isdir(path) else "file"})
    settings.write(FILE, rules)
    return rules


def remove(path):
    rules = [rule for rule in load() if key(rule["path"]) != key(path)]
    settings.write(FILE, rules)
    return rules


def where(rules):
    """색인(files 표)에서 예외를 빼는 SQL 조건과 값. 규칙이 없으면 빈 조건."""
    parts, values = [], []
    for rule in rules:
        if rule["kind"] == "folder":
            prefix = os.path.join(key(rule["path"]), "")  # 끝에 구분자를 붙여 D:\a가 D:\ab를 빼지 않게 한다.
            parts.append("substr(key, 1, ?) <> ?")
            values += [len(prefix), prefix]
        else:
            parts.append("key <> ?")
            values.append(key(rule["path"]))
    return " and ".join(parts), values
