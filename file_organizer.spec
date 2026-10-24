# PyInstaller 빌드 설정: pyinstaller file_organizer.spec → dist/file_organizer/ (exe + 라이브러리 폴더)
# 라이선스(GPL-3.0)와 함께 쓰는 부품의 고지·원문도 배포본에 넣는다(_internal 안).
a = Analysis(["desktop.py"], datas=[("static", "static"), ("LICENSE", "."), ("THIRD_PARTY_NOTICES.md", "."), ("THIRD_PARTY_LICENSES.txt", ".")])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="file_organizer", console=False, icon="icon.ico")  # 창·작업 표시줄도 exe의 아이콘을 쓴다
COLLECT(exe, a.binaries, a.datas, name="file_organizer")
