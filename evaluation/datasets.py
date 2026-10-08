import json
from pathlib import Path

from pydantic import BaseModel, Field

from utils.settings import PROJECT_ROOT

DEFAULT_DATASET = PROJECT_ROOT / "evaluation" / "questions.json"


class EvalQuestion(BaseModel):
    question: str
    expected: str | None = None
    expected_contact: str | None = None
    expected_tools: list[str] = Field(default_factory=list)

    @property
    def reference(self) -> str | None:
        parts = [
            p for p in [self.expected, self.expected_contact and f"Mentions contact: {self.expected_contact}"] if p
        ]
        return "; ".join(parts) or None


def load_dataset(path: Path | str = DEFAULT_DATASET) -> list[EvalQuestion]:
    data = json.loads(Path(path).read_text())
    return [EvalQuestion.model_validate(item) for item in data]
