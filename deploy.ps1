# 클론 직후 이 스크립트 하나로 전체 스택을 띄운다 (Windows 호스트 PC, Docker Desktop 기준).
# Linux/macOS 용 deploy.sh 와 동작·옵션을 맞췄다. 더블클릭으로 실행하려면 deploy.bat 를 쓴다
# (deploy.bat 가 실행 정책 우회까지 포함해서 이 파일을 호출한다).
#
#   .\deploy.ps1                 # 소스 빌드 + 기동 (기본). GPU 는 자동 감지한다.
#   .\deploy.ps1 -Hub             # 빌드하지 않고 Docker Hub 이미지를 pull 해서 기동
#   .\deploy.ps1 -ResetData      # 볼륨까지 삭제하고 처음부터
#   .\deploy.ps1 -NoFirewall     # 방화벽 포트 개방을 건너뛴다
#   .\deploy.ps1 -NoOps          # 모니터링 스택 없이 앱만
#
# 두 모드 다 순서·옵션은 같고 다른 건 딱 하나 -- 이미지를 이 PC 에서 직접 빌드하느냐
# (기본, 5~15분·소스 최신 상태 반영) Docker Hub 에서 받기만 하느냐(-Hub, 몇 분·빌드 PC 가
# 미리 올려둔 버전)다. 둘 중 뭘 쓸지 모르겠으면 기본값(빌드)을 쓰면 된다.
param(
    [switch]$Hub,
    [switch]$ResetData,
    [switch]$NoFirewall,
    [switch]$NoOps
)

$ErrorActionPreference = "Stop"

Write-Host "====================================="
if ($Hub) {
    Write-Host "LawGraphRAG 배포 (Docker Hub 이미지, Windows)"
} else {
    Write-Host "LawGraphRAG 빌드 및 실행 (소스 빌드, Windows)"
}
Write-Host "====================================="

# ---------------------------------------------------------------- 0. 사전 점검
Write-Host "[0/5] 실행 환경 점검..."

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Host "  x Docker 가 설치돼 있지 않습니다." -ForegroundColor Red
    Write-Host "    설치:  winget install Docker.DockerDesktop"
    Write-Host "    또는:  https://www.docker.com/products/docker-desktop/ 에서 다운로드"
    Write-Host "    설치 후 Docker Desktop 을 한 번 실행해 초기화를 마친 뒤 이 스크립트를 다시 실행하세요."
    exit 1
}

docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  x docker compose 플러그인을 찾을 수 없습니다. Docker Desktop 을 최신 버전으로 업데이트하세요." -ForegroundColor Red
    exit 1
}

# Docker Desktop 이 꺼져 있으면 여기서 걸린다.
docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  x Docker 데몬에 접속할 수 없습니다. Docker Desktop 을 실행한 뒤 다시 시도하세요." -ForegroundColor Red
    exit 1
}
$dockerVersion = docker version --format "{{.Server.Version}}" 2>$null
Write-Host "  v docker $dockerVersion"

# GPU 예약은 오버레이로 분리해 뒀다. NVIDIA 런타임이 없는 호스트에 예약을 걸면
# 컨테이너 생성 자체가 실패하므로, 여기서 판단해서 붙일지 말지 정한다.
# -Hub 모드에서도 똑같이 자동 감지해서 cpu/cuda 태그를 고른다.
# ("\n" 대신 {{println}} 을 쓰는 이유: PowerShell 이 --format 문자열의 이스케이프를
#  bash 와 다르게 다뤄서 "\n" 그대로 넘기면 Go 템플릿 파싱 에러가 난다.)
if ($Hub) {
    $appFiles = @("-f", "docker-compose.hub.yml")
    $opsFiles = @("-f", "docker-compose.ops.hub.yml")
} else {
    $appFiles = @("-f", "docker-compose.yml")
    $opsFiles = @("-f", "docker-compose.ops.yml")
}
$runtimeFmt = '{{range $k, $v := .Runtimes}}{{$k}}{{println}}{{end}}'
$runtimes = docker info --format $runtimeFmt 2>$null
if ($runtimes -split "`n" | Where-Object { $_.Trim() -eq "nvidia" }) {
    $appFiles += @("-f", "docker-compose.gpu.yml")
    $env:TORCH_VARIANT = "cuda"
    $env:API_TAG = "cuda"
    Write-Host "  v NVIDIA 런타임 감지 -- GPU 가속을 켭니다(CUDA 이미지, 약 8.8GB)." -ForegroundColor Green
} else {
    # 이걸 안 하면 compose 기본값(cuda)으로 빌드/pull 되어, GPU 도 없는 호스트에서
    # 8.8GB 짜리 CUDA 이미지를 받느라 몇십 분을 버린다. CPU 이미지는 약 2.3GB.
    $env:TORCH_VARIANT = "cpu"
    $env:API_TAG = "cpu"
    Write-Host "  · NVIDIA 런타임 없음 -- CPU 이미지를 씁니다(약 2.3GB, 기능 동일ㆍ임베딩만 느림)."
}

# ---------------------------------------------------------------- 1. 환경변수
Write-Host "[1/5] 환경 설정 확인..."

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "  > .env 를 .env.example 에서 만들었습니다."
}
if (-not (Test-Path ".env.ops")) {
    Copy-Item ".env.ops.example" ".env.ops"
    Write-Host "  > .env.ops 를 .env.ops.example 에서 만들었습니다."
}

function Set-EnvValue([string]$Path, [string]$Placeholder, [string]$Key) {
    $content = Get-Content $Path -Raw
    if ($content -notmatch [regex]::Escape($Placeholder)) { return }
    Write-Host ""
    Write-Host "[필수] $Key 가 비어 있습니다." -ForegroundColor Yellow
    $value = Read-Host "  $Key 입력 (Enter 로 건너뛰기)"
    if ($value) {
        $content = $content -replace [regex]::Escape($Placeholder), "$Key=$value"
        Set-Content $Path $content -NoNewline -Encoding utf8
        Write-Host "  저장했습니다." -ForegroundColor Green
    } else {
        Write-Host "  비워두면 관련 기능이 동작하지 않습니다." -ForegroundColor Yellow
    }
}

Write-Host "  법제처 Open API 키 발급: https://open.law.go.kr"
Set-EnvValue ".env" "LAW_OC_KEY=your_law_open_api_key_here" "LAW_OC_KEY"
Write-Host "  Gemini API 키 발급: https://aistudio.google.com/apikey"
Set-EnvValue ".env" "GEMINI_API_KEY=your_gemini_api_key_here" "GEMINI_API_KEY"

if (-not $NoOps) {
    $opsContent = Get-Content ".env.ops" -Raw
    if ($opsContent -match "SLACK_WEBHOOK_URL=your_slack_webhook_url_here" -or $opsContent -match "(?m)^SLACK_WEBHOOK_URL=$") {
        Write-Host ""
        Write-Host "[선택] 모니터링 알림용 Slack Webhook URL (Enter 로 건너뛰면 Slack 알림만 끕니다)."
        $slack = Read-Host "  SLACK_WEBHOOK_URL 입력"
        $opsContent = $opsContent -replace "SLACK_WEBHOOK_URL=.*", "SLACK_WEBHOOK_URL=$slack"
        Set-Content ".env.ops" $opsContent -NoNewline -Encoding utf8
        if ($slack) { Write-Host "  저장했습니다." -ForegroundColor Green } else { Write-Host "  Slack 알림을 끕니다." }
    }
}

# ---------------------------------------------------------------- 2. 방화벽
# ops 스택(8900/3001/9091/9093)은 127.0.0.1 에만 바인딩돼 있어 방화벽을 열 필요가 없다.
# 이 PC 에서만 쓸 거면 이 단계 자체가 의미 없다(로컬 접속은 방화벽과 무관하다).
if (-not $NoFirewall) {
    Write-Host "[2/5] Windows 방화벽 확인..."
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if ($isAdmin) {
        $existing = Get-NetFirewallRule -DisplayName "LawGraphRAG-HTTP" -ErrorAction SilentlyContinue
        if (-not $existing) {
            New-NetFirewallRule -DisplayName "LawGraphRAG-HTTP" -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow | Out-Null
            Write-Host "  v 인바운드 80/tcp 규칙을 추가했습니다." -ForegroundColor Green
        } else {
            Write-Host "  v 80/tcp 규칙이 이미 있습니다."
        }
    } else {
        Write-Host "  ! 관리자 권한이 아니라 방화벽 규칙을 추가하지 못했습니다." -ForegroundColor Yellow
        Write-Host "    이 PC 에서만 쓸 거면 상관없습니다(로컬 접속은 방화벽과 무관합니다)."
        Write-Host "    다른 PC 에서 접속하게 하려면 관리자 PowerShell 에서:"
        Write-Host "      New-NetFirewallRule -DisplayName 'LawGraphRAG-HTTP' -Direction Inbound -LocalPort 80 -Protocol TCP -Action Allow"
    }
} else {
    Write-Host "[2/5] 방화벽 설정 건너뜀(-NoFirewall)."
}

# ---------------------------------------------------------------- 3. 정리
Write-Host "[3/5] 기존 컨테이너 정리..."
$downOpt = @()
if ($ResetData) {
    Write-Host "경고: -ResetData 로 수집한 법령·그래프 데이터가 모두 삭제됩니다." -ForegroundColor Red
    $confirm = Read-Host "정말 삭제할까요? (y/N)"
    if ($confirm -notmatch "^[Yy]$") { Write-Host "취소했습니다."; exit 1 }
    $downOpt = @("-v")
    Write-Host "볼륨까지 삭제합니다." -ForegroundColor Red
} else {
    Write-Host "데이터 볼륨은 보존합니다." -ForegroundColor Green
}
# 두 스택은 compose 프로젝트가 다르다(lawgraphrag / lawgraphrag-ops).
# 한 명령에 -f 로 묶으면 하나의 프로젝트로 병합돼 엉뚱한 것을 내리므로 따로 내린다.
try { docker compose @appFiles down @downOpt } catch {}
if (-not $NoOps) {
    try { docker compose @opsFiles --env-file .env.ops down @downOpt } catch {}
}

# ---------------------------------------------------------------- 4. 앱
if ($Hub) {
    Write-Host "[4/5] 앱 스택 이미지 pull 및 기동..."
    docker compose @appFiles pull
    if ($LASTEXITCODE -ne 0) { Write-Error "pull 실패"; exit 1 }
    docker compose @appFiles up -d
} else {
    Write-Host "[4/5] 앱 스택 빌드 및 기동..."
    docker compose @appFiles up --build -d
}
if ($LASTEXITCODE -ne 0) { Write-Error "앱 스택 기동 실패"; exit 1 }

# ---------------------------------------------------------------- 5. 모니터링
if (-not $NoOps) {
    if ($Hub) {
        Write-Host "[5/5] 모니터링 스택 이미지 pull 및 기동..."
        docker compose @opsFiles --env-file .env.ops pull
        if ($LASTEXITCODE -ne 0) { Write-Error "ops pull 실패"; exit 1 }
        docker compose @opsFiles --env-file .env.ops up -d
    } else {
        Write-Host "[5/5] 모니터링 스택 빌드 및 기동..."
        docker compose @opsFiles --env-file .env.ops up --build -d
    }
    if ($LASTEXITCODE -ne 0) { Write-Error "모니터링 스택 기동 실패"; exit 1 }
} else {
    Write-Host "[5/5] 모니터링 스택 건너뜀(-NoOps)."
}

Write-Host ""
Write-Host "====================================="
Write-Host "완료. 컨테이너 상태는 'docker ps' 로 확인하세요." -ForegroundColor Green
Write-Host ""
Write-Host "  앱   http://localhost"
Write-Host ""
Write-Host "  아래는 127.0.0.1 에만 열려 있어 이 PC 안에서만 보입니다(의도된 것입니다)."
Write-Host "  api (직접)     http://localhost:8000"
Write-Host "  Neo4j          http://localhost:7474"
if (-not $NoOps) {
Write-Host "  운영 대시보드  http://localhost:8900"
Write-Host "  Grafana        http://localhost:3001"
}
Write-Host ""
Write-Host "  다른 PC 에서 이 PC 로 접속하게 하려면 위 [2/5] 안내대로 80 을 열고,"
Write-Host "  아래 이 PC 의 IP 중 실제 LAN 주소로 접속합니다:"
Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -ne "127.0.0.1" -and $_.PrefixOrigin -ne "WellKnown" } |
    ForEach-Object { Write-Host "    $($_.IPAddress)  ($($_.InterfaceAlias))" }
Write-Host ""
Write-Host "다음 단계 -- DB 는 비어 있으므로 법령 데이터를 수집해야 합니다:"
Write-Host "  docker compose $($appFiles -join ' ') exec api python -m app.ingest --all"
Write-Host "====================================="
