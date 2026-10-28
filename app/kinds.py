"""확장자 → 종류. 정리할 때의 폴더 이름이자 목록의 종류 칩. 표에 없는 확장자는 OTHER."""
KINDS = {
    "문서": ".doc .docx .hwp .hwpx .txt .md .rtf .odt .json .xml .html .htm",
    "발표 자료": ".ppt .pptx .pps .ppsx .key .odp",
    "표": ".xls .xlsx .xlsm .csv .tsv .ods",
    "PDF": ".pdf",
    "사진": ".jpg .jpeg .heic .heif .raw .cr2 .cr3 .nef .arw .dng .orf .rw2",  # 카메라·휴대폰으로 찍은 것
    "이미지": ".png .gif .bmp .webp .svg .tif .tiff .ico .psd .ai",  # 그 밖의 그림(스크린샷, 아이콘, 디자인 파일)
    "동영상": ".mp4 .mkv .avi .mov .wmv .webm .flv .m4v .ts",
    "음악": ".mp3 .wav .flac .aac .m4a .ogg .wma",
    "압축": ".zip .7z .rar .tar .gz .bz2 .xz .alz .egg",
    "설치파일": ".exe .msi .msix .appx .apk .iso .dmg",
}
# 연결된 프로그램으로 '열면' 실행되는 종류(os.startfile). 앱의 [열기]는 이것을 실행하지 않고 탐색기에서 골라 보여 준다.
RUNNABLE = set(".exe .msi .msix .msixbundle .appx .appxbundle .com .bat .cmd .scr .pif .cpl .lnk .url .js .jse .vbs .vbe .wsf .wsh "
               ".hta .ps1 .psm1 .reg .jar .msc .application .appref-ms .settingcontent-ms".split())
OTHER = "기타"
NAMES = list(KINDS) + [OTHER]
BY_EXT = {ext: name for name, exts in KINDS.items() for ext in exts.split()}


def of(ext):
    return BY_EXT.get(ext.lower(), OTHER)
