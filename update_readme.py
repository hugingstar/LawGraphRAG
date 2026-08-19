import re

with open('README.md', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Update deployment workflow section
old_workflow_start = '### 스크립트 한눈에 보기'
old_workflow_end = '### 1) Docker 설치'
if old_workflow_start in content and old_workflow_end in content:
    pre = content.split(old_workflow_start)[0]
    post = old_workflow_end + content.split(old_workflow_end, 1)[1]
    new_workflow = """### 💡 3단계 배포 워크플로우

1. **로컬 테스트 (`build.bat`)**: 내 PC(Windows)에서 코드를 수정한 뒤, Docker 환경으로 직접 빌드하고 띄워 정상 작동을 확인합니다.
2. **이미지 업로드 (`docker-push.ps1`)**: 테스트가 완료된 최종 운영용 이미지(cpu, cuda, ops)를 빌드하여 Docker Hub(`yslee4050/lawowly`)에 Push합니다.
3. **운영 서버 적용 (`deploy.sh`)**: 리눅스 운영 서버에서 실행하면, 별도의 빌드 과정 없이 Docker Hub의 최신 이미지를 고속으로 Pull 받아 무중단 재시작합니다.

```mermaid
%%{init: {"themeVariables": {"fontSize": "18px"}, "flowchart": {"nodeSpacing": 80, "rankSpacing": 100}}}%%
flowchart LR
    Local[1. 로컬 테스트<br/>build.bat] -->|테스트 완료| Push[2. 이미지 업로드<br/>docker-push.ps1]
    Push -->|Push| Hub[(Docker Hub<br/>yslee4050/lawowly)]
    Hub -.->|Pull| Deploy[3. 운영 서버 배포<br/>deploy.sh]
```

"""
    content = pre + new_workflow + post

# 2. Fix Architecture diagram overlapping
content = content.replace(
    '%%{init: {"themeVariables": {"fontSize": "20px"}, "flowchart": {"nodeSpacing": 45, "rankSpacing": 65, "htmlLabels": true}}}%%',
    '%%{init: {"themeVariables": {"fontSize": "18px"}, "flowchart": {"nodeSpacing": 80, "rankSpacing": 100, "htmlLabels": true}}}%%'
)

# 3. Fix ER diagram overlapping
content = content.replace(
    '%%{init: {"themeVariables": {"fontSize": "20px"}}}%%',
    '%%{init: {"themeVariables": {"fontSize": "16px"}}}%%'
)

with open('README.md', 'w', encoding='utf-8') as f:
    f.write(content)
print("Updated README.md")
