from collections import namedtuple

from django.test import SimpleTestCase

from services.ranking import Signals, diversify, is_hidden, rank, reasons, score_parts, total

Res = namedtuple("Res", "pk title source")


def res(pk, title="A video", source="fake"):
    return Res(pk, title, source)


def score(title="A video", source="fake", level=None, **signal_values):
    parts = score_parts(title=title, source=source, signals=Signals(**signal_values), level=level)
    return total(parts)


def pks(resources):
    return [r.pk for r in resources]


class OwnFeedbackTests(SimpleTestCase):  # unit tests
    def test_a_neutral_resource_scores_zero(self):
        self.assertEqual(score(), 0)

    def test_exact_weights(self):
        self.assertEqual(score(rating="like"), 5.0)
        self.assertEqual(score(rating="dislike"), -50.0)
        self.assertEqual(score(progress="done"), -10.0)
        self.assertEqual(score(progress="skipped"), -30.0)
        self.assertEqual(score(rating="like", progress="done"), -5.0)

    def test_order_of_preference(self):
        self.assertGreater(score(rating="like"), score())
        self.assertGreater(score(), score(progress="done"))
        self.assertGreater(score(progress="done"), score(progress="skipped"))
        self.assertGreater(score(progress="skipped"), score(rating="dislike"))

    def test_only_dislikes_and_skips_are_hidden(self):
        self.assertTrue(is_hidden(Signals(rating="dislike")))
        self.assertTrue(is_hidden(Signals(progress="skipped")))
        self.assertFalse(is_hidden(Signals(progress="done")))
        self.assertFalse(is_hidden(Signals(rating="like", progress="done")))
        self.assertFalse(is_hidden(Signals()))


class LevelTests(SimpleTestCase):
    def test_beginners_prefer_beginner_titles(self):
        self.assertEqual(score(title="Python for beginners", level="beginner"), 2.0)
        self.assertEqual(score(title="Advanced Python", level="beginner"), -2.0)
        self.assertEqual(score(title="Python", level="beginner"), 0)

    def test_intermediate_learners_prefer_advanced_titles(self):
        self.assertEqual(score(title="Python for beginners", level="intermediate"), -1.0)
        self.assertEqual(score(title="Advanced Python", level="intermediate"), 2.0)

    def test_the_middle_level_only_avoids_advanced_titles(self):
        self.assertEqual(score(title="Python for beginners", level="some"), 0)
        self.assertEqual(score(title="Advanced Python", level="some"), -1.0)

    def test_unknown_level_is_neutral(self):  # edge case
        for level in (None, "wizard"):
            self.assertEqual(score(title="Python for beginners", level=level), 0)
            self.assertEqual(score(title="Advanced Python", level=level), 0)

    def test_words_must_match_whole_and_case_is_ignored(self):
        self.assertEqual(score(title="Introspection and mindfulness", level="beginner"), 0)
        self.assertEqual(score(title="PYTHON FOR BEGINNERS", level="beginner"), 2.0)
        self.assertEqual(score(title="A beginner's guide", level="beginner"), 2.0)

    def test_both_kinds_of_word_cancel_out(self):
        self.assertEqual(score(title="Advanced introduction", level="beginner"), 0)


class SourceTests(SimpleTestCase):
    def test_real_sources_get_a_bonus_and_others_do_not(self):
        self.assertEqual(score(source="youtube"), 3.0)
        self.assertEqual(score(source="openlibrary"), 3.0)
        self.assertEqual(score(source="wikipedia"), 3.0)
        self.assertEqual(score(source="github"), 3.0)
        self.assertEqual(score(source="search"), 0)
        self.assertEqual(score(source="fake"), 0)
        self.assertEqual(score(source="never-heard-of-it"), 0)

    def test_a_dislike_outweighs_the_source_bonus(self):
        self.assertEqual(score(source="youtube", rating="dislike"), -47.0)


class RankTests(SimpleTestCase):
    def test_orders_by_score_for_the_level(self):
        items = [res(1, "Advanced Python"), res(2, "Python for beginners"), res(3, "Python")]
        self.assertEqual(pks(rank(items, {}, "beginner")), [2, 3, 1])

    def test_ties_break_by_id_whatever_the_input_order(self):
        items = [res(3), res(1), res(2)]
        self.assertEqual(pks(rank(items, {}, None)), [1, 2, 3])
        self.assertEqual(pks(rank(list(reversed(items)), {}, None)), [1, 2, 3])

    def test_input_is_not_modified(self):
        items = [res(2), res(1)]
        rank(items, {}, None)
        self.assertEqual(pks(items), [2, 1])

    def test_empty_input(self):
        self.assertEqual(rank([], {}, None), [])

    def test_disliked_and_skipped_are_dropped_when_enough_remain(self):
        items = [res(1), res(2), res(3), res(4)]
        signals = {1: Signals(rating="dislike"), 2: Signals(progress="skipped")}
        self.assertEqual(pks(rank(items, signals, None)), [3, 4])

    def test_hidden_ones_are_dropped_even_when_that_empties_a_step(self):
        items = [res(1), res(2), res(3)]
        signals = {1: Signals(rating="dislike"), 2: Signals(progress="skipped")}
        self.assertEqual(pks(rank(items, signals, None)), [3])

    def test_a_single_hidden_resource_is_dropped_not_brought_back(self):
        self.assertEqual(rank([res(1)], {1: Signals(rating="dislike")}, None), [])

    def test_done_resources_stay_but_sink(self):
        items = [res(1), res(2)]
        self.assertEqual(pks(rank(items, {1: Signals(progress="done")}, None)), [2, 1])

    def test_real_sources_beat_search_links(self):
        items = [res(1, source="search"), res(2, source="youtube")]
        self.assertEqual(pks(rank(items, {}, None)), [2, 1])

    def test_google_books_is_a_trusted_source(self):
        items = [res(1, source="search"), res(2, source="googlebooks")]
        self.assertEqual(pks(rank(items, {}, None)), [2, 1])


class DiversifyTests(SimpleTestCase):
    def test_round_robins_sources_and_keeps_the_best_first(self):
        items = [
            res(1, source="openlibrary"),
            res(2, source="openlibrary"),
            res(3, source="wikipedia"),
            res(4, source="googlebooks"),
        ]
        self.assertEqual(pks(diversify(items)), [1, 3, 4, 2])

    def test_one_source_keeps_its_order(self):
        items = [res(3, source="fake"), res(1, source="fake"), res(2, source="fake")]
        self.assertEqual(pks(diversify(items)), [3, 1, 2])

    def test_empty_input(self):
        self.assertEqual(diversify([]), [])

    def test_input_is_not_modified(self):
        items = [res(1, source="a"), res(2, source="b")]
        diversify(items)
        self.assertEqual(pks(items), [1, 2])


class ReasonTests(SimpleTestCase):
    def reasons_for(self, title="A video", source="fake", level=None, **signal_values):
        parts = score_parts(title=title, source=source, signals=Signals(**signal_values), level=level)
        return reasons(parts)

    def test_nothing_special_gives_no_reason(self):
        self.assertEqual(self.reasons_for(), [])

    def test_each_reason(self):
        self.assertEqual(self.reasons_for(rating="like"), ["You liked this"])
        self.assertEqual(self.reasons_for(title="for beginners", level="beginner"), ["Matches your level"])
        self.assertEqual(self.reasons_for(source="youtube"), ["Top result from a real source"])

    def test_negative_signals_give_no_reason(self):
        self.assertEqual(self.reasons_for(rating="dislike"), [])
        self.assertEqual(self.reasons_for(title="Advanced Python", level="beginner"), [])

    def test_reasons_come_in_a_fixed_order(self):
        result = self.reasons_for(
            rating="like", title="for beginners", level="beginner", source="youtube"
        )
        self.assertEqual(
            result,
            [
                "You liked this",
                "Matches your level",
                "Top result from a real source",
            ],
        )

    def test_reasons_never_reveal_counts(self):
        result = self.reasons_for(
            rating="like", title="for beginners", level="beginner", source="youtube"
        )
        self.assertFalse(any(ch.isdigit() for text in result for ch in text))