# K-Buddy API
#
# Python 은 로컬(3.12.3)과 핀을 일치시킨다 — "로컬에선 됐는데 배포에서 안 되는"
# 사고를 제거하기 위해서다. 휠 호환은 dev-log 2026-08-11 에서 확인했다.
#
# 빌드 컨텍스트는 **리포 루트**다 (corpus/ 를 함께 담아야 하므로):
#   docker build -f apps/api/Dockerfile -t kbuddy-api .
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

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PIP_NO_CACHE_DIR=1
# 임베딩 모델 캐시를 이미지 안에 고정한다.
# 런타임 다운로드는 콜드스타트를 악화시키므로 금지 (planner §15.3).
ENV HF_HOME=/app/.cache/huggingface
ENV EMBED_MODEL=intfloat/multilingual-e5-large

# ★ **두 파일을 함께 복사한다.** requirements.txt 는 `-r requirements-base.txt` 로
#   시작하므로 base 가 없으면 빌드가 여기서 죽는다 (ADR-005 후속, 2026-08-19).
COPY apps/api/requirements-base.txt apps/api/requirements.txt ./
# CPU 파일을 쓴다. 이 이미지는 GPU 런타임(`--gpus all`)을 전제하지 않는다 —
# GPU 배포로 갈 때 requirements-gpu.txt 로 바꾸고 nvidia-container-toolkit 을 건다.
RUN pip install --no-cache-dir -r requirements.txt

# ★ 임베딩 모델을 빌드 타임에 내려받는다. 이 줄이 없으면 첫 요청이
# 모델 다운로드(2.2GB)를 기다리고, 심사위원이 처음 접속했을 때 그 일이 벌어진다.
RUN python -c "from fastembed import TextEmbedding; \
    import os; TextEmbedding(model_name=os.environ['EMBED_MODEL'])"

# 인덱스는 빌드 타임에 만들어 동봉한다 (Chroma + BM25).
# 없으면 /healthz 의 index_present 가 false 로 뜨고 검색이 폴백된다.
COPY apps/api/data/ ./data/

# 용어 사전은 런타임에 읽는다(질의 정규화·프롬프트 주입).
# 빠뜨리면 첫 요청에서 SystemExit 가 난다 — 로컬에서는 리포 루트에서 찾히므로
# **배포에서만** 터지는 종류의 사고다.
COPY corpus/glossary/ ./corpus/glossary/

COPY apps/api/app/ ./app/

# 배포 환경이 PORT 를 주입하면 그것을 쓴다. 없으면 10000.
ENV PORT=10000
EXPOSE 10000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
