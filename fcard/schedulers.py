"""Small scheduler interface. Schedulers know nothing about Flask or SQLite."""

from dataclasses import dataclass
import random
from typing import Protocol


@dataclass(frozen=True)
class Rating:
    value: str
    label: str
    correct: bool


@dataclass(frozen=True)
class Control:
    value: str
    label: str


@dataclass(frozen=True)
class Candidate:
    question_id: int
    algorithm: str
    state: dict | None
    due_at: int | None
    reviews: int


@dataclass(frozen=True)
class Transition:
    state: dict
    due_at: int | None = None


class Scheduler(Protocol):
    id: str
    label: str
    ratings: tuple[Rating, ...]
    controls: tuple[Control, ...]

    def default_rating(self, verdict: str) -> str: ...
    def eligible(self, candidate: Candidate, now: int) -> bool: ...
    def choose(self, candidates: list[Candidate], now: int) -> Candidate: ...
    def review(
        self, state: dict | None, rating: str, now: int, control: str | None = None
    ) -> Transition: ...


class Buckets:
    id = "buckets-v1"
    label = "Buckets"
    ratings = (
        Rating("correct", "Correct", True),
        Rating("incorrect", "Incorrect", False),
    )
    controls = (
        Control("up5", "Then move up 5 extra buckets"),
        Control("down5", "Then move down 5 extra buckets"),
    )

    def __init__(self, rng=None):
        self.rng = rng if rng is not None else random.SystemRandom()

    @staticmethod
    def bucket(state):
        if state is None:
            return 0
        value = state.get("bucket")
        if type(value) is not int or not 0 <= value <= 10:
            raise ValueError("Invalid buckets-v1 state")
        return value

    def default_rating(self, verdict):
        if verdict not in {"correct", "incorrect"}:
            raise ValueError("Unknown verdict")
        return verdict

    def eligible(self, candidate, now):
        return True  # Buckets deliberately have no time gate.

    def choose(self, candidates, now):
        weights = [2 ** (-self.bucket(c.state)) for c in candidates]
        return self.rng.choices(candidates, weights=weights, k=1)[0]

    def review(self, state, rating, now, control=None):
        if rating not in {r.value for r in self.ratings}:
            raise ValueError("Unknown rating")
        if control not in {None, *(c.value for c in self.controls)}:
            raise ValueError("Unknown control")
        bucket = self.bucket(state)
        bucket = max(0, min(10, bucket + (1 if rating == "correct" else -3)))
        if control:
            bucket = max(0, min(10, bucket + (5 if control == "up5" else -5)))
        return Transition({"bucket": bucket})


class TimeBased:
    """Optional helper for future schedulers; no time-based algorithm is bundled."""

    def eligible(self, candidate, now):
        return candidate.due_at is None or candidate.due_at <= now

    def choose(self, candidates, now):
        return min(
            candidates,
            key=lambda c: (
                c.reviews == 0,
                c.due_at if c.due_at is not None else 0,
                c.question_id,
            ),
        )


def choose_question(candidates, registry, now, previous=None, last_algorithm=None):
    eligible = [
        c for c in candidates if registry[c.algorithm].eligible(c, now)
    ]
    if len(eligible) > 1:
        eligible = [c for c in eligible if c.question_id != previous]
    if not eligible:
        return None
    groups = {}
    for candidate in eligible:
        groups.setdefault(candidate.algorithm, []).append(candidate)
    algorithms = sorted(groups)
    # Rotate through all registered groups in the deck, including temporarily
    # ineligible groups, so a returning group is not perpetually starved.
    all_algorithms = sorted({c.algorithm for c in candidates})
    if last_algorithm in all_algorithms:
        index = all_algorithms.index(last_algorithm)
        cycle = all_algorithms[index + 1:] + all_algorithms[:index + 1]
        algorithm = next(a for a in cycle if a in groups)
    else:
        algorithm = algorithms[0]
    return registry[algorithm].choose(groups[algorithm], now)
