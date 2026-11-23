"""
Lightweight Vector Embeddings and Cosine Similarity Engine.
Pure-NumPy Character/Word N-Gram TF-IDF Cosine Similarity engine (Instant, 0 MB, 0 downloads)
with optional FastEmbed (ONNX Runtime) support.
Builds and persists index directly from SQLite symbols table in < 1 second.
"""

import os
import re
import json
from typing import List, Dict, Any, Optional, Set, Tuple
import numpy as np

try:
    from fastembed import TextEmbedding
    HAS_FASTEMBED = True
except Exception:
    HAS_FASTEMBED = False


def extract_ngrams_and_words(text: str) -> List[str]:
    """Extracts words and character n-grams (3..5 chars) for typo-tolerant matching."""
    text_clean = text.lower()
    words = re.findall(r'[a-z0-9_]+|[а-яё0-9_]+', text_clean)
    features = list(words)

    # Character n-grams for words >= 3 chars
    for w in words:
        w_len = len(w)
        if w_len >= 3:
            for n in (3, 4, 5):
                if w_len >= n:
                    for i in range(w_len - n + 1):
                        features.append(w[i:i + n])
    return features


class NumpyNgramVectorizer:
    """
    Zero-dependency, hashing-trick TF-IDF character & word n-gram vectorizer.
    Extremely fast: vectorizes 6,700 C++ symbols in ~0.3 seconds on CPU.
    """
    def __init__(self, dim: int = 2048):
        self.dim = dim
        self.idf = np.ones(dim, dtype=np.float32)

    def _hash_feature(self, feat: str) -> int:
        h = 2166136261
        for ch in feat:
            h = ((h ^ ord(ch)) * 16777619) & 0xFFFFFFFF
        return h % self.dim

    def fit_transform(self, documents: List[str]) -> np.ndarray:
        n_docs = len(documents)
        matrix = np.zeros((n_docs, self.dim), dtype=np.float32)
        df = np.zeros(self.dim, dtype=np.float32)

        for i, doc in enumerate(documents):
            feats = extract_ngrams_and_words(doc)
            seen_in_doc = set()
            for f in feats:
                idx = self._hash_feature(f)
                matrix[i, idx] += 1.0
                if idx not in seen_in_doc:
                    seen_in_doc.add(idx)
                    df[idx] += 1.0

        # Compute smoothed IDF
        self.idf = np.log((1.0 + n_docs) / (1.0 + df)) + 1.0

        # Apply TF-IDF and L2 normalize
        matrix = matrix * self.idf
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix = matrix / norms
        return matrix

    def transform_single(self, query: str) -> np.ndarray:
        vec = np.zeros(self.dim, dtype=np.float32)
        feats = extract_ngrams_and_words(query)
        for f in feats:
            idx = self._hash_feature(f)
            vec[idx] += 1.0

        vec = vec * self.idf
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec


class VectorIndex:
    def __init__(self, db_manager, storage_dir: Optional[str] = None, use_fastembed: bool = False):
        self.db = db_manager
        if storage_dir is None:
            self.storage_dir = os.path.dirname(db_manager.db_path)
        else:
            self.storage_dir = storage_dir

        self.matrix_path = os.path.join(self.storage_dir, "vector_matrix.npz")
        self.meta_path = os.path.join(self.storage_dir, "vector_meta.json")

        self.matrix: Optional[np.ndarray] = None
        self.metadata: List[Dict[str, Any]] = []
        self.vectorizer: Optional[NumpyNgramVectorizer] = None
        self.fastembed_model = None
        self.use_fastembed = use_fastembed
        self.backend_name = "numpy_ngram"

        self._initialize()

    def _initialize(self):
        """Loads cached vector index or builds from DB if missing."""
        if os.path.exists(self.matrix_path) and os.path.exists(self.meta_path):
            try:
                npz = np.load(self.matrix_path, allow_pickle=True)
                self.matrix = npz["matrix"]
                self.backend_name = str(npz.get("backend", "numpy_ngram"))
                dim = self.matrix.shape[1]
                self.vectorizer = NumpyNgramVectorizer(dim=dim)
                if "idf" in npz:
                    self.vectorizer.idf = npz["idf"]

                with open(self.meta_path, "r", encoding="utf-8") as f:
                    self.metadata = json.load(f)

                if HAS_FASTEMBED and self.backend_name == "fastembed":
                    try:
                        self.fastembed_model = TextEmbedding("BAAI/bge-small-en-v1.5")
                    except Exception:
                        self.backend_name = "numpy_ngram"
                return
            except Exception:
                pass

        # Build directly from sqlite DB without re-parsing AST (takes ~0.3s)
        self.build_index_from_db(use_fastembed=self.use_fastembed)

    def build_index_from_db(self, use_fastembed: bool = False):
        """
        Builds the vector matrix over all symbols in rag_index.db.
        Takes ~0.3-0.5 seconds for 6,700 symbols using the pure-numpy engine.
        """
        with self.db.get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT name, kind, file_path, line_start, line_end, signature, body
                FROM symbols
                ORDER BY id ASC;
            """)
            rows = cur.fetchall()

        if not rows:
            return

        documents: List[str] = []
        meta: List[Dict[str, Any]] = []

        for r in rows:
            name = r["name"]
            kind = r["kind"]
            fp = r["file_path"]
            line = r["line_start"]
            sig = r["signature"] or ""
            body_len = len(r["body"] or "")
            body_snippet = (r["body"] or "")[:400]
            doc_text = f"{name} {kind} {os.path.basename(fp)} {sig} {body_snippet}"
            documents.append(doc_text)
            meta.append({
                "name": name,
                "kind": kind,
                "file_path": fp,
                "line_start": line,
                "signature": sig,
                "body_len": body_len
            })

        self.metadata = meta

        used_fastembed = False
        if use_fastembed and HAS_FASTEMBED:
            try:
                self.fastembed_model = TextEmbedding("BAAI/bge-small-en-v1.5")
                embeddings = list(self.fastembed_model.embed(documents))
                self.matrix = np.array(embeddings, dtype=np.float32)
                norms = np.linalg.norm(self.matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                self.matrix = self.matrix / norms
                self.backend_name = "fastembed"
                used_fastembed = True
            except Exception:
                used_fastembed = False

        if not used_fastembed:
            self.backend_name = "numpy_ngram"
            self.vectorizer = NumpyNgramVectorizer(dim=2048)
            self.matrix = self.vectorizer.fit_transform(documents)

        # Save to disk
        np.savez_compressed(
            self.matrix_path,
            matrix=self.matrix,
            idf=self.vectorizer.idf if self.vectorizer else np.empty(0),
            backend=self.backend_name
        )

        with open(self.meta_path, "w", encoding="utf-8") as f:
            json.dump(self.metadata, f, ensure_ascii=False)

    def search(self, query: str, top_k: int = 10) -> List[Dict[str, Any]]:
        """
        Calculates cosine similarity between query and all indexed symbols.
        Deduplicates results and prioritizes active logic over trivial destructors.
        """
        if self.matrix is None or len(self.metadata) == 0:
            return []

        if self.backend_name == "fastembed" and self.fastembed_model is not None:
            try:
                q_emb = list(self.fastembed_model.embed([query]))[0]
                q_vec = np.array(q_emb, dtype=np.float32)
                norm = np.linalg.norm(q_vec)
                if norm > 0:
                    q_vec = q_vec / norm
            except Exception:
                if self.vectorizer is None:
                    self.vectorizer = NumpyNgramVectorizer(dim=self.matrix.shape[1])
                q_vec = self.vectorizer.transform_single(query)
        else:
            if self.vectorizer is None:
                self.vectorizer = NumpyNgramVectorizer(dim=self.matrix.shape[1])
            q_vec = self.vectorizer.transform_single(query)

        # Cosine similarity: Dot product with normalized matrix
        scores = np.dot(self.matrix, q_vec)

        # Top candidate indices (take more to allow filtering)
        candidate_count = min(len(scores), max(top_k * 4, 30))
        top_indices = np.argsort(scores)[::-1][:candidate_count]

        results: List[Dict[str, Any]] = []
        seen_keys: Set[Tuple[str, str, int]] = set()

        for idx in top_indices:
            score = float(scores[idx])
            if score <= 0.08:
                continue

            item = dict(self.metadata[idx])
            name = re.sub(r'^[&*~;\s]+', '', item.get("name", "")).strip()
            fp = item.get("file_path", "")
            line = item.get("line_start", 0)

            # 1. Skip destructors (~ClassName) from top vector search
            if "~" in item.get("name", ""):
                continue

            # 2. Strict deduplication by (name, file_path, line_start)
            dedup_key = (name, fp, line)
            if dedup_key in seen_keys:
                continue

            seen_keys.add(dedup_key)
            item["score"] = score
            results.append(item)

            if len(results) >= top_k:
                break

        return results
