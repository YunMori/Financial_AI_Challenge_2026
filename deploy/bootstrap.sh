#!/usr/bin/env bash
#
# K-Buddy — EC2 인스턴스 초기 구성 (Ubuntu 22.04/24.04 · NVIDIA 드라이버 포함 AMI)
#
#   sudo bash deploy/bootstrap.sh            # GPU (기본)
#   sudo MODE=cpu bash deploy/bootstrap.sh   # CPU 스테이징 (쿼터 승인 전)
#
# MODE=cpu 는 드라이버·툴킷·가중치 단계를 통째로 건너뛰고 생성을 Anthropic API
# 로 보낸다. 배선(nginx·SSE·레이트리밋·인덱스)을 시간당 $1 장비가 아니라
# 시간당 몇 원짜리에서 먼저 검증하기 위한 임시 경로다 — deploy/README.md 참조.
#
# 멱등하게 짰다. 중간에 끊기면 그대로 다시 돌리면 된다 — 이미 끝난 단계는
# 건너뛴다. 각 단계가 **왜** 필요한지는 해당 위치 주석에 있다.
#
# ★ 이 스크립트는 8.43GB 가중치를 업로드하지 않는다. 인덱스도 가중치도
#   리포에 있는 것만으로 인스턴스에서 재생성한다:
#     corpus/processed/*.md (git 추적) → 03_chunk → 04_index → apps/api/data/
#     Qwen/Qwen3.5-4B (HF)             → export_local_model --stage textonly
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODELS_HOST_DIR=/opt/kbuddy/models
MODE="${MODE:-gpu}"
case "$MODE" in
    gpu) COMPOSE_FILE=docker-compose.yml ;;
    cpu) COMPOSE_FILE=docker-compose.cpu.yml ;;
    *)   echo "MODE 는 gpu 또는 cpu 다 (받은 값: $MODE)" >&2; exit 1 ;;
esac
echo "모드: $MODE  ($COMPOSE_FILE)"
STEP=0
step() { STEP=$((STEP+1)); printf '\n\033[1m[%d] %s\033[0m\n' "$STEP" "$*"; }

# ── 1. GPU 드라이버 ──────────────────────────────────────────────────
# 드라이버는 이 스크립트가 설치하지 않는다. AMI 가 가진 것을 쓴다 —
# 드라이버 설치는 재부팅과 커널 모듈이 얽혀 있어, 실패하면 원인이 배포
# 스크립트인지 AMI 인지 구분되지 않는다. DLAMI 를 쓰라는 이유가 이것이다.
if [[ "$MODE" == "gpu" ]]; then
step "NVIDIA 드라이버 확인"
if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "✗ nvidia-smi 가 없다. GPU 드라이버가 포함된 AMI(Deep Learning Base OSS" >&2
    echo "  Nvidia Driver GPU AMI)로 인스턴스를 다시 띄우는 편이 빠르다." >&2
    exit 1
fi
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

# ── 2. Docker + nvidia-container-toolkit ─────────────────────────────
step "Docker / nvidia-container-toolkit"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
fi
if ! docker info 2>/dev/null | grep -q "Runtimes:.*nvidia"; then
    # 컨테이너에서 GPU 를 보려면 이것이 있어야 한다. 없으면 `--gpus all` 이
    # "could not select device driver" 로 죽는다.
    curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
    curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
    apt-get update && apt-get install -y nvidia-container-toolkit
    nvidia-ctk runtime configure --runtime=docker
    systemctl restart docker
fi
# 컨테이너가 실제로 GPU 를 보는지 **여기서** 확인한다. 뒤로 미루면 이미지를
# 20분 빌드한 뒤에 알게 된다.
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu22.04 nvidia-smi -L
else
step "Docker (CPU 모드 — 드라이버·툴킷 건너뜀)"
command -v docker >/dev/null 2>&1 || curl -fsSL https://get.docker.com | sh
fi

# ── 3. .env ──────────────────────────────────────────────────────────
step ".env 확인"
if [[ ! -f "$REPO_ROOT/.env" ]]; then
    cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
    echo "⚠ .env 를 .env.example 에서 만들었다."
fi
if [[ "$MODE" == "cpu" ]] && ! grep -qE '^ANTHROPIC_API_KEY=.+' "$REPO_ROOT/.env"; then
    # CPU 모드는 생성을 Anthropic 으로 보내므로 키가 **반드시** 있어야 한다.
    # 없으면 기동은 되지만 모든 답변이 폴백으로 떨어져, 배선 검증이라는
    # 목적 자체가 무의미해진다. 여기서 멈추는 편이 낫다.
    echo "✗ MODE=cpu 인데 .env 에 ANTHROPIC_API_KEY 가 비어 있다." >&2
    echo "  이 경로는 생성을 API 로 보내므로 키가 없으면 전부 폴백된다." >&2
    exit 1
fi

# ── 4. 검색 인덱스 재생성 ────────────────────────────────────────────
# `apps/api/data/` 는 gitignore 라 새 체크아웃에 없다. GPU 이미지가 이것을
# COPY 하므로 **빌드보다 먼저** 만들어야 한다. 없으면 /healthz 의
# index_present 가 false 로 뜨고 검색이 통째로 폴백된다.
#
# 호스트에 파이썬 의존성을 깔지 않는다 — 리포에 핀이 있는데 호스트 환경을
# 따로 만들면 버전이 갈릴 자리가 하나 더 생긴다. 일회용 컨테이너로 돌린다.
step "검색 인덱스 (Chroma + BM25)"
if [[ -d "$REPO_ROOT/apps/api/data/chroma" && -f "$REPO_ROOT/apps/api/data/bm25.pkl" ]]; then
    echo "이미 있다 — 건너뛴다. 다시 만들려면 apps/api/data/ 를 지운다."
else
    docker run --rm \
        -v "$REPO_ROOT:/repo" -w /repo \
        -e HF_HOME=/repo/.cache/huggingface \
        python:3.12-slim bash -c '
            set -e
            pip install --no-cache-dir -q -r apps/api/requirements.txt
            python corpus/scripts/03_chunk.py
            python corpus/scripts/04_index.py --rebuild
        '
fi

# ── 5. 로컬 생성 모델 가중치 ─────────────────────────────────────────
# `--stage textonly` 는 비전 타워(0.667GB)와 MTP 헤드(0.241GB)를 떼어 낸다.
# 무손실이다 — 2026-08-18 실측 로짓 최대차 0, 로드 28 → 17초.
# meta device 로 조립하므로 GPU 없이 돈다.
if [[ "$MODE" == "gpu" ]]; then
step "가중치 (Qwen3.5-4B → textonly 8.43GB)"
mkdir -p "$MODELS_HOST_DIR"
if [[ -f "$MODELS_HOST_DIR/qwen35-4b-textonly/config.json" ]]; then
    echo "이미 있다 — 건너뛴다."
else
    # 산출에 torch·transformers 가 필요하므로 API 이미지를 빌려 쓴다. 이미지가
    # 인덱스(④)를 COPY 하므로 **순서가 이렇게 고정된다**: 인덱스 → 이미지 → 가중치.
    if ! docker image inspect kbuddy-api:gpu >/dev/null 2>&1; then
        echo "kbuddy-api:gpu 를 먼저 빌드한다 (10~20분)"
        docker compose -f "$REPO_ROOT/deploy/docker-compose.yml" build api
    fi
    # 여기서만 HF 허브를 탄다. 런타임 컨테이너는 HF_HUB_OFFLINE=1 이다.
    docker run --rm \
        -v "$REPO_ROOT:/repo" -v "$MODELS_HOST_DIR:/out" -w /repo \
        -e HF_HOME=/repo/.cache/huggingface \
        kbuddy-api:gpu \
        python apps/api/scripts/export_local_model.py \
               --stage textonly --out /out/qwen35-4b-textonly
fi
du -sh "$MODELS_HOST_DIR"/* 2>/dev/null || true
fi

# ── 6. 기동 ──────────────────────────────────────────────────────────
step "컨테이너 기동"
cd "$REPO_ROOT/deploy"
docker compose -f "$COMPOSE_FILE" up -d --build

echo
echo "── 확인 ───────────────────────────────────────────────────────"
echo "  curl -s localhost/healthz | python3 -m json.tool"
echo "  docker compose -f $REPO_ROOT/deploy/$COMPOSE_FILE logs -f api"
echo
echo "  index_present 와 llm_configured 가 둘 다 true 여야 한다."
