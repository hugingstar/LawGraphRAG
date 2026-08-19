# Law Owly (법 부엉이) - LawGraphRAG

Law Owly는 사용자가 작성한 **사고 진술문(자연어)**을 기반으로, 관련된 **법률 조문을 지식 그래프(Knowledge Graph)와 벡터 검색(RAG)**을 통해 정확하게 찾아주는 스마트 법률 지원 시스템입니다.

단순 검색을 넘어, 전국 단위 사건사고 접수부터 검토까지 처리하는 워크플로우와 지역별 발생 현황을 시각화하는 대시보드 기능이 통합되어 있습니다.

---

## 🚀 빠른 시작 가이드 (초보자용)

### 💡 3단계 배포 워크플로우
이 프로젝트는 Docker 기반으로 동작하여 복잡한 환경 설정 없이 스크립트 하나로 전체 시스템을 띄울 수 있습니다.

```mermaid
%%{init: {"themeVariables": {"fontSize": "20px", "fontFamily": "sans-serif"}, "flowchart": {"nodeSpacing": 100, "rankSpacing": 120, "htmlLabels": true}}}%%
flowchart LR
    Local[1. 로컬 테스트<br/>build.bat] -->|테스트 성공| Push[2. 이미지 업로드<br/>docker-push.ps1]
    Push -->|Push| Hub[(Docker Hub<br/>yslee4050/lawowly)]
    Hub -.->|Pull| Deploy[3. 운영 서버 배포<br/>deploy.sh]
```

### 1️⃣ 소스 코드 다운로드
```bash
git clone https://github.com/hugingstar/LawGraphRAG.git
cd LawGraphRAG
```

### 2️⃣ 시스템 실행 (배포)
Windows 사용자는 `deploy.bat`을 더블클릭하고, Linux/macOS 사용자는 터미널에서 아래 명령어를 실행합니다.
```bash
chmod +x deploy.sh
./deploy.sh
```
- **자동 감지**: 호스트 PC의 NVIDIA GPU 유무를 스스로 판단하여 최적의 이미지(CPU/CUDA)를 실행합니다.
- **방화벽 설정**: `80` 포트가 자동으로 개방되어 외부에서 접속 가능하게 세팅됩니다.
- **모니터링 포함**: 상태 점검을 위한 Grafana, Prometheus 스택이 함께 기동됩니다.

### 3️⃣ 초기 법령 데이터 수집
최초 실행 시 DB가 비어 있으므로, 국가 법령을 다운로드(약 10~20분 소요) 받아야 합니다.
```bash
docker compose exec api python -m app.ingest --all
```

### 4️⃣ 접속
웹 브라우저를 열고 다음 주소로 접속하세요.
- **메인 서비스**: `http://localhost` (서버 IP)
- **운영 대시보드 (Grafana)**: `http://localhost:3001` (서버 내부망에서만 접근 가능)

---

## 🏛️ 시스템 아키텍처 (Architecture)

시스템은 웹 애플리케이션(FastAPI), 데이터 수집 파이프라인, 그래프 추출 AI, 그리고 RAG 검색 엔진으로 촘촘히 연결되어 있습니다.

```mermaid
%%{init: {"themeVariables": {"fontSize": "20px", "fontFamily": "sans-serif"}, "flowchart": {"nodeSpacing": 120, "rankSpacing": 120, "htmlLabels": true}}}%%
flowchart TD
    User(["사용자 (신청자 / 담당자)"]) -->|HTTP Request| Nginx["Nginx (포트 80)"]
    Nginx --> App["FastAPI 서버 (포트 8000)"]
    
    subgraph Web_Application ["웹 애플리케이션 계층"]
        App --> Auth["인증/인가 미들웨어"]
        Auth --> Routers["사건 접수 / 대시보드 / 사용자 관리 라우터"]
    end
    
    subgraph Storage ["데이터 저장소"]
        DB[("PostgreSQL<br/>(pgvector 벡터 검색)")]
        Neo4j[("Neo4j<br/>(법령 연결 관계 그래프)")]
    end
    
    subgraph GraphRAG_Engine ["지능형 RAG 검색 파이프라인"]
        Query["사고 진술문 입력"] --> LLM_Issue["Gemini API: 쟁점 추출"]
        LLM_Issue --> VectorSearch["pgvector: 의미 유사도 기반 후보 조문 검색"]
        VectorSearch --> GraphSearch["Neo4j: 시드 조문의 연관 법령 그래프 확장"]
        GraphSearch --> Rerank["Cross-Encoder: 검색 결과 재순위화 (Reranking)"]
        Rerank --> LLM_Final["Gemini API: 최종 조문 적합성 검토 및 발췌"]
    end
    
    Routers --> Query
    LLM_Final -->|검토 결과 반환| Routers
    
    VectorSearch --> DB
    GraphSearch --> Neo4j
    
    %% 관리자/모니터링
    Prometheus["Prometheus"] -.->|메트릭 수집| App
    Grafana["Grafana 대시보드"] -.-> Prometheus
```

---

## 🗄️ 데이터베이스 ER 다이어그램 (ERD)

PostgreSQL을 활용해 법령 구조(법, 조문, 조문 분할 조각)를 저장하고, 사용자 및 사건(Incident) 상태를 관리합니다.

```mermaid
%%{init: {"themeVariables": {"fontSize": "20px", "fontFamily": "sans-serif"}, "class": {"nodeSpacing": 120, "rankSpacing": 150}}}%%
classDiagram
    direction LR

    class laws {
        int id [PK]
        string law_name
        string department
        int category_id [FK]
    }
    
    class articles {
        int id [PK]
        int law_id [FK]
        string title
        text full_text
    }
    
    class article_chunks {
        int id [PK]
        int article_id [FK]
        text chunk_text
        vector embedding
    }

    class users {
        int id [PK]
        string username
        string role
        string sido_code
    }

    class incidents {
        int id [PK]
        int created_by_user_id [FK]
        text background
        text statement
        string status
        json citations
    }

    class incident_events {
        int id [PK]
        int incident_id [FK]
        string status
        text note
    }

    laws "1" *-- "*" articles : 포함
    articles "1" *-- "*" article_chunks : 텍스트 분할 및 벡터화
    
    users "1" --> "*" incidents : 사건 접수
    incidents "1" *-- "*" incident_events : 처리 상태 이력 관리
```

---

## 🛠 주요 기능 요약

1. **지능형 조문 분석 (GraphRAG)**: 단순 단어 검색이 아닌, 사용자의 상황을 AI가 이해하고 쟁점을 뽑아낸 뒤 벡터 유사도와 연결 그래프(Neo4j)를 모두 동원하여 정확한 현행 법령을 찾아줍니다.
2. **사건 접수 및 처리 상태 추적**: 전국 단위로 민원을 접수하고, "검토중 -> 보완요청 -> 보완완료 -> 검토완료"의 워크플로우를 통해 체계적으로 관리합니다.
3. **전국 사건 현황 시각화**: 통계청 TopoJSON 데이터를 활용한 코로플레스(Choropleth) 지도를 통해 전국 시/도/구 단위로 어느 지역에 어떤 사건이 집중되는지 한눈에 파악할 수 있습니다.
4. **증분 법령 수집**: 법제처 Open API와 연동하여 법령이 새로 바뀌거나 폐지된 내용만 똑똑하게 부분 업데이트합니다. 비용과 시간을 획기적으로 절약합니다.
