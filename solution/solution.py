"""
Day 14 — AI Evaluation & Benchmarking Pipeline
AICB-P1: AI Practical Competency Program, Phase 1
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable


# ---------------------------------------------------------------------------
# Task 1 — Data Models (Golden Dataset + Evaluation Results)
# ---------------------------------------------------------------------------

@dataclass
class QAPair:
    """A question-answer pair for evaluation (part of the Golden Dataset)."""
    question: str
    expected_answer: str
    context: str = ""
    metadata: dict = field(default_factory=dict)
    retrieved_contexts: list = field(default_factory=list)


@dataclass
class EvalResult:
    """Evaluation result for a single Q&A pair."""
    qa_pair: QAPair
    actual_answer: str
    faithfulness: float
    relevance: float
    completeness: float
    passed: bool
    failure_type: str | None = None
    context_precision: float | None = None
    context_recall: float | None = None

    def overall_score(self) -> float:
        """Compute the average of faithfulness, relevance, and completeness."""
        return (self.faithfulness + self.relevance + self.completeness) / 3.0


# ---------------------------------------------------------------------------
# Task 2 — RAGAS Evaluator (Simplified word-overlap heuristic)
# ---------------------------------------------------------------------------

STOPWORDS: set[str] = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "of", "in", "on", "at", "to", "for", "with", "as", "by", "and", "or",
    "it", "its", "this", "that", "these", "those", "from", "into", "than",
}


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokenization, ignoring punctuation and stopwords."""
    if not text:
        return set()
    tokens = re.findall(r"\b\w+\b", text.lower())
    return {t for t in tokens if t not in STOPWORDS}


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


class RAGASEvaluator:
    """Evaluates RAG pipeline outputs using RAGAS-inspired heuristics."""

    def evaluate_faithfulness(self, answer: str, context: str) -> float:
        answer_tokens = _tokenize(answer)
        if not answer_tokens:
            return 1.0
        context_tokens = _tokenize(context)
        score = len(answer_tokens & context_tokens) / len(answer_tokens)
        return _clamp(score)

    def evaluate_relevance(self, answer: str, question: str) -> float:
        question_tokens = _tokenize(question)
        if not question_tokens:
            return 1.0
        answer_tokens = _tokenize(answer)
        score = len(answer_tokens & question_tokens) / len(question_tokens)
        return _clamp(score)

    def evaluate_completeness(self, answer: str, expected: str) -> float:
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        answer_tokens = _tokenize(answer)
        score = len(answer_tokens & expected_tokens) / len(expected_tokens)
        return _clamp(score)

    # -----------------------------------------------------------------------
    # Task 2b — Retrieval-side metrics (evaluate the GET-CONTEXT step)
    # -----------------------------------------------------------------------

    def evaluate_context_recall(self, contexts: list[str], expected: str) -> float:
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        union_tokens: set[str] = set()
        for chunk in contexts:
            union_tokens |= _tokenize(chunk)
        score = len(expected_tokens & union_tokens) / len(expected_tokens)
        return _clamp(score)

    def evaluate_context_precision(
        self,
        contexts: list[str],
        expected: str,
        relevance_threshold: float = 0.1,
    ) -> float:
        expected_tokens = _tokenize(expected)
        if not expected_tokens:
            return 1.0
        if not contexts:
            return 0.0
 
        relevance_flags = []
        for chunk in contexts:
            chunk_tokens = _tokenize(chunk)
            overlap = len(chunk_tokens & expected_tokens) / len(expected_tokens)
            relevance_flags.append(overlap >= relevance_threshold)
 
        num_relevant = sum(relevance_flags)
        if num_relevant == 0:
            return 0.0
 
        running_relevant = 0
        precision_sum = 0.0
        for k, is_relevant in enumerate(relevance_flags, start=1):
            if is_relevant:
                running_relevant += 1
                precision_at_k = running_relevant / k
                precision_sum += precision_at_k
 
        return _clamp(precision_sum / num_relevant)

    def run_full_eval(
        self,
        answer: str,
        question: str,
        context: str,
        expected: str,
        contexts: list[str] | None = None,
    ) -> EvalResult:
        faithfulness = self.evaluate_faithfulness(answer, context)
        relevance = self.evaluate_relevance(answer, question)
        completeness = self.evaluate_completeness(answer, expected)

        passed = faithfulness >= 0.5 and relevance >= 0.5 and completeness >= 0.5

        failure_type = None
        if not passed:
            if faithfulness < 0.3:
                failure_type = "hallucination"
            elif relevance < 0.3:
                failure_type = "irrelevant"
            elif completeness < 0.3:
                failure_type = "incomplete"
            else:
                failure_type = "off_topic"
 
        context_recall = None
        context_precision = None
        if contexts is not None:
            context_recall = self.evaluate_context_recall(contexts, expected)
            context_precision = self.evaluate_context_precision(contexts, expected)
 
        qa_pair = QAPair(
            question=question,
            expected_answer=expected,
            context=context,
            retrieved_contexts=contexts or [],
        )

        return EvalResult(
            qa_pair=qa_pair,
            actual_answer=answer,
            faithfulness=faithfulness,
            relevance=relevance,
            completeness=completeness,
            passed=passed,
            failure_type=failure_type,
            context_precision=context_precision,
            context_recall=context_recall,
        )


# ---------------------------------------------------------------------------
# Reranking helper (used by Exercise 3.5 — boosting Context Precision)
# ---------------------------------------------------------------------------

def rerank_by_overlap(contexts: list[str], query: str) -> list[str]:
    """A minimal lexical reranker: sort chunks by word overlap with the query"""
    query_tokens = _tokenize(query)
    return sorted(
        contexts,
        key=lambda c: len(_tokenize(c) & query_tokens),
        reverse=True,
    )


# ---------------------------------------------------------------------------
# Task 3 — LLM Judge
# ---------------------------------------------------------------------------

import json


class LLMJudge:
    """Uses an LLM to score AI responses according to a rubric."""

    def __init__(self, judge_llm_fn: Callable[[str], str]) -> None:
        self.judge_llm_fn = judge_llm_fn

    def score_response(
        self,
        question: str,
        answer: str,
        rubric: dict[str, Any],
    ) -> dict[str, Any]:
        rubric_lines = "\n".join(f"- {k}: {v}" for k, v in rubric.items())
        prompt = (
            f"Question: {question}\n"
            f"Answer: {answer}\n"
            f"Rubric:\n{rubric_lines}\n\n"
            "Score each rubric criterion from 0.0 to 1.0. "
            "Respond ONLY with a JSON object mapping criterion name to score."
        )
 
        raw_response = self.judge_llm_fn(prompt)
 
        scores: dict[str, float] = {}
        try:
            cleaned = raw_response.strip()
            cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                for criterion in rubric:
                    value = parsed.get(criterion, 0.5)
                    try:
                        scores[criterion] = float(value)
                    except (TypeError, ValueError):
                        scores[criterion] = 0.5
            else:
                scores = {criterion: 0.5 for criterion in rubric}
        except (json.JSONDecodeError, AttributeError):
            scores = {criterion: 0.5 for criterion in rubric}
 
        return {
            "scores": scores,
            "reasoning": raw_response,
        }

    def detect_bias(self, scores_batch: list[dict[str, Any]]) -> dict[str, Any]:
        if not scores_batch:
            return {
                "positional_bias": False,
                "leniency_bias": False,
                "severity_bias": False,
            }
        
        all_values: list[float] = []
        for entry in scores_batch:
            scores = entry.get("scores", {})
            all_values.extend(scores.values())
 
        avg_score = sum(all_values) / len(all_values) if all_values else 0.0
 
        # Positional bias: first response in each batch entry consistently
        # scores higher than the rest of that entry's criteria.
        positional_bias = False
        first_higher_count = 0
        comparisons = 0
        for entry in scores_batch:
            scores = list(entry.get("scores", {}).values())
            if len(scores) >= 2:
                comparisons += 1
                if scores[0] > max(scores[1:]):
                    first_higher_count += 1
        if comparisons > 0 and first_higher_count == comparisons:
            positional_bias = True

        leniency_bias = avg_score > 0.8
        severity_bias = avg_score < 0.3
 
        return {
            "positional_bias": positional_bias,
            "leniency_bias": leniency_bias,
            "severity_bias": severity_bias,
        }


# ---------------------------------------------------------------------------
# Task 4 — Benchmark Runner
# ---------------------------------------------------------------------------

class BenchmarkRunner:
    """Runs a full evaluation benchmark."""

    def run(
        self,
        qa_pairs: list[QAPair],
        agent_fn: Callable[[str], str],
        evaluator: RAGASEvaluator,
    ) -> list[EvalResult]:
        results: list[EvalResult] = []
        for pair in qa_pairs:
            actual_answer = agent_fn(pair.question)
            result = evaluator.run_full_eval(
                answer=actual_answer,
                question=pair.question,
                context=pair.context,
                expected=pair.expected_answer,
                contexts=pair.retrieved_contexts or None,
            )
            result.qa_pair = pair
            results.append(result)
        return results

    def generate_report(self, results: list[EvalResult]) -> dict[str, Any]:
        total = len(results)
        passed = sum(1 for r in results if r.passed)
        pass_rate = passed / total if total else 0.0
 
        avg_faithfulness = sum(r.faithfulness for r in results) / total if total else 0.0
        avg_relevance = sum(r.relevance for r in results) / total if total else 0.0
        avg_completeness = sum(r.completeness for r in results) / total if total else 0.0
 
        recalls = [r.context_recall for r in results if r.context_recall is not None]
        precisions = [r.context_precision for r in results if r.context_precision is not None]
 
        avg_context_recall = sum(recalls) / len(recalls) if recalls else None
        avg_context_precision = sum(precisions) / len(precisions) if precisions else None
 
        failure_types: dict[str, int] = {}
        for r in results:
            if r.failure_type:
                failure_types[r.failure_type] = failure_types.get(r.failure_type, 0) + 1
 
        return {
            "total": total,
            "passed": passed,
            "pass_rate": pass_rate,
            "avg_faithfulness": avg_faithfulness,
            "avg_relevance": avg_relevance,
            "avg_completeness": avg_completeness,
            "avg_context_recall": avg_context_recall,
            "avg_context_precision": avg_context_precision,
            "failure_types": failure_types,
        }

    def run_regression(self, new_results: list, baseline_results: list) -> dict:
        def _avg(results, attr):
            if not results:
                return 0.0
            return sum(getattr(r, attr) for r in results) / len(results)
 
        new_avg_faithfulness = _avg(new_results, "faithfulness")
        new_avg_relevance = _avg(new_results, "relevance")
        new_avg_completeness = _avg(new_results, "completeness")
 
        baseline_avg_faithfulness = _avg(baseline_results, "faithfulness")
        baseline_avg_relevance = _avg(baseline_results, "relevance")
        baseline_avg_completeness = _avg(baseline_results, "completeness")
 
        regressions = []
        if baseline_avg_faithfulness - new_avg_faithfulness > 0.05:
            regressions.append("faithfulness")
        if baseline_avg_relevance - new_avg_relevance > 0.05:
            regressions.append("relevance")
        if baseline_avg_completeness - new_avg_completeness > 0.05:
            regressions.append("completeness")
 
        return {
            "new_avg_faithfulness": new_avg_faithfulness,
            "new_avg_relevance": new_avg_relevance,
            "new_avg_completeness": new_avg_completeness,
            "baseline_avg_faithfulness": baseline_avg_faithfulness,
            "baseline_avg_relevance": baseline_avg_relevance,
            "baseline_avg_completeness": baseline_avg_completeness,
            "regressions": regressions,
            "passed": len(regressions) == 0,
        }

    def identify_failures(
        self,
        results: list[EvalResult],
        threshold: float = 0.5,
    ) -> list[EvalResult]:
        failures = []
        for r in results:
            scores = [r.faithfulness, r.relevance, r.completeness]
            if any(score < threshold for score in scores):
                failures.append(r)
        return failures


# ---------------------------------------------------------------------------
# Task 5 — Failure Analyzer
# ---------------------------------------------------------------------------

class FailureAnalyzer:
    """Analyzes failed evaluation results to identify patterns and suggest fixes."""

    def categorize_failures(
        self, failures: list[EvalResult]
    ) -> dict[str, int]:
        categories: dict[str, int] = {}
        for f in failures:
            failure_type = f.failure_type or "unknown"
            categories[failure_type] = categories.get(failure_type, 0) + 1
        return categories

    def find_root_cause(self, failure: EvalResult) -> str:
        scores = {
            "faithfulness": failure.faithfulness,
            "relevance": failure.relevance,
            "completeness": failure.completeness,
        }
        min_score = min(scores.values())
        lowest = [name for name, value in scores.items() if value == min_score]
 
        if len(lowest) > 1:
            return "Multiple issues detected — review full pipeline"
 
        lowest_metric = lowest[0]
        if lowest_metric == "faithfulness":
            return "Context is missing or irrelevant — improve retrieval"
        elif lowest_metric == "relevance":
            return "Answer does not address the question — improve prompt clarity"
        else:
            return "Answer is missing key information — increase context window or improve generation"

    def generate_improvement_log(self, failures: list, suggestions: list[str]) -> str:
        lines = [
            "| Failure ID | Type | Root Cause | Suggested Fix | Status |",
            "|------------|------|------------|---------------|--------|",
        ]
        for i, failure in enumerate(failures):
            failure_id = f"F{i + 1:03d}"
            failure_type = getattr(failure, "failure_type", None) or "unknown"
            root_cause = self.find_root_cause(failure)
            suggestion = suggestions[i] if i < len(suggestions) else (suggestions[-1] if suggestions else "N/A")
            lines.append(
                f"| {failure_id} | {failure_type} | {root_cause} | {suggestion} | Open |"
            )
        return "\n".join(lines)

    def generate_improvement_suggestions(
        self, failures: list[EvalResult]
    ) -> list[str]:
        if not failures:
            return []
 
        categories = self.categorize_failures(failures)
        suggestions: list[str] = []
 
        if categories.get("hallucination"):
            suggestions.append(
                "Implement hallucination checker to filter unsupported claims"
            )
        if categories.get("irrelevant"):
            suggestions.append(
                "Improve prompt clarity and intent detection to keep answers on-topic"
            )
        if categories.get("incomplete"):
            suggestions.append(
                "Add few-shot examples showing complete answers to improve completeness"
            )
        if categories.get("off_topic"):
            suggestions.append(
                "Review routing/intent classification to prevent off-topic responses"
            )
        if categories.get("refusal"):
            suggestions.append(
                "Relax overly strict guardrails causing unnecessary refusals"
            )
 
        # Ensure at least 3 generic suggestions if fewer categories matched
        generic_suggestions = [
            "Increase chunk size in RAG pipeline to reduce context fragmentation",
            "Expand golden dataset coverage to include more edge cases",
            "Calibrate LLM judge scores against human review on a sample batch",
        ]
        for s in generic_suggestions:
            if len(suggestions) >= 3:
                break
            if s not in suggestions:
                suggestions.append(s)
 
        return suggestions


# ---------------------------------------------------------------------------
# Entry point for manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    qa_pairs = [
        QAPair(
            question="What is RAG?",
            expected_answer="RAG stands for Retrieval-Augmented Generation, which combines retrieval with text generation.",
            context="RAG is a technique that retrieves relevant documents and uses them to ground LLM generation.",
            metadata={"difficulty": "easy", "category": "definition"},
        ),
        QAPair(
            question="What is the capital of France?",
            expected_answer="Paris is the capital of France.",
            context="France is a country in Western Europe. Its capital city is Paris.",
            metadata={"difficulty": "easy", "category": "factual"},
        ),
        QAPair(
            question="Explain backpropagation and why it matters for training",
            expected_answer="Backpropagation is an algorithm for training neural networks by computing gradients efficiently, enabling deep learning models to learn from errors.",
            context="Neural networks learn through gradient descent. Backpropagation efficiently computes these gradients layer by layer.",
            metadata={"difficulty": "medium", "category": "explanation"},
        ),
        QAPair(
            question="Should I use RAG or fine-tuning for my chatbot?",
            expected_answer="It depends on the use case: RAG is better for frequently updated knowledge, fine-tuning for consistent style/behavior. Consider cost, latency, and data freshness.",
            context="RAG retrieves external documents at inference time. Fine-tuning modifies model weights during training.",
            metadata={"difficulty": "hard", "category": "comparison"},
        ),
        QAPair(
            question="What is the meaning of life?",
            expected_answer="This question is outside the scope of this system. I can help with AI and technology questions.",
            context="This is an AI assistant specialized in technology topics.",
            metadata={"difficulty": "adversarial", "category": "out_of_scope"},
        ),
    ]
 
    evaluator = RAGASEvaluator()
    runner = BenchmarkRunner()
 
    def mock_agent(question: str) -> str:
        return f"Based on my knowledge: {question[:30]}... The answer involves key concepts."
 
    results = runner.run(qa_pairs, mock_agent, evaluator)
    report = runner.generate_report(results)
    print("=== Benchmark Report ===")
    for k, v in report.items():
        print(f"  {k}: {v}")
 
    failures = runner.identify_failures(results, threshold=0.5)
    print(f"\n=== Failures ({len(failures)}) ===")
    analyzer = FailureAnalyzer()
 
    categories = analyzer.categorize_failures(failures)
    print("Failure Categories:", categories)
 
    for f in failures:
        cause = analyzer.find_root_cause(f)
        print(f"  Root cause: {cause}")
 
    suggestions = analyzer.generate_improvement_suggestions(failures)
    print("\nImprovement Suggestions:")
    for s in suggestions:
        print(f"  - {s}")
 
    log = analyzer.generate_improvement_log(failures, suggestions)
    print("\n=== Improvement Log ===")
    print(log)