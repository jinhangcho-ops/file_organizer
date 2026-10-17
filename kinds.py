"""확장자 → 종류. 정리할 때의 폴더 이름이자 검색의 종류 조건. 표에 없는 확장자는 OTHER."""
KINDS = {
    "문서": ".pdf .doc .docx .hwp .hwpx .txt .md .rtf .odt .xls .xlsx .xlsm .csv .tsv .ppt .pptx .odp .ods .json .xml .html .htm",
    "이미지": ".jpg .jpeg .png .gif .bmp .webp .heic .svg .tif .tiff .ico .raw .psd",
    "동영상": ".mp4 .mkv .avi .mov .wmv .webm .flv .m4v .ts",
    "음악": ".mp3 .wav .flac .aac .m4a .ogg .wma",
    "압축": ".zip .7z .rar .tar .gz .bz2 .xz .alz .egg",
    "설치파일": ".exe .msi .msix .appx .apk .iso .dmg",
}
OTHER = "기타"
NAMES = list(KINDS) + [OTHER]
BY_EXT = {ext: name for name, exts in KINDS.items() for ext in exts.split()}


def of(ext):
    return BY_EXT.get(ext.lower(), OTHER)
