# K-Buddy API
#
# Python 은 로컬(3.12.3)과 핀을 일치시킨다 — "로컬에선 됐는데 배포에서 안 되는"
# 사고를 제거하기 위해서다. 휠 호환은 dev-log 2026-08-11 에서 확인했다.
FROM python:3.12-slim

# WeasyPrint 시스템 의존성 + 다국어 폰트.
# M1 에서 PDF 를 쓰지 않지만 지금 넣는다 — 이 누락이 이 프로젝트에서 가장 흔한
# 배포 사고이고(planner §15.4), 6주 차에 처음 마주치면 늦는다.
# macOS 로컬에서는 이 라이브러리들이 없어 `import weasyprint` 가 실패하는 것이 정상이다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 \
        libpangoft2-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        libffi8 \
        shared-mime-info \
        fonts-noto-core \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    # 임베딩 모델 캐시를 이미지 안에 고정한다. 런타임 다운로드는 콜드스타트를
    # 악화시키므로 금지 (planner §15.3)
    HF_HOME=/app/.cache/huggingface

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 인덱스는 빌드 타임에 만들어 동봉한다 (Chroma + BM25).
# 없으면 /healthz 의 index_present 가 false 로 뜨고 검색이 폴백된다.
COPY data/ ./data/
COPY app/ ./app/

EXPOSE 10000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "10000"]
