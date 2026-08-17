"""후보 조문 재순위(cross-encoder).

벡터 검색은 질의와 조문을 각각 따로 임베딩해 비교하므로(bi-encoder), 사고 진술문처럼
서술이 긴 질의에서는 "비슷한 분위기"의 조문이 상위에 섞인다. cross-encoder는 질의와 조문을
한 번에 같이 읽어 점수를 매기므로 훨씬 정확하지만 후보 전체에는 못 쓴다(모든 쌍을 계산해야
한다). 그래서 벡터로 넓게 뽑은 뒤 상위 RERANK_POOL개만 이 모델로 다시 줄 세운다.

로컬 GPU 모델이라 API 비용·쿼터와 무관하다. 모델을 못 받거나 로드에 실패하면 재순위를
건너뛰고 벡터 순서를 그대로 쓴다 — 검색이 통째로 죽는 것보다 낫다.
"""

import logging
from functools import lru_cache

from app.config import settings

logger = logging.getLogger(__name__)

# 재순위에 넘길 후보 수. 늘릴수록 정확해지지만 cross-encoder 계산이 그만큼 늘어난다.
RERANK_POOL = 50


@lru_cache(maxsize=1)
def get_reranker():
    import torch
    from sentence_transformers import CrossEncoder

    device = "cuda" if torch.cuda.is_available() else "cpu"
    kwargs = {"device": device, "max_length": 512}
    if device == "cuda":
        # 임베딩 모델과 같은 GPU를 나눠 쓰므로 절반 정밀도로 올린다.
        # (sentence-transformers 3.x의 CrossEncoder는 automodel_args로 받는다)
        kwargs["automodel_args"] = {"torch_dtype": torch.float16}
    return CrossEncoder(settings.rerank_model, **kwargs)


def rerank_order(query: str, passages: list[str]) -> list[int] | None:
    """passages를 질의 관련도 순으로 다시 세운 인덱스 목록. 실패하면 None."""
    if not settings.rerank_enabled or len(passages) < 2:
        return None
    try:
        model = get_reranker()
        scores = model.predict([(query, p) for p in passages])
    except Exception:
        logger.warning("재순위 모델을 쓸 수 없어 벡터 순서를 그대로 사용한다", exc_info=True)
        return None
    return sorted(range(len(passages)), key=lambda i: float(scores[i]), reverse=True)
