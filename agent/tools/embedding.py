from langchain_huggingface import HuggingFaceEmbeddings

_model: HuggingFaceEmbeddings | None = None


def get_embedder() -> HuggingFaceEmbeddings:
    global _model
    if _model is None:
        _model = HuggingFaceEmbeddings(
            model_name="all-MiniLM-L6-v2",
            model_kwargs={"device": "cpu"},
            encode_kwargs={"normalize_embeddings": True},
        )
    return _model
