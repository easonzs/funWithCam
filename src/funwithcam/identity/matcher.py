from collections import deque
from dataclasses import dataclass
from math import sqrt
from typing import Deque, Iterable, Optional, Sequence, Tuple


KNOWN_IDENTITIES = ("child", "parent")


@dataclass(frozen=True)
class FaceTemplate:
    profile_id: int
    role: str
    name: str
    embedding: Tuple[float, ...]


@dataclass(frozen=True)
class FrameMatch:
    identity: str
    name: Optional[str]
    score: Optional[float]
    reason: str


@dataclass(frozen=True)
class VerificationResult:
    identity: str
    name: Optional[str]
    score: Optional[float]
    confirmed: bool
    reason: str
    matching_frames: int


def normalize_embedding(values: Iterable[float]) -> Tuple[float, ...]:
    embedding = tuple(float(value) for value in values)
    magnitude = sqrt(sum(value * value for value in embedding))
    if magnitude == 0:
        raise ValueError("face embedding cannot be zero")
    return tuple(value / magnitude for value in embedding)


def average_embeddings(
    embeddings: Sequence[Sequence[float]],
) -> Tuple[float, ...]:
    if not embeddings:
        raise ValueError("at least one face embedding is required")
    width = len(embeddings[0])
    if width == 0 or any(len(embedding) != width for embedding in embeddings):
        raise ValueError("face embeddings must have the same non-zero length")
    averages = (
        sum(float(embedding[index]) for embedding in embeddings) / len(embeddings)
        for index in range(width)
    )
    return normalize_embedding(averages)


def cosine_similarity(
    left: Sequence[float],
    right: Sequence[float],
) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("face embeddings must have the same non-zero length")
    left_normalized = normalize_embedding(left)
    right_normalized = normalize_embedding(right)
    return sum(
        left_value * right_value
        for left_value, right_value in zip(left_normalized, right_normalized)
    )


class IdentityMatcher:
    def __init__(
        self,
        templates: Sequence[FaceTemplate],
        match_threshold: float,
        ambiguity_margin: float,
    ):
        self._templates = tuple(templates)
        self._match_threshold = match_threshold
        self._ambiguity_margin = ambiguity_margin

    def match(self, embedding: Sequence[float]) -> FrameMatch:
        if not self._templates:
            return FrameMatch("unknown", None, None, "no_profiles")

        best_by_role = {}
        for template in self._templates:
            score = cosine_similarity(embedding, template.embedding)
            current = best_by_role.get(template.role)
            if current is None or score > current[0]:
                best_by_role[template.role] = (score, template)

        ranked = sorted(best_by_role.values(), key=lambda item: item[0], reverse=True)
        best_score, best_template = ranked[0]
        if best_score < self._match_threshold:
            return FrameMatch("unknown", None, best_score, "below_threshold")

        if len(ranked) > 1 and best_score - ranked[1][0] < self._ambiguity_margin:
            return FrameMatch("unknown", None, best_score, "ambiguous")

        return FrameMatch(
            best_template.role,
            best_template.name,
            best_score,
            "matched",
        )


class MultiFrameVerifier:
    def __init__(self, required_frames: int, window_seconds: float):
        if required_frames <= 0:
            raise ValueError("required_frames must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._required_frames = required_frames
        self._window_seconds = window_seconds
        self._observations: Deque[Tuple[float, FrameMatch]] = deque()

    def update(self, match: FrameMatch, monotonic_at: float) -> VerificationResult:
        self._observations.append((monotonic_at, match))
        cutoff = monotonic_at - self._window_seconds
        while self._observations and self._observations[0][0] < cutoff:
            self._observations.popleft()

        if match.identity not in KNOWN_IDENTITIES:
            return VerificationResult(
                "unknown",
                None,
                match.score,
                False,
                match.reason,
                0,
            )

        known = [
            item
            for _, item in self._observations
            if item.identity in KNOWN_IDENTITIES
        ]
        if any(item.identity != match.identity for item in known):
            return VerificationResult(
                "unknown",
                None,
                match.score,
                False,
                "conflicting_frames",
                0,
            )

        matching = [item for item in known if item.identity == match.identity]
        confirmed = len(matching) >= self._required_frames
        return VerificationResult(
            match.identity if confirmed else "unknown",
            match.name if confirmed else None,
            match.score,
            confirmed,
            "confirmed" if confirmed else "collecting_frames",
            len(matching),
        )
