# 다국어 PDF 조판 선행 검증 (planner §12.1)

WeasyPrint 는 **macOS 로컬에서 import 자체가 실패한다**(libpango/libgobject 없음).
그래서 조판은 컨테이너 안에서만 확인할 수 있다.

앱 이미지는 임베딩 모델 캐시를 포함해 5GB 라 조판 하나 보려고 다시 만들 것이
못 된다. 여기 있는 최소 이미지는 `apps/api/Dockerfile` 과 **같은 조판
의존성·폰트만** 뽑아 와 질문을 격리한다.

```bash
cd apps/api/scripts/typeset
docker build -t kb-typeset . && docker run --rm kb-typeset
```

## 무엇을 보는가

1. **베트남어 성조 결합문자** — planner §12.2 가 "확인 필수"로 못박은 항목.
   `ề ạ ế ộ ữ` 가 두부로 깨지거나 결합이 풀려 따로 찍히는지.
2. **한글 완성형** — CJK 폰트가 실제로 잡히는지.
3. **폰트 폴백** — 세 언어가 섞였을 때 글자마다 맞는 폰트가 선택되는지.

## ★ 텍스트 비교만으로 검증하면 안 된다

두부(tofu)로 찍혀도 **코드포인트는 그대로 추출된다.** 텍스트만 대조하면
글자가 다 깨진 PDF 가 통과한다. 그래서 `pdfplumber` 로 **글자별 폰트명**을
뽑아 한글이 CJK 폰트에, 베트남어가 라틴 폰트에 각각 걸렸는지 대조한다.

## 마지막 결과 (2026-08-12)

통과. 한글 → `Noto-Sans-CJK-KR`, 베트남어 확장 → `Noto-Sans`,
결합기호가 별도 글자로 찍히지 않음, 폰트·글리프 경고 0건.
→ planner §12.2 의 `@font-face` + `unicode-range` CSS 는 불필요하다
(`docs/spec-changes.md` #25).
