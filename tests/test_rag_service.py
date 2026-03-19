from __future__ import annotations

import unittest

from app.chat.service import (
    _extract_anchor_terms,
    _extract_eligible_query_terms,
    _retrieval_quality,
    _should_accept_rewrite,
)
from app.platform.config import Settings


class RagServiceQualityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            rag_min_score=0.2,
            rag_min_evidence_chars=30,
            rag_offtopic_min_query_terms=2,
            rag_offtopic_score_ceiling=0.55,
        )

    def test_extract_eligible_query_terms_skips_latex_and_generic_words(self) -> None:
        terms = _extract_eligible_query_terms(r"What does $\int_a^b f(x)\,dx$ mean for Python lists?")
        self.assertEqual(terms, ("lists", "python"))

    def test_extract_anchor_terms_is_deterministic(self) -> None:
        chunks = [
            {
                "title": "Inverse Functions",
                "content_text": "A function has an inverse when it is one-to-one on its domain.",
            },
            {
                "title": "Continuity",
                "content_text": "A continuous function has no breaks or jumps in its graph.",
            },
        ]
        anchor_terms = _extract_anchor_terms(chunks)
        self.assertIn("inverse", anchor_terms)
        self.assertIn("continuous", anchor_terms)
        self.assertNotIn("when", anchor_terms)

    def test_retrieval_quality_marks_numeric_weak_reason(self) -> None:
        quality = _retrieval_quality(
            message="What is a function?",
            chunks=[{"title": "Functions", "content_text": "A function maps inputs to outputs."}],
            retrieval={"top_score": 0.12},
            settings=self.settings,
        )
        self.assertTrue(quality["weak_evidence"])
        self.assertEqual(quality["weak_evidence_reason"], "low_top_score")
        self.assertFalse(quality["offtopic_suspected"])

    def test_retrieval_quality_marks_offtopic_zero_overlap(self) -> None:
        quality = _retrieval_quality(
            message="Who won the FIFA World Cup in 2022?",
            chunks=[
                {
                    "title": "Functions",
                    "content_text": "A function maps each input to exactly one output.",
                }
            ],
            retrieval={"top_score": 0.39},
            settings=self.settings,
        )
        self.assertTrue(quality["weak_evidence"])
        self.assertTrue(quality["offtopic_suspected"])
        self.assertEqual(quality["weak_evidence_reason"], "offtopic_zero_overlap")
        self.assertEqual(quality["matched_query_term_count"], 0)

    def test_retrieval_quality_keeps_in_scope_overlap_strong(self) -> None:
        quality = _retrieval_quality(
            message="How can I tell whether a function has an inverse?",
            chunks=[
                {
                    "title": "Inverse Functions",
                    "content_text": "An inverse function exists when the function is one-to-one.",
                }
            ],
            retrieval={"top_score": 0.42},
            settings=self.settings,
        )
        self.assertFalse(quality["weak_evidence"])
        self.assertEqual(quality["weak_evidence_reason"], "ok")
        self.assertGreaterEqual(quality["matched_query_term_count"], 2)

    def test_should_accept_rewrite_requires_meaningful_top_score_gain(self) -> None:
        accepted = _should_accept_rewrite(
            original_chunks=[{"chunk_id": "a"}],
            original_top_score=0.48,
            original_evidence_chars=500,
            rewritten_chunks=[{"chunk_id": "b"}],
            rewritten_top_score=0.56,
            rewritten_evidence_chars=800,
            rewritten_weak_evidence=False,
        )
        self.assertFalse(accepted)

    def test_should_accept_rewrite_accepts_clear_rescue(self) -> None:
        accepted = _should_accept_rewrite(
            original_chunks=[{"chunk_id": "a"}],
            original_top_score=0.50,
            original_evidence_chars=500,
            rewritten_chunks=[{"chunk_id": "b"}],
            rewritten_top_score=0.70,
            rewritten_evidence_chars=650,
            rewritten_weak_evidence=False,
        )
        self.assertTrue(accepted)


if __name__ == "__main__":
    unittest.main()
