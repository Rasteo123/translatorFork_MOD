from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RatioProfile:
    key: str
    source_languages: frozenset[str]
    target_language: str
    minimum: float
    maximum: float

    def contains(self, value: float) -> bool:
        return self.minimum <= value <= self.maximum


CJK_TO_RUSSIAN = RatioProfile(
    key="cjk_to_ru",
    source_languages=frozenset({"zh", "ja", "ko"}),
    target_language="ru",
    minimum=2.80,
    maximum=3.30,
)
ALPHABETIC_TO_RUSSIAN = RatioProfile(
    key="alphabetic_to_ru",
    source_languages=frozenset(),
    target_language="ru",
    minimum=0.92,
    maximum=1.20,
)


def _base_language(value: str) -> str:
    return value.strip().lower().replace("_", "-").split("-", 1)[0]


def get_ratio_profile(source_language: str, target_language: str) -> RatioProfile:
    source = _base_language(source_language)
    target = _base_language(target_language)
    if target == "ru" and source in CJK_TO_RUSSIAN.source_languages:
        return CJK_TO_RUSSIAN
    if target == "ru":
        return ALPHABETIC_TO_RUSSIAN
    raise KeyError(f"Unsupported language pair: {source_language}->{target_language}")


def validation_ratio_presets() -> dict[str, tuple[float, float, str]]:
    return {
        "Алфавитный (A -> A)": (
            ALPHABETIC_TO_RUSSIAN.minimum,
            ALPHABETIC_TO_RUSSIAN.maximum,
            "Ожидаемое соотношение перевод/оригинал для En/Fr/De -> Ru",
        ),
        "Иероглифический (象 -> A)": (
            CJK_TO_RUSSIAN.minimum,
            CJK_TO_RUSSIAN.maximum,
            "Ожидаемое перевод/оригинал для Zh/Jp/Ko -> Ru; если меньше x2.8, это уже подозрительно",
        ),
    }
