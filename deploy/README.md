# 단일 EC2 배포 — 런북

한 대(`g6.xlarge` · `ap-northeast-2`)에 API·프런트·프록시를 전부 올린다.
로컬 생성 모델을 그 GPU 에서 돌리므로 **외부로 나가는 호출이 없다** — ADR-004 가
노린 국내 리전 원칙이 문구가 아니라 실제로 충족되는 구성이다.

```
Internet :80
   └─ nginx (172.28.0.10)
        ├─ /api/v1, /healthz, /docs  →  api:10000   FastAPI + Qwen3.5-4B (CUDA)
        └─ /                          →  web:3000    Next.js 15 (standalone)
```

프런트와 API 가 같은 오리진에서 나오므로 CORS 가 발생하지 않고,
`NEXT_PUBLIC_API_BASE` 는 상대 경로 `/api/v1` 로 끝난다.

## 파일

| 파일 | 역할 |
|---|---|
| `provision-aws.sh` | EC2·보안그룹·키페어 생성. **기본은 계획만 출력**하고 `--apply` 로만 만든다 |
| `bootstrap.sh` | 인스턴스에서 1회. 드라이버 확인 → 툴킷 → 인덱스 → 가중치 → 기동 |
| `docker-compose.yml` | api(GPU)·web·nginx 3서비스 |
| `nginx.conf` | 리버스 프록시. SSE·타임아웃·X-Forwarded-For |
| `../apps/api/Dockerfile.gpu` | `requirements-gpu.txt` 를 쓰는 API 이미지 |
| `../apps/web/Dockerfile` | Next.js standalone |

## 절차

```bash
# 0) 자격증명 (아직 이 머신에 없다)
aws configure                      # 리전 ap-northeast-2

# 1) 인스턴스 — 먼저 계획만 본다
bash deploy/provision-aws.sh
bash deploy/provision-aws.sh --apply

# 2) 인스턴스에서
ssh -i ~/.ssh/kbuddy-key.pem ubuntu@<IP>
git clone <repo> kbuddy && cd kbuddy
sudo bash deploy/bootstrap.sh      # 40~60분 (이미지 빌드 + 가중치)

# 3) 확인
curl -s localhost/healthz | python3 -m json.tool
#   {"status":"ok","llm_configured":true,"index_present":true}
```

## 왜 8.43GB 를 업로드하지 않는가

인덱스도 가중치도 **리포에 있는 것만으로 인스턴스에서 재생성된다.**

```
corpus/processed/*.md   (git 추적)  → 03_chunk.py → 04_index.py → apps/api/data/
Qwen/Qwen3.5-4B         (HF 허브)   → export_local_model.py --stage textonly
```

`apps/api/data/` 와 `models/` 는 gitignore 라 새 체크아웃에 없다. `bootstrap.sh`
④⑤ 가 이것을 만든다. 가정용 업링크로 8.43GB 를 올리는 것보다 EC2 가 HF 에서
받는 편이 훨씬 빠르다.

**순서가 고정되어 있다**: 인덱스 → API 이미지 → 가중치. GPU 이미지가
`apps/api/data/` 를 COPY 하고, 가중치 산출이 그 이미지의 torch 를 빌려 쓰기
때문이다. `bootstrap.sh` 가 이 순서를 강제한다.

## 이 배포에서 새로 생긴 함정

### ① 프록시 뒤에서 레이트리밋이 무력해진다

`slowapi` 의 `get_remote_address` 는 `request.client.host` 를 본다. nginx 를
앞에 두면 그 값이 프록시 IP 로 고정돼 **전 이용자가 분당 20회 한 버킷을
공유**한다 — planner §9.1 의 "IP당" 이 조용히 깨진다.

대응은 두 곳이 **쌍으로** 맞아야 한다:

- `nginx.conf` 가 `X-Forwarded-For` 를 넘긴다
- `Dockerfile.gpu` 의 uvicorn 이 `--proxy-headers --forwarded-allow-ips` 를 켠다
- `docker-compose.yml` 이 그 값에 nginx 의 **고정 IP(172.28.0.10)** 를 준다

`--forwarded-allow-ips=*` 로 두면 안 된다. 아무나 헤더를 위조해 한도를
우회한다. 그래서 nginx 에 고정 IP 를 박았다 — 한쪽만 바꾸면 되돌아간다.

### ② `proxy_buffering off` 가 SSE 의 전제다

nginx 기본값이면 응답을 모아 두었다가 한 번에 내보내, `/api/v1/chat` 의 토큰
스트리밍이 통째로 지연된다. 화면에는 "멈춰 있다가 답이 한꺼번에 나오는"
형태로 보이고 `ttft_ms` 계측이 무의미해진다.

### ③ 타임아웃을 실측에 맞췄다

nginx 기본 60초면 정상 답변이 502 로 끊긴다. `exp_012` 의 생성 지연 p95 는
149초였고 `upstream_error` 문항은 p50 156초였다(ADR-005 후속 ⑨). 300초를 준다.
**지연 자체는 미해결 과제이고, 이 값은 그것을 감추는 것이지 고치는 것이 아니다.**

### ④ 빌드 타임에 GPU 가 없다

`Dockerfile.gpu` 가 임베딩 모델을 구울 때 `CUDA_VISIBLE_DEVICES=""` 를 준다.
fastembed 0.8 의 기본값이 `cuda=Device.AUTO` 라, 비워 두지 않으면 GPU 없는
빌드 컨테이너에서 CUDA 세션을 만들려다 죽는다.

### ⑤ `pip uninstall onnxruntime` 를 빠뜨리면 임베딩이 조용히 CPU 로 돈다

`requirements-gpu.txt` 헤더가 적어 둔 2단계다. 이미지가 그것을 그대로 한다.
에러가 나지 않고 32배 느려지기만 하므로 **로그로는 발견되지 않는다.**

## 비용

- `g6.xlarge` 온디맨드 시간당 대략 $1. 상시 가동은 월 수십만원이다.
  정확한 값은 [요금 페이지](https://aws.amazon.com/ko/ec2/pricing/on-demand/)에서 확인한다.
- **쓰지 않을 때는 중지(stop)** 한다. 인스턴스 시간 과금이 멈추고 EBS 만 남는다.
- 종료(terminate)하면 가중치·인덱스가 사라져 `bootstrap.sh` 를 처음부터 다시 돈다.
- 재시작하면 퍼블릭 IP 가 바뀐다. 심사용 고정 주소가 필요하면 Elastic IP.

## 아직 안 한 것

- **ADR-005 §6 의 "AWS 1회 검증"** — 개발 GPU 는 RTX 5060 Ti(sm_120)이고 배포
  타깃은 sm_86(g5)/sm_89(g6)다. 커널 경로가 갈릴 수 있다(ONNX 양자화 그래프의
  fused `Attention` 이 sm_120 에서 실패한 사례가 있다). **인스턴스에서
  `exp_011` 을 재현해 같은 산출물이 나오는지 확인해야 한다** — 이것이
  이 배포의 실질적 수용 기준이다.
- HTTPS. 지금은 80 만 연다. 도메인이 정해지면 certbot 또는 ALB.
- 지연 예산(p95 6초) 미달은 그대로다. 이 배포가 고치는 문제가 아니다.
