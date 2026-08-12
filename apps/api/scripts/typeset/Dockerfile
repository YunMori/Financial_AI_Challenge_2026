FROM python:3.12-slim
# apps/api/Dockerfile 과 **같은** 조판 의존성·폰트만 뽑아 왔다.
# 5GB 앱 이미지를 다시 만들지 않고 조판 질문만 격리해 답한다.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libfribidi0 \
        fonts-noto-core fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir weasyprint==63.1 pdfplumber
COPY check_typeset.py /check_typeset.py
CMD ["python", "/check_typeset.py"]
