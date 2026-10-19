import random
import unittest

from fcard.schedulers import (
    Buckets, Candidate, TimeBased, choose_question,
)


class CaptureRandom:
    def choices(self, population, weights, k):
        self.population = population
        self.weights = weights
        return [population[0]]


class SchedulerTest(unittest.TestCase):
    def candidate(self, id, bucket=0, algorithm="buckets-v1", reviews=1, due=None):
        return Candidate(id, algorithm, {"bucket": bucket}, due, reviews)

    def test_bucket_transitions_bounds_and_controls(self):
        b = Buckets()
        self.assertEqual(b.review(None, "correct", 0).state, {"bucket": 1})
        self.assertEqual(b.review(None, "incorrect", 0).state, {"bucket": 0})
        self.assertEqual(b.review({"bucket": 10}, "correct", 0).state, {"bucket": 10})
        self.assertEqual(b.review({"bucket": 8}, "incorrect", 0).state, {"bucket": 5})
        self.assertEqual(b.review({"bucket": 8}, "correct", 0, "up5").state, {"bucket": 10})
        self.assertEqual(b.review({"bucket": 8}, "incorrect", 0, "down5").state, {"bucket": 0})
        self.assertIsNone(b.review(None, "correct", 0).due_at)
        for state in ({}, {"bucket": -1}, {"bucket": 11}, {"bucket": True}):
            with self.assertRaises(ValueError):
                b.bucket(state)

    def test_weights_and_immediate_repeat_exclusion(self):
        rng = CaptureRandom()
        b = Buckets(rng)
        pool = [self.candidate(1, 0), self.candidate(2, 1), self.candidate(3, 10)]
        b.choose(pool, 0)
        self.assertEqual(rng.weights, [1, 0.5, 1 / 1024])
        picked = choose_question(pool, {b.id: b}, 0, previous=1)
        self.assertEqual(picked.question_id, 2)
        self.assertEqual([c.question_id for c in rng.population], [2, 3])
        picked = choose_question(pool[:1], {b.id: b}, 0, previous=1)
        self.assertEqual(picked.question_id, 1)

    def test_bucket_distribution(self):
        b = Buckets(random.Random(1))
        pool = [self.candidate(1, 0), self.candidate(2, 3)]
        low = sum(b.choose(pool, 0).question_id == 1 for _ in range(5000))
        self.assertTrue(4300 < low < 4600)

    def test_mixed_round_robin_and_time_helper(self):
        b = Buckets(CaptureRandom())
        clock = TimeBased()
        registry = {b.id: b, "clock": clock}
        pool = [
            self.candidate(1),
            self.candidate(2, algorithm="clock", due=10),
            self.candidate(3, algorithm="clock", reviews=0),
            self.candidate(4, algorithm="clock", due=100),
        ]
        picked = choose_question(pool, registry, now=20, last_algorithm=b.id)
        self.assertEqual(picked.question_id, 2)
        picked = choose_question(pool, registry, now=20, last_algorithm="clock")
        self.assertEqual(picked.question_id, 1)
        self.assertIsNone(choose_question(pool[-1:], registry, now=20))
        self.assertEqual(choose_question([], registry, now=20), None)
        # Bucket questions ignore even accidental future due dates.
        self.assertTrue(b.eligible(self.candidate(1, due=10**10), now=0))


if __name__ == "__main__":
    unittest.main()
