"""앱 전역에서 공유하는 Jinja2 템플릿 인스턴스.

static_version은 프로세스 시작 시각으로 고정되어, 컨테이너를 재시작(재배포)할 때마다
바뀐다. 템플릿에서 `?v={{ v }}`로 정적 자산 URL에 붙여 브라우저 캐시를 무효화한다.
"""

import time

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory="app/templates")
templates.env.globals["v"] = str(int(time.time()))
