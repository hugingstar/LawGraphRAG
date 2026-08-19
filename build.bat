@echo off
chcp 65001 >nul
echo [build.bat] 로컬 PC에서 Docker 기반으로 컨테이너를 빌드하고 실행하여 정상 작동 여부를 테스트합니다...
docker compose up --build -d
echo.
echo 배포가 완료되었습니다. 웹 브라우저에서 http://localhost 로 접속하여 확인하세요.
pause
