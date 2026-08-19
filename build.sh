#!/bin/bash
# 예외적으로 리눅스 환경에서 직접 소스 코드를 빌드해야 할 경우 사용됩니다.
# 내부적으로 deploy.sh를 빌드 모드로 호출합니다.

cd "$(dirname "$0")"
./deploy.sh --build "$@"
