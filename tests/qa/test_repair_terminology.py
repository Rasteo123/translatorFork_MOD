"""A restored paragraph must speak the book's language, not the model's own."""

from __future__ import annotations

import pytest

from gemini_translator.qa.glossary_audit import (
    contains_term_forms,
    glossary_violation_reason,
)
from gemini_translator.qa.models import GlossaryPolicy, RelevantGlossaryTerm


_SOURCE = "極致屬性和均衡屬性之論"


def _term(original: str, canonical: str, policy=GlossaryPolicy.MUST_TRANSLATE):
    return RelevantGlossaryTerm(original, canonical, policy, 1, 0)


# --- canonical wording ------------------------------------------------------


def test_a_synonym_instead_of_the_canonical_translation_is_refused():
    """Наблюдалось вживую: «экстремальный» вместо «предельный» во всей книге."""
    glossary = (_term("極致屬性", "предельный атрибут"),)

    reason = glossary_violation_reason(
        "Кто-то выдвинул теорию об экстремальных атрибутах.", _SOURCE, glossary
    )

    assert reason == "canonical_term_missing"


def test_the_canonical_translation_counts_in_any_inflected_form():
    """Русский термин почти всегда стоит в косвенном падеже — это не нарушение."""
    glossary = (_term("極致屬性", "предельный атрибут"),)

    reason = glossary_violation_reason(
        "В книге рассуждали о предельных атрибутах и их пользе.", _SOURCE, glossary
    )

    assert reason == ""


def test_a_term_absent_from_the_gap_is_not_demanded_in_the_fragment():
    """Проверяется только то, что действительно было в пропущенном куске."""
    glossary = (_term("魂師", "духовный мастер"),)

    reason = glossary_violation_reason("Он открыл книгу.", _SOURCE, glossary)

    assert reason == ""


def test_a_term_cannot_exist_without_a_canonical_translation():
    """Проверка канона опирается на контракт: пустого канона в глоссарии не бывает."""
    from gemini_translator.qa.models import QaModelValidationError

    with pytest.raises(QaModelValidationError):
        _term("極致屬性", "   ")


@pytest.mark.parametrize(
    ("fragment", "policy", "expected"),
    [
        ("Он оставил 極致屬性 нетронутым.", GlossaryPolicy.MUST_TRANSLATE, "original_term_kept"),
        ("Он перевёл название целиком.", GlossaryPolicy.KEEP_ORIGINAL, "original_term_dropped"),
        ("Он оставил 極致屬性 как есть.", GlossaryPolicy.KEEP_ORIGINAL, ""),
    ],
)
def test_the_two_older_violations_keep_their_own_names(fragment, policy, expected):
    """Обобщённое «glossary_violation» не говорило, что именно сломалось."""
    canonical = (
        "極致屬性" if policy is GlossaryPolicy.KEEP_ORIGINAL else "предельный атрибут"
    )
    glossary = (_term("極致屬性", canonical, policy),)

    assert glossary_violation_reason(fragment, _SOURCE, glossary) == expected


def test_the_matcher_still_refuses_a_different_word_with_a_shared_prefix():
    """Стемный матч не должен принимать «предел» за «предельный атрибут»."""
    assert contains_term_forms("о предельных атрибутах", "предельный атрибут") is True
    assert contains_term_forms("о пределах прочности", "предельный атрибут") is False


# --- the style window -------------------------------------------------------


def _units(texts):
    from gemini_translator.qa.models import SemanticInlineSpan, SemanticUnit

    return tuple(
        SemanticUnit(
            unit_id=f"t{index}",
            document_id="target-doc",
            block_id=f"b{index}",
            ordinal=index,
            text=text,
            normalized_text=text,
            source_start=0,
            source_end=len(text),
            kind="paragraph",
            inline_spans=(SemanticInlineSpan(f"i{index}", 0, len(text), 0, len(text)),),
        )
        for index, text in enumerate(texts)
    )


def _candidate_with_anchors(left_target: str, right_target: str):
    import hashlib

    from gemini_translator.qa.foreign_text_filter import ForeignTextFilter
    from gemini_translator.qa.models import (
        AlignmentSpan,
        CandidateContext,
        GapCandidate,
        VerifiedCandidate,
    )

    candidate_id = "gap-" + hashlib.sha256(b"window").hexdigest()[:20]
    left = AlignmentSpan(("s0",), (left_target,), 0.95, "1:1")
    right = AlignmentSpan(("s2",), (right_target,), 0.95, "1:1")
    candidate = GapCandidate(
        candidate_id, "source", ("s1",), (), left, right, True, ("missing_in_target",)
    )
    context = CandidateContext(
        candidate_id=candidate_id,
        source_text="原文二",
        target_text="",
        source_before="原文一",
        source_after="原文三",
        target_before="Якорь слева.",
        target_after="Якорь справа.",
        source_language="zh",
        target_language="ru",
        candidate_language="zh",
    )
    from gemini_translator.qa.llm.schemas import OmissionVerdict

    verdict = OmissionVerdict(
        decision="missing_content",
        confidence=0.95,
        source_unit_ids=("s1",),
        missing_facts=("Потеряно.",),
        explanation="Проверка.",
    )
    return VerifiedCandidate(
        candidate=candidate,
        context=context,
        verdict=verdict,
        foreign_text_decision=ForeignTextFilter().classify(candidate, context),
        eligible_for_repair=True,
        status="verified",
    )


def test_the_window_carries_the_sentences_around_the_gap_in_reading_order():
    """Двух якорей мало, чтобы перенять терминологию сцены."""
    from gemini_translator.qa.service import _style_windows

    units = _units(
        [
            "Далеко позади.",
            "Ближе.",
            "Совсем рядом слева.",
            "Совсем рядом справа.",
            "Дальше.",
            "Ещё дальше.",
            "Слишком далеко.",
        ]
    )
    item = _candidate_with_anchors("t2", "t3")

    before, after = _style_windows(item, units)

    assert before == ("Далеко позади.", "Ближе.")
    assert after == ("Дальше.", "Ещё дальше.", "Слишком далеко.")


def test_a_window_at_the_edge_of_a_chapter_is_simply_shorter():
    """Первый абзац главы не имеет соседей слева — это не ошибка."""
    from gemini_translator.qa.service import _style_windows

    units = _units(["Первый.", "Второй."])
    item = _candidate_with_anchors("t0", "t1")

    before, after = _style_windows(item, units)

    assert before == ()
    assert after == ()


def test_an_oversized_neighbour_never_crowds_out_the_prompt():
    """Окно — подсказка, а не вторая глава в запросе."""
    from gemini_translator.qa.service import _STYLE_WINDOW_CHARS, _style_windows

    units = _units(["я" * (_STYLE_WINDOW_CHARS + 1), "Короткий.", "Левый.", "Правый."])
    item = _candidate_with_anchors("t2", "t3")

    before, _after = _style_windows(item, units)

    assert before == ("Короткий.",)


def test_an_unknown_anchor_yields_no_window_instead_of_guessing():
    from gemini_translator.qa.service import _style_windows

    units = _units(["Первый.", "Второй."])
    item = _candidate_with_anchors("t9", "t8")

    assert _style_windows(item, units) == ((), ())


def test_copying_a_window_sentence_is_rejected_as_an_echo():
    """Окно даёт слова, а не текст: скопированный сосед — не перевод пропуска."""
    from gemini_translator.qa.llm import CancellationToken, QaModelSelection
    from gemini_translator.qa.llm.omission_repairer import (
        RepairContext,
        _rejection_detail,
    )
    from gemini_translator.qa.llm.schemas import RepairProposal

    item = _candidate_with_anchors("t0", "t1")
    neighbour = "Тан Юань видел эту книгу в Павильоне Книг Императорской Академии."
    request = RepairContext(
        chapter_id="chapter-1",
        model=QaModelSelection("gemini", "m"),
        cancellation=CancellationToken(),
        target_window_before=(neighbour,),
    )
    echoed = RepairProposal(
        candidate_id=item.candidate.candidate_id,
        translated_fragment=f"{neighbour} И ещё немного.",
        glossary_terms_used=(),
    )
    fresh = RepairProposal(
        candidate_id=item.candidate.candidate_id,
        translated_fragment="Совершенно новый перевод потерянного абзаца.",
        glossary_terms_used=(),
    )

    assert _rejection_detail(echoed, item, (), request) == "window_echo"
    assert _rejection_detail(fresh, item, (), request) == ""


def test_a_one_character_entry_never_demands_its_canon():
    """9 % глоссария книги — одиночные иероглифы, попадающие внутрь чужих слов."""
    glossary = (_term("修", "совершенствоваться"),)

    # 修 стоит внутри 修煉, и перевод абзаца не обязан содержать это слово.
    reason = glossary_violation_reason(
        "Одни считают, что мастер должен довести атрибут до предела.",
        "有的人提倡魂師應該選擇一個屬性修煉到極致",
        glossary,
    )

    assert reason == ""


def test_a_term_inside_a_longer_matched_term_is_that_term():
    """«Тан» внутри «Тан Юань» — не второе понятие, которое нужно назвать отдельно."""
    glossary = (_term("唐", "Тан"), _term("唐元", "Тан Юань"))

    satisfied = glossary_violation_reason("Тан Юань открыл книгу.", "唐元看到過這本書", glossary)
    missing = glossary_violation_reason("Он открыл книгу.", "唐元看到過這本書", glossary)

    assert satisfied == ""
    assert missing == "canonical_term_missing"


def test_a_real_multi_character_term_still_has_to_appear():
    """Ради этого проверка и существует: канон книги важнее выдумки модели."""
    glossary = (_term("魂師", "духовный мастер"),)

    assert glossary_violation_reason(
        "Одни считают, что мастер души должен выбрать один атрибут.",
        "有的人提倡魂師應該選擇一個屬性",
        glossary,
    ) == "canonical_term_missing"
    assert glossary_violation_reason(
        "Одни считают, что духовный мастер должен выбрать один атрибут.",
        "有的人提倡魂師應該選擇一個屬性",
        glossary,
    ) == ""
