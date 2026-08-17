#!/bin/sh
# Alertmanager 는 설정 파일 안의 환경변수 치환을 지원하지 않는다.
# 그래서 읽기 전용으로 마운트된 템플릿을 쓰기 가능한 위치로 복사한 뒤,
# 자리표시자를 SLACK_WEBHOOK_URL 값으로 바꿔서 실행한다.
set -eu

SRC=/etc/alertmanager/alertmanager.yml
DST=/tmp/alertmanager.yml

# api_url 은 설정 파싱 시점에 스킴(https://)이 있는지 검증된다. 빈 문자열을
# 넣으면 "unsupported scheme" 로 기동 자체가 실패해 재시작 루프에 빠진다.
# 그래서 비어 있을 땐 문법적으로 유효한 자리표시자를 넣어 기동은 되게 하고,
# 실제 critical 알림이 떴을 때 발송만 실패하게 한다(재시작 루프보다 훨씬 낫다).
WEBHOOK_URL="${SLACK_WEBHOOK_URL:-https://hooks.slack.com/services/UNSET/UNSET/UNSET}"

if [ -z "${SLACK_WEBHOOK_URL:-}" ]; then
    echo "[entrypoint] SLACK_WEBHOOK_URL 이 비어 있습니다. .env.ops 에 값을 채우고 재기동하세요." >&2
    echo "[entrypoint] 지금은 기동은 되지만 Slack 발송은 실패합니다(라우팅·억제 규칙은 정상 평가됨)." >&2
fi

sed "s|__SLACK_WEBHOOK_URL__|${WEBHOOK_URL}|g" "$SRC" > "$DST"

exec /bin/alertmanager --config.file="$DST" --storage.path=/alertmanager "$@"
