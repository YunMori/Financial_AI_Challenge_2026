#!/usr/bin/env bash
#
# K-Buddy — EC2 인스턴스 생성 (ap-northeast-2)
#
#   bash deploy/provision-aws.sh            # 계획만 출력한다 (아무것도 만들지 않음)
#   bash deploy/provision-aws.sh --apply    # 실제로 만든다
#
# ★ **과금이 시작된다.** g6.xlarge 온디맨드는 시간당 대략 $1 수준이고 상시
#   가동하면 월 수십만원이다. 정확한 값은 요금 페이지에서 확인한다:
#   https://aws.amazon.com/ko/ec2/pricing/on-demand/
#
#   쓰지 않을 때는 **중지(stop)** 한다. 중지하면 인스턴스 시간 과금이 멈추고
#   EBS 만 남는다(200GB gp3 ≈ 월 2만원대). 종료(terminate)하면 가중치와
#   인덱스까지 사라져 bootstrap 을 처음부터 다시 돌려야 한다.
set -euo pipefail

REGION="${REGION:-ap-northeast-2}"
INSTANCE_TYPE="${INSTANCE_TYPE:-g6.xlarge}"
VOLUME_GB="${VOLUME_GB:-200}"
NAME="${NAME:-kbuddy}"
KEY_NAME="${KEY_NAME:-kbuddy-key}"
APPLY=0
[[ "${1:-}" == "--apply" ]] && APPLY=1

run() {
    if [[ $APPLY -eq 1 ]]; then "$@"; else printf '  (계획) %s\n' "$*"; fi
}

echo "리전 $REGION · 타입 $INSTANCE_TYPE · 루트 ${VOLUME_GB}GB gp3"
aws sts get-caller-identity --output text --query 'Arn' \
    || { echo "✗ 자격증명이 없다. 먼저 'aws configure' 를 실행한다." >&2; exit 1; }

# ── AMI ──────────────────────────────────────────────────────────────
# 드라이버가 포함된 AMI 를 쓴다. bootstrap.sh 가 드라이버를 설치하지 않는
# 이유는 그 파일 주석에 있다 — 커널 모듈·재부팅이 얽혀 실패 원인이 흐려진다.
echo
echo "AMI 조회 (Deep Learning Base OSS Nvidia Driver GPU · Ubuntu 22.04)"
AMI_ID=$(aws ec2 describe-images --region "$REGION" --owners amazon \
    --filters "Name=name,Values=Deep Learning Base OSS Nvidia Driver GPU AMI (Ubuntu 22.04)*" \
              "Name=state,Values=available" \
    --query 'sort_by(Images,&CreationDate)[-1].[ImageId,Name]' --output text)
echo "  → $AMI_ID"
[[ "$AMI_ID" == "None" || -z "$AMI_ID" ]] && { echo "✗ AMI 를 찾지 못했다." >&2; exit 1; }
AMI_ID=$(echo "$AMI_ID" | awk '{print $1}')

# ── 보안 그룹 ────────────────────────────────────────────────────────
# 22 는 **내 IP 만** 연다. 0.0.0.0/0 으로 열어 두면 몇 분 안에 스캔이 붙는다.
MY_IP=$(curl -fsS https://checkip.amazonaws.com | tr -d '\n')
echo
echo "보안 그룹 — 80/tcp 전체, 22/tcp ${MY_IP}/32"
SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
    --filters "Name=group-name,Values=${NAME}-sg" \
    --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")
if [[ "$SG_ID" == "None" ]]; then
    if [[ $APPLY -eq 1 ]]; then
        SG_ID=$(aws ec2 create-security-group --region "$REGION" \
            --group-name "${NAME}-sg" --description "K-Buddy single-instance deploy" \
            --query GroupId --output text)
        aws ec2 authorize-security-group-ingress --region "$REGION" --group-id "$SG_ID" \
            --ip-permissions \
            "IpProtocol=tcp,FromPort=80,ToPort=80,IpRanges=[{CidrIp=0.0.0.0/0,Description=http}]" \
            "IpProtocol=tcp,FromPort=22,ToPort=22,IpRanges=[{CidrIp=${MY_IP}/32,Description=ssh-admin}]"
    else
        echo "  (계획) 보안 그룹 ${NAME}-sg 생성 + 규칙 2건"
    fi
else
    echo "  → 기존 $SG_ID 사용"
fi

# ── 키페어 ───────────────────────────────────────────────────────────
echo
if [[ -f "$HOME/.ssh/${KEY_NAME}.pem" ]]; then
    echo "키페어 → 기존 ~/.ssh/${KEY_NAME}.pem"
elif [[ $APPLY -eq 1 ]]; then
    aws ec2 create-key-pair --region "$REGION" --key-name "$KEY_NAME" \
        --query KeyMaterial --output text > "$HOME/.ssh/${KEY_NAME}.pem"
    chmod 400 "$HOME/.ssh/${KEY_NAME}.pem"
    echo "키페어 생성 → ~/.ssh/${KEY_NAME}.pem"
else
    echo "  (계획) 키페어 ${KEY_NAME} 생성 → ~/.ssh/${KEY_NAME}.pem"
fi

# ── 인스턴스 ─────────────────────────────────────────────────────────
# 루트 볼륨을 크게 잡는다. GPU 이미지(torch cu13 + onnxruntime-gpu + nvidia
# 런타임 ~1.5GB + 임베딩 모델 2.2GB)만으로 10GB 를 넘고, 가중치 8.43GB 와
# HF 캐시(원본 10GB)가 그 위에 얹힌다. 기본 8GB 로는 빌드 중에 디스크가 찬다.
echo
echo "인스턴스 생성 — $INSTANCE_TYPE / $AMI_ID / ${VOLUME_GB}GB"
if [[ $APPLY -eq 1 ]]; then
    IID=$(aws ec2 run-instances --region "$REGION" \
        --image-id "$AMI_ID" --instance-type "$INSTANCE_TYPE" \
        --key-name "$KEY_NAME" --security-group-ids "$SG_ID" \
        --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${VOLUME_GB},VolumeType=gp3,DeleteOnTermination=true}" \
        --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=${NAME}}]" \
        --query 'Instances[0].InstanceId' --output text)
    echo "  → $IID  (기동 대기)"
    aws ec2 wait instance-running --region "$REGION" --instance-ids "$IID"
    IP=$(aws ec2 describe-instances --region "$REGION" --instance-ids "$IID" \
        --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)
    cat <<EOF

  인스턴스 $IID · $IP

  다음:
    ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@$IP
    git clone <repo> kbuddy && cd kbuddy
    sudo bash deploy/bootstrap.sh

  중지(과금 정지):  aws ec2 stop-instances  --region $REGION --instance-ids $IID
  재시작:           aws ec2 start-instances --region $REGION --instance-ids $IID
                    (재시작하면 퍼블릭 IP 가 바뀐다 — 고정하려면 Elastic IP)
EOF
else
    echo "  (계획) run-instances"
    echo
    echo "실제로 만들려면: bash deploy/provision-aws.sh --apply"
fi
