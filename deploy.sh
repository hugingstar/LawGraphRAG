#!/bin/bash
# 클론 직후 이 스크립트 하나로 전체 스택을 띄운다 (Linux/macOS — Rocky VM 포함).
#
#   ./deploy.sh                 # 소스 빌드 + 기동 (기본). GPU 는 자동 감지한다.
#   ./deploy.sh --hub           # 빌드하지 않고 Docker Hub 이미지를 pull 해서 기동
#   ./deploy.sh --reset-data    # 볼륨까지 삭제하고 처음부터
#   ./deploy.sh --no-firewall   # 방화벽 포트 개방을 건너뛴다
#   ./deploy.sh --no-ops        # 모니터링 스택 없이 앱만
#
# 두 모드 다 순서·옵션은 같고 다른 건 딱 하나 — 이미지를 이 컴퓨터에서 직접 빌드하느냐
# (기본, 5~15분·소스 최신 상태 반영) Docker Hub 에서 받기만 하느냐(--hub, 몇 분·빌드 PC 가
# 미리 올려둔 버전)다. 둘 중 뭘 쓸지 모르겠으면 기본값(빌드)을 쓰면 된다.
set -e

MODE=hub
RESET_DATA=false
OPEN_FIREWALL=true
WITH_OPS=true
for arg in "$@"; do
    case "$arg" in
        --build)       MODE=build ;;
        --reset-data)  RESET_DATA=maybe ;;
        --no-firewall) OPEN_FIREWALL=false ;;
        --no-ops)      WITH_OPS=false ;;
        *) echo "알 수 없는 옵션: $arg"; exit 1 ;;
    esac
done

echo "====================================="
if [ "$MODE" = hub ]; then
    echo "LawGraphRAG 배포 (Docker Hub 이미지)"
else
    echo "LawGraphRAG 빌드 및 실행 (소스 빌드)"
fi
echo "====================================="

# ---------------------------------------------------------------- 0. 사전 점검
echo "[0/5] 실행 환경 점검..."

command -v docker >/dev/null 2>&1 || {
    echo "  ✗ docker 가 없습니다. Rocky Linux 라면:"
    echo "      sudo dnf -y install dnf-plugins-core"
    echo "      sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo"
    echo "      sudo dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin"
    echo "      sudo systemctl enable --now docker"
    exit 1
}

docker compose version >/dev/null 2>&1 || {
    echo "  ✗ docker compose 플러그인이 없습니다: sudo dnf -y install docker-compose-plugin"
    exit 1
}

# 데몬에 붙을 수 있는지. 신규 설치 후 가장 흔한 실패 지점이다.
docker info >/dev/null 2>&1 || {
    echo "  ✗ Docker 데몬에 접속할 수 없습니다."
    echo "    데몬 기동:   sudo systemctl enable --now docker"
    echo "    권한 부여:   sudo usermod -aG docker \$USER   (그 뒤 로그아웃/로그인 또는 'newgrp docker')"
    exit 1
}
echo "  ✓ docker $(docker version --format '{{.Server.Version}}' 2>/dev/null)"

# GPU 예약은 오버레이로 분리해 뒀다. NVIDIA 런타임이 없는 호스트에 예약을 걸면
# 컨테이너 생성 자체가 실패하므로, 여기서 판단해서 붙일지 말지 정한다.
# --hub 모드에서도 똑같이 자동 감지해서 cpu/cuda 태그를 고른다 — 사람이 --gpu 를
# 따로 기억할 필요가 없게 했다.
if [ "$MODE" = hub ]; then
    APP_FILES="-f docker-compose.hub.yml"
    OPS_FILES="-f docker-compose.ops.hub.yml"
else
    APP_FILES="-f docker-compose.yml"
    OPS_FILES="-f docker-compose.ops.yml"
fi
# JSON 전체를 grep 하면 runc 의 features 문자열에 걸려 오탐할 수 있어, 런타임 키만 본다.
if docker info --format '{{range $k, $v := .Runtimes}}{{$k}}{{"
"}}{{end}}' 2>/dev/null | grep -qx nvidia; then
    APP_FILES="$APP_FILES -f docker-compose.gpu.yml"
    export TORCH_VARIANT=cuda API_TAG=cuda
    echo "  ✓ NVIDIA 런타임 감지 — GPU 가속을 켭니다(CUDA 이미지, 약 8.8GB)."
else
    # 이걸 안 하면 compose 기본값(cuda)으로 빌드/pull 되어, GPU 도 없는 호스트에서
    # 8.8GB 짜리 CUDA 이미지를 받느라 몇십 분을 버린다. CPU 이미지는 약 2.3GB.
    export TORCH_VARIANT=cpu API_TAG=cpu
    echo "  · NVIDIA 런타임 없음 — CPU 이미지를 씁니다(약 2.3GB, 기능 동일·임베딩만 느림)."
fi

if command -v getenforce >/dev/null 2>&1 && [ "$(getenforce)" = "Enforcing" ]; then
    echo "  · SELinux Enforcing — 설정 파일 마운트에 :z 라벨을 쓰도록 이미 맞춰져 있습니다."
fi

# ---------------------------------------------------------------- 1. 환경변수
echo "[1/5] 환경 설정 확인..."

[ -f .env ]     || { cp .env.example .env;         echo "  > .env 를 .env.example 에서 만들었습니다."; }
[ -f .env.ops ] || { cp .env.ops.example .env.ops; echo "  > .env.ops 를 .env.ops.example 에서 만들었습니다."; }

if grep -q "LAW_OC_KEY=your_law_open_api_key_here" .env; then
    echo ""
    echo "🔑 [필수] 법제처 Open API 키가 비어 있습니다."
    echo "  발급: https://open.law.go.kr"
    read -p "  LAW_OC_KEY 입력: " user_law_key || true
    if [ -n "$user_law_key" ]; then
        sed "s|LAW_OC_KEY=your_law_open_api_key_here|LAW_OC_KEY=$user_law_key|" .env > .env.tmp && mv .env.tmp .env
        echo "  ✅ 저장했습니다."
    else
        echo "  ⚠️  비워두면 법령 수집이 동작하지 않습니다."
    fi
fi

if grep -q "GEMINI_API_KEY=your_gemini_api_key_here" .env; then
    echo ""
    echo "🔑 [필수] Gemini API 키가 비어 있습니다."
    echo "  발급: https://aistudio.google.com/apikey"
    read -p "  GEMINI_API_KEY 입력: " user_gemini_key || true
    if [ -n "$user_gemini_key" ]; then
        sed "s|GEMINI_API_KEY=your_gemini_api_key_here|GEMINI_API_KEY=$user_gemini_key|" .env > .env.tmp && mv .env.tmp .env
        echo "  ✅ 저장했습니다."
    else
        echo "  ⚠️  비워두면 조문 분석이 빈손으로 끝납니다."
    fi
    echo ""
fi

if [ "$WITH_OPS" = true ] && { grep -q "SLACK_WEBHOOK_URL=your_slack_webhook_url_here" .env.ops || grep -q "^SLACK_WEBHOOK_URL=$" .env.ops; }; then
    echo ""
    echo "🚨 [선택] 모니터링 알림용 Slack Webhook URL."
    echo "  (그냥 Enter 를 누르면 Slack 알림만 끕니다)"
    read -p "  SLACK_WEBHOOK_URL 입력: " user_slack_url || true
    sed "s|SLACK_WEBHOOK_URL=.*|SLACK_WEBHOOK_URL=$user_slack_url|" .env.ops > .env.ops.tmp && mv .env.ops.tmp .env.ops
    if [ -n "$user_slack_url" ]; then echo "  ✅ 저장했습니다."; else echo "  ℹ️  Slack 알림을 끕니다."; fi
    echo ""
fi

# ---------------------------------------------------------------- 2. 방화벽
# ops 스택(8900/3001/9091/9093)은 127.0.0.1 에만 바인딩돼 있어 방화벽을 열 필요가 없다.
# 밖에서 보려면 SSH 터널을 쓴다 — README 참고.
if [ "$OPEN_FIREWALL" = true ] && command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld 2>/dev/null; then
    echo "[2/5] firewalld 포트 개방..."
    # nginx 80 이 외부 진입점이다. 8000 은 nginx 뒤에 있으므로 굳이 열지 않는다.
    # (Neo4j 브라우저를 밖에서 직접 쓰고 싶으면 7474/7687 을 여기에 추가한다)
    for port in ${FIREWALL_PORTS:-80}; do
        if sudo firewall-cmd --permanent --add-port=${port}/tcp >/dev/null 2>&1; then
            echo "  ✓ ${port}/tcp"
        else
            echo "  ⚠️  ${port}/tcp 개방 실패(sudo 권한 확인). 수동: sudo firewall-cmd --permanent --add-port=${port}/tcp"
        fi
    done
    sudo firewall-cmd --reload >/dev/null 2>&1 || true
else
    echo "[2/5] 방화벽 설정 건너뜀(firewalld 비활성 또는 --no-firewall)."
fi

# ---------------------------------------------------------------- 3. 정리
echo "[3/5] 기존 컨테이너 정리..."
if [ "$RESET_DATA" = maybe ]; then
    echo "⚠️  --reset-data: 수집한 법령·그래프 데이터가 모두 삭제됩니다."
    read -p "정말 삭제할까요? (y/N) " -n 1 -r || true; echo
    [[ $REPLY =~ ^[Yy]$ ]] || { echo "취소했습니다."; exit 1; }
    DOWN_OPT="-v"
    echo "🚨 볼륨까지 삭제합니다."
else
    DOWN_OPT=""
    echo "✅ 데이터 볼륨은 보존합니다."
fi
# 두 스택은 compose 프로젝트가 다르다(lawgraphrag / lawgraphrag-ops).
# 한 명령에 -f 로 묶으면 하나의 프로젝트로 병합돼 엉뚱한 것을 내리므로 따로 내린다.
docker compose $APP_FILES down $DOWN_OPT || true
[ "$WITH_OPS" = true ] && { docker compose $OPS_FILES --env-file .env.ops down $DOWN_OPT || true; }

# ---------------------------------------------------------------- 4. 앱
if [ "$MODE" = hub ]; then
    echo "[4/5] 앱 스택 이미지 pull 및 기동..."
    docker compose $APP_FILES pull
    docker compose $APP_FILES up -d
else
    echo "[4/5] 앱 스택 빌드 및 기동..."
    docker compose $APP_FILES up --build -d
fi

# ---------------------------------------------------------------- 5. 모니터링
if [ "$WITH_OPS" = true ]; then
    if [ "$MODE" = hub ]; then
        echo "[5/5] 모니터링 스택 이미지 pull 및 기동..."
        docker compose $OPS_FILES --env-file .env.ops pull
        docker compose $OPS_FILES --env-file .env.ops up -d
    else
        echo "[5/5] 모니터링 스택 빌드 및 기동..."
        docker compose $OPS_FILES --env-file .env.ops up --build -d
    fi
else
    echo "[5/5] 모니터링 스택 건너뜀(--no-ops)."
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP=localhost

echo ""
echo "====================================="
echo "완료. 컨테이너 상태는 'docker ps' 로 확인하세요."
echo ""
echo "  앱          http://$IP           <- 브라우저에서 이 주소로 접속"
echo ""
echo "  아래는 127.0.0.1 에만 열려 있어 이 컴퓨터 밖에서는 안 보입니다(의도된 것입니다)."
echo "  이 컴퓨터 안에서 직접 쓰거나, 원격에서 보려면 SSH 터널을 씁니다:"
echo "    ssh -L 8000:localhost:8000 -L 7474:localhost:7474 \\"
if [ "$WITH_OPS" = true ]; then
echo "       -L 8900:localhost:8900 -L 3001:localhost:3001 \\"
fi
echo "       사용자@$IP"
echo "  api (직접)     http://localhost:8000"
echo "  Neo4j          http://localhost:7474"
if [ "$WITH_OPS" = true ]; then
echo "  운영 대시보드  http://localhost:8900"
echo "  Grafana        http://localhost:3001"
fi
echo ""
echo "다음 단계 — DB 는 비어 있으므로 법령 데이터를 수집해야 합니다:"
echo "  docker compose $APP_FILES exec api python -m app.ingest --all"
echo "====================================="
