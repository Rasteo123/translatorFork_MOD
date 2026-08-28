"""Qt-free contracts and user-facing metadata for optional QA capabilities."""

from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Literal


class QaCapabilityKey(StrEnum):
    RAZDEL = "razdel"
    LANGUAGE_TOOL = "language_tool"
    SLOVNET = "slovnet"
    COMETKIWI = "cometkiwi"


@dataclass(frozen=True, slots=True)
class QaCapabilitySettings:
    razdel_enabled: bool = True
    language_tool_enabled: bool = False
    slovnet_enabled: bool = False
    cometkiwi_enabled: bool = False

    def fingerprint(self) -> str:
        serialized = "".join(
            "1" if enabled else "0"
            for enabled in (
                self.razdel_enabled,
                self.language_tool_enabled,
                self.slovnet_enabled,
                self.cometkiwi_enabled,
            )
        )
        return sha256(serialized.encode("ascii")).hexdigest()[:20]


@dataclass(frozen=True, slots=True)
class QaCapabilityDescription:
    key: QaCapabilityKey
    title: str
    summary: str
    load_level: Literal["low", "medium", "high"]
    resources: tuple[str, ...]
    speed_impact: str
    quality_benefit: str
    quality_risk: str
    network_policy: str

    def __post_init__(self) -> None:
        allowed_resources = {"cpu", "memory", "gpu", "network"}
        invalid_resources = set(self.resources) - allowed_resources
        if invalid_resources:
            raise ValueError(
                "Unsupported QA capability resources: "
                + ", ".join(sorted(invalid_resources))
            )


@dataclass(frozen=True, slots=True)
class QaCapabilityRuntimeStatus:
    key: QaCapabilityKey
    state: Literal[
        "enabled",
        "disabled",
        "needs_setup",
        "installing",
        "available",
        "unavailable",
    ]
    reason: str = ""
    last_duration_seconds: float | None = None
    installed_size_bytes: int | None = None

    def __post_init__(self) -> None:
        if (
            self.last_duration_seconds is not None
            and self.last_duration_seconds < 0
        ):
            raise ValueError("last_duration_seconds must be non-negative")
        if self.installed_size_bytes is not None and self.installed_size_bytes < 0:
            raise ValueError("installed_size_bytes must be non-negative")


CAPABILITY_DESCRIPTIONS: dict[QaCapabilityKey, QaCapabilityDescription] = {
    QaCapabilityKey.RAZDEL: QaCapabilityDescription(
        key=QaCapabilityKey.RAZDEL,
        title="Razdel — улучшенная сегментация",
        summary="Точнее разделяет перевод на предложения и смысловые фрагменты.",
        load_level="low",
        resources=("cpu",),
        speed_impact="Добавляет небольшую нагрузку на CPU; время зависит от компьютера и текста.",
        quality_benefit="Помогает сопоставлять и проверять более точные фрагменты перевода.",
        quality_risk="Ошибки сегментации исходного текста могут повлиять на последующую проверку.",
        network_policy="Не требует сети.",
    ),
    QaCapabilityKey.LANGUAGE_TOOL: QaCapabilityDescription(
        key=QaCapabilityKey.LANGUAGE_TOOL,
        title="LanguageTool — орфография и грамматика",
        summary="Ищет орфографические, пунктуационные и грамматические ошибки.",
        load_level="medium",
        resources=("cpu", "memory", "network"),
        speed_impact="Проверка добавляет задержку, зависящую от способа запуска, текста и компьютера.",
        quality_benefit="Находит ошибки, которые не обязательно заметны при сравнении с исходником.",
        quality_risk="Автоматические правила могут давать ложные срабатывания на терминах и намеренных формулировках.",
        network_policy="Сетевой режим может отправлять текст внешнему сервису; локальный режим сети не требует.",
    ),
    QaCapabilityKey.SLOVNET: QaCapabilityDescription(
        key=QaCapabilityKey.SLOVNET,
        title="Slovnet/Navec — сущности, словоформы и связи",
        summary="Анализирует сущности, словоформы и синтаксические связи в тексте.",
        load_level="high",
        resources=("cpu", "memory"),
        speed_impact="Модель добавляет вычислительную нагрузку; длительность зависит от текста и компьютера.",
        quality_benefit="Даёт структурные признаки для более содержательной проверки перевода.",
        quality_risk="Ошибки NLP-анализа или несовпадение домена модели могут привести к неверным подсказкам.",
        network_policy="После установки работает локально и не требует сети.",
    ),
    QaCapabilityKey.COMETKIWI: QaCapabilityDescription(
        key=QaCapabilityKey.COMETKIWI,
        title="COMETKiwi — углублённая оценка",
        summary="Оценивает качество перевода с помощью отдельной нейросетевой модели.",
        load_level="high",
        resources=("cpu", "memory", "gpu"),
        speed_impact="Модель заметно увеличивает вычислительную нагрузку; время зависит от оборудования и размера текста.",
        quality_benefit="Помогает находить смысловые проблемы, не сводящиеся к совпадению слов.",
        quality_risk="Оценка модели вероятностна и может ошибаться на редких терминах, стилях и специализированных текстах.",
        network_policy="После установки работает локально и не требует сети.",
    ),
}
