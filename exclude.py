"""예외 목록(exclude.json). 예외인 파일은 모아 보기(최근 받은·최근 고친·오래 안 본 파일)에만 나오지 않는다.
폴더를 예외로 하면 그 아래 전부, 파일을 예외로 하면 그 파일 하나만."""
import os

import settings

FILE = "exclude.json"


def key(path):
    """대소문자·구분자를 맞춘 비교용 경로. 색인의 key 칸과 같다."""
    return os.path.normcase(os.path.abspath(path))


def load():
    return settings.read(FILE, [])


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
