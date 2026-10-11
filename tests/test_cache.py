"""Embedding cache: keys cover model/version/full content, files hold vectors only."""
from tests import helpers  # noqa: F401

import unittest
from unittest import mock

import numpy as np

from ats import config, embedder
from ats.embedder import Embedder, _cache_key, semantic_chunks


class CacheKeyTests(unittest.TestCase):
    def test_regression_no_collision_on_shared_prefix(self):
        # Old cache keyed on the first 2000 characters only: template resumes collided.
        prefix = "x " * 1500
        self.assertNotEqual(_cache_key([prefix + "python developer"]), _cache_key([prefix + "accountant"]))

    def test_key_changes_with_model_and_version(self):
        base = _cache_key(["same text"])
        with mock.patch.object(config, "EMBEDDING_VERSION", "other"):
            self.assertNotEqual(base, _cache_key(["same text"]))
        with mock.patch.object(config, "EMBEDDING_MODEL", "other-model"):
            self.assertNotEqual(base, _cache_key(["same text"]))

    def test_cached_vectors_reused_and_contain_no_text(self):
        fake = mock.Mock()
        fake.encode.side_effect = lambda texts, **kw: np.ones((len(texts), embedder.EMBEDDING_DIM), dtype=np.float32)
        e = Embedder(batch_size=4)
        with mock.patch.object(Embedder, "get_model", return_value=fake):
            v1, hits1 = e.embed_documents([["secret resume text alpha"], []])
            v2, hits2 = e.embed_documents([["secret resume text alpha"]])
        self.assertEqual((hits1, hits2), (0, 1))
        self.assertIsNone(v1[1])  # empty document -> no vector, no crash
        self.assertAlmostEqual(float(np.linalg.norm(v2[0])), 1.0, places=5)
        self.assertEqual(fake.encode.call_count, 1)
        for path in e.cache_dir.glob("*.npy"):
            self.assertNotIn(b"secret resume", path.read_bytes())

    def test_semantic_chunks_prioritize_relevant_sections(self):
        text = "\n".join(["personal details", "father name: x " * 30, "summary", "backend engineer python",
                          "technical skills", "python, sql"])
        chunks = semantic_chunks(text, max_chunks=1, chunk_words=6)
        self.assertEqual(chunks, ["summary backend engineer python technical skills"])
        self.assertEqual(semantic_chunks(""), [])


if __name__ == "__main__":
    unittest.main()
