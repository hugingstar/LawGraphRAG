# SafetyLawAdvisor

사고 상황을 서술한 긴 텍스트를 입력하면, 산업안전보건 관련 법령 중 실제로 적용되는 조문을 찾아
원문 위치에 하이라이트하고 법제처 원문 링크를 함께 보여주는 서비스.

## 아키텍처

```mermaid
flowchart TD
    User([User]) -->|사고 진술문 입력| FastAPI[FastAPI Web Server]
    
    subgraph "Data Ingestion Pipeline"
        LawAPI[법제처 Open API] -->|법령 XML 수집| Ingest[ingest.py]
        Ingest -->|문장 단위 분할| Chunking[chunking.py]
        Chunking -->|LangChain HuggingFace| Embed[embeddings.py]
        Embed -->|벡터화된 조문 저장| DB[(PostgreSQL<br>+ pgvector<br>+ pg_trgm)]
    end
    
    subgraph "LangChain RAG Pipeline (annotate.py)"
        FastAPI -->|입력 텍스트 전달| TextChunk[chunking.py]
        TextChunk -->|청크별 질의| Retriever[HybridSafetyLawRetriever]
        Retriever -->|벡터+키워드 하이브리드 검색<br>(RRF)| DB
        DB -.->|후보 조문 반환| Retriever
        Retriever -->|LCEL Chain| LLM[ChatGoogleGenerativeAI<br>with_structured_output]
        LLM -->|적용 조문 판단 및 발췌| Citation[Citation Merge & Link]
    end
    
    Citation --> FastAPI
    FastAPI -->|결과 렌더링| User
```

- **Postgres + pgvector + pg_trgm** (Docker): 법령 조문을 조/청크 단위로 저장, 벡터+키워드 하이브리드 검색
- **법제처 Open API**: 법령 원문 수집 (OC 키 필요)
- **LangChain + 로컬 임베딩 모델** (`intfloat/multilingual-e5-large`): 조문/질의 벡터화, 추가 비용 없음
- **LangChain + Gemini API**: 하이브리드 검색으로 뽑은 후보 조문 중 실제 적용 여부와 인용 근거 판단 (LCEL)
- **FastAPI + 정적 HTML/JS**: 입력 → 분석 → 하이라이트 결과 표시

## 준비물

1. Docker / Docker Compose
2. 법제처 Open API OC 키: https://open.law.go.kr 에서 회원가입 후 발급
3. Gemini API 키: https://aistudio.google.com/apikey

## 설정

```bash
cp .env.example .env
# .env에 LAW_OC_KEY, GEMINI_API_KEY 채워넣기
```

## 실행

```bash
docker compose up -d db
docker compose build api
```

### 1) 법령 데이터 수집 (최초 1회, OC 키 필요)

```bash
docker compose run --rm api python -m app.ingest
```

`Data/DATA_SOURCE_URL.md`에 정의된 4개 법령(산업안전보건법, 시행령, 시행규칙,
산업안전보건기준에 관한 규칙)을 조문 단위로 수집해 DB에 저장하고 임베딩까지 생성한다.
법령이 개정되면 같은 명령을 다시 실행하면 된다(법령ID+조번호 기준 upsert).

### 2) 웹 서버 실행

```bash
docker compose up api
```

http://localhost:8000 접속 후 사고 진술문을 입력하면 관련 조문이 하이라이트되어 표시된다.

## 테스트

```bash
pip install -r requirements.txt
pip install pytest
pytest
```

청킹 오프셋 계산과 법제처 링크 생성 로직에 대한 유닛 테스트가 포함되어 있다.
`ingest`/`annotate` 파이프라인은 실제 OC 키·Gemini 키가 있어야 end-to-end 검증이 가능하다.

## 알아둘 점

- 법제처 딥링크 URL 패턴(`app/law_links.py`)은 사이트 구조가 바뀌면 이 파일만 수정하면 된다.
- 조문 청킹/질의 텍스트 청킹은 모두 `app/chunking.py`의 문장 단위 슬라이딩 윈도우를 공유한다.
- 하이브리드 검색은 `app/retrieval.py`에서 벡터 유사도와 트라이그램 유사도를 RRF로 결합한다.
