"""로컬 다국어 임베딩 모델 래퍼 (intfloat/multilingual-e5-large 등).

E5 계열 모델은 "query: "/"passage: " 프리픽스를 붙였을 때 성능이 더 좋으므로,
검색 질의(입력 청크)와 저장 대상(조문 청크)을 구분해 인코딩한다.
"""

from functools import lru_cache
import torch

from langchain_huggingface import HuggingFaceEmbeddings

from app.config import settings


@lru_cache(maxsize=1)
def get_embedding_model() -> HuggingFaceEmbeddings:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    # 16GB 메모리 환경 안정성을 위해 VRAM/RAM 사용량 최적화
    model_kwargs = {"device": device}
    if device == "cuda":
        # GPU 사용 시 가중치를 절반(float16)으로 줄여 메모리 대폭 절감
        model_kwargs["model_kwargs"] = {"torch_dtype": torch.float16}
        
    return HuggingFaceEmbeddings(
        model_name=settings.embedding_model,
        model_kwargs=model_kwargs,
        encode_kwargs={
            "normalize_embeddings": True,
            "batch_size": 16, # OOM 방지를 위해 배치 사이즈 하향 조정 (기본 32)
        },
    )


def embed_passages(texts: list[str]) -> list[list[float]]:
    model = get_embedding_model()
    prefixed = [f"passage: {t}" for t in texts]
    return model.embed_documents(prefixed)


def embed_queries(texts: list[str]) -> list[list[float]]:
    model = get_embedding_model()
    prefixed = [f"query: {t}" for t in texts]
    return model.embed_documents(prefixed)
