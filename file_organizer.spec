# PyInstaller 빌드 설정: pyinstaller file_organizer.spec → dist/file_organizer/ (exe + 라이브러리 폴더)
a = Analysis(["desktop.py"], datas=[("static", "static")])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, exclude_binaries=True, name="file_organizer", console=False, icon="icon.ico")  # 창·작업 표시줄도 exe의 아이콘을 쓴다
COLLECT(exe, a.binaries, a.datas, name="file_organizer")
