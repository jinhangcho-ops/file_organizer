# 함께 쓰는 부품과 라이선스

파일 정리는 GPL-3.0으로 배포합니다(`LICENSE`). exe·설치 프로그램에는 아래 부품이 함께 들어 있습니다. 각 Python 패키지의 라이선스 원문은 `THIRD_PARTY_LICENSES.txt`에 모았습니다(패키지가 배포한 파일 그대로). 버전은 `requirements.txt`와 같습니다.

## GPL·LGPL 부품 (소스 코드를 받을 수 있는 곳)

| 부품 | 쓰는 곳 | 라이선스 | 소스 코드 |
|---|---|---|---|
| FFmpeg 7.1 (gyan.dev essentials 빌드, imageio-ffmpeg 0.6.0이 넣어 줌) | 동영상 장면 뽑기·길이 | GPL-3.0(`--enable-gpl --enable-version3`로 빌드) | https://ffmpeg.org/download.html (git 태그 `n7.1`), 빌드 설명 https://www.gyan.dev/ffmpeg/builds/ |
| x265 4.3 (pillow-heif 1.8.1이 넣어 줌) | HEIC 사진 읽기 | GPL-2.0(pillow-heif의 표기) | https://github.com/Multicorewareinc/x265/tree/4.3 |
| libheif 1.23.6 (pillow-heif) | HEIC 사진 읽기 | LGPL-3.0 | https://github.com/strukturag/libheif/tree/v1.23.6 |
| libde265 1.1.3 (pillow-heif) | HEIC 사진 읽기 | LGPL-3.0 | https://github.com/strukturag/libde265/tree/v1.1.3 |
| MinGW-w64 런타임(libgcc, libstdc++, libwinpthread; pillow-heif) | 위 라이브러리의 실행 | GPL-3.0 + GCC 런타임 예외, MIT·BSD | https://sourceforge.net/p/mingw-w64/mingw-w64/ |
| PyInstaller 6.22.3의 부트로더 | exe 실행 | GPL-2.0 이상 + 예외(만든 프로그램은 어떤 라이선스로든 배포 가능) | https://github.com/pyinstaller/pyinstaller |

pillow-heif는 소스가 BSD-3-Clause이지만, 위 라이브러리가 들어간 Windows 배포본은 GPL-2.0이라고 밝힙니다. 이 앱의 소스 코드는 이 저장소에 있습니다.

## 그 밖의 부품

| 부품 | 버전 | 라이선스 |
|---|---|---|
| Python | 3.14.3 | PSF-2.0 |
| Microsoft Visual C++ 런타임(vcruntime140, msvcp140) | | Microsoft 재배포 조건 |
| Flask, Werkzeug, Jinja2, MarkupSafe, itsdangerous, click | 3.1.3, 3.1.9, 3.1.6, 3.0.4, 2.2.0, 8.5.0 | BSD-3-Clause |
| blinker | 1.9.0 | MIT |
| pywebview | 6.2.1 | BSD-3-Clause |
| pythonnet, clr_loader, proxy_tools, bottle | 3.2.1, 0.3.1, 0.1.0, 0.13.4 | MIT |
| pypdfium2(PDFium 156.0.8076 포함) | 5.14.0 | BSD-3-Clause·Apache-2.0, PDFium이 쓰는 라이브러리는 각자의 라이선스(원문 파일에 포함) |
| python-docx, python-pptx, openpyxl, et_xmlfile, XlsxWriter | 1.2.0, 1.0.2, 3.1.5, 2.0.0, 3.2.9 | MIT(XlsxWriter는 BSD-2-Clause) |
| lxml | 6.1.3 | BSD-3-Clause |
| Send2Trash | 2.1.0 | BSD-3-Clause |
| Pillow | 13.0.0 | MIT-CMU |
| pillow-heif | 1.8.1 | 위 GPL·LGPL 표 참고 |
| imageio-ffmpeg | 0.6.0 | BSD-2-Clause(넣어 주는 FFmpeg는 위 표) |
| onnxruntime | 1.31.0 | MIT(ThirdPartyNotices 포함) |
| opencv-python-headless | 5.0.0.93 | Apache-2.0(OpenCV와 함께 들어간 라이브러리는 원문 파일에 포함) |
| tokenizers | 0.23.3 | Apache-2.0 |
| numpy | 2.5.4 | BSD-3-Clause 외(OpenBLAS 등 함께 들어간 라이브러리는 원문 파일에 포함) |
| packaging, typing_extensions, PyYAML, charset-normalizer, cffi, pycparser, colorama, setuptools | | Apache-2.0·BSD, PSF-2.0, MIT, MIT, MIT-0, BSD-3-Clause, BSD-3-Clause, MIT |

## 처음 쓸 때 받는 모델 (설치 프로그램에는 없음)

사진·동영상 인식을 켜고 [모델 받기]를 누를 때 아래 출처에서 받습니다. 라이선스는 각 출처를 따릅니다.

| 모델 | 받는 곳 |
|---|---|
| 종류 인식(CLIP ViT-B/32, 양자화 ONNX) | https://huggingface.co/Xenova/clip-vit-base-patch32 |
| 얼굴 찾기(YuNet) | https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet |
| 같은 사람 찾기(SFace) | https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface |

'내 분류 모델'은 사용자가 고른 파일이라 이 저장소에 들어 있지 않습니다.
