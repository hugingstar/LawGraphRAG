# Docker Hub 로 이미지를 빌드해서 올린다 (빌드 PC 에서 실행).
#
#   .\docker-push.ps1            # cpu + cuda + ops 전부
#   .\docker-push.ps1 -Only cpu  # cpu 만
#   .\docker-push.ps1 -Only ops  # ops 만
#
# 올라가는 것:
#   yslee4050/lawowly:cpu     약 2.5GB  (GPU 없는 PC 용, latest 도 같이 가리킴)
#   yslee4050/lawowly:cuda    약 9GB    (NVIDIA GPU 있는 PC 용)
#   yslee4050/lawowly-ops:latest  약 300MB
param(
    [ValidateSet("all", "cpu", "cuda", "ops")]
    [string]$Only = "all"
)

$ErrorActionPreference = "Stop"

function Invoke-Step {
    param([string]$Label, [scriptblock]$Body)
    Write-Host ""
    Write-Host "=== $Label ===" -ForegroundColor Cyan
    & $Body
    if ($LASTEXITCODE -ne 0) {
        Write-Error "$Label 실패 (exit $LASTEXITCODE)"
        exit 1
    }
}

Write-Host "Docker Hub 로그인 상태를 확인합니다. 이미 로그인돼 있으면 그냥 넘어갑니다."
docker login
if ($LASTEXITCODE -ne 0) { Write-Error "docker login 실패"; exit 1 }

if ($Only -eq "all" -or $Only -eq "cpu") {
    $env:TORCH_VARIANT = "cpu"
    $env:API_TAG = "cpu"
    Invoke-Step "빌드: api:cpu" { docker compose build api }

    # latest 는 cpu 를 가리킨다. 태그를 생략하고 pull 한 사람이
    # 9GB 짜리 CUDA 이미지를 받는 일이 없도록.
    Invoke-Step "태그: api:cpu -> api:latest" {
        docker tag yslee4050/lawowly:cpu yslee4050/lawowly:latest
    }
    Invoke-Step "푸시: api:cpu"    { docker push yslee4050/lawowly:cpu }
    Invoke-Step "푸시: api:latest" { docker push yslee4050/lawowly:latest }
}

if ($Only -eq "all" -or $Only -eq "cuda") {
    $env:TORCH_VARIANT = "cuda"
    $env:API_TAG = "cuda"
    Invoke-Step "빌드: api:cuda" { docker compose build api }
    Invoke-Step "푸시: api:cuda" { docker push yslee4050/lawowly:cuda }
}

if ($Only -eq "all" -or $Only -eq "ops") {
    $env:OPS_TAG = "latest"
    Invoke-Step "빌드: ops:latest" {
        docker compose -f docker-compose.ops.yml --env-file .env.ops build ops
    }
    Invoke-Step "푸시: ops:latest" { docker push yslee4050/lawowly-ops:latest }
}

Write-Host ""
Write-Host "완료. Docker Hub 에서 확인: https://hub.docker.com/u/yslee4050" -ForegroundColor Green
