from typing import Annotated, Literal, Optional
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

Rating = Annotated[int, Field(strict=True, ge=1, le=5)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Start(Strict):
    consent: Literal[True]
    profile_id: Optional[Literal["runpod", "gemini", "mock"]] = None


class PreSurvey(Strict):
    ai_frequency: Annotated[int, Field(strict=True, ge=0, le=4)]
    image_experience: Annotated[int, Field(strict=True, ge=0, le=3)]
    education: Annotated[int, Field(strict=True, ge=0, le=3)]
    error_strategy: Annotated[int, Field(strict=True, ge=0, le=5)]
    difficulties: list[Annotated[int, Field(strict=True, ge=0, le=6)]] = Field(min_length=1, max_length=2)
    writing: list[Rating] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def choices(self):
        if len(set(self.difficulties)) != len(self.difficulties) or (any(v >= 5 for v in self.difficulties) and len(self.difficulties) > 1):
            raise ValueError("difficulty choices conflict")
        return self


class FeatureRating(Strict):
    need: Rating
    intention: Rating


class PostSurvey(Strict):
    writing: list[Rating] = Field(min_length=4, max_length=4)
    learning: Optional[list[Rating]] = Field(default=None, min_length=4, max_length=4)
    revision: Optional[str] = Field(default=None, max_length=1000)
    learning_help: Optional[Rating] = None
    learning_interest: Rating
    features: dict[str, FeatureRating]
    revisit: list[Rating] = Field(min_length=6, max_length=6)
    priority: list[Literal["optimizer", "recommendation", "tip", "game", "garden", "quiz", "unsure"]] = Field(min_length=1, max_length=2)
    feedback: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def validate_features(self):
        if set(self.features) != {"optimizer", "recommendation", "tip", "game", "garden"}:
            raise ValueError("all five features required")
        if len(set(self.priority)) != len(self.priority) or ("unsure" in self.priority and len(self.priority) > 1):
            raise ValueError("priority choices conflict")
        return self


class Attempt(Strict):
    attempt_no: int = Field(default=1, ge=1, le=2)
    prompt: str = Field(min_length=1, max_length=2000)


class TrainingAttempt(Strict):
    idempotency_key: UUID
    selected: list[str] = Field(min_length=1, max_length=25)


class Event(Strict):
    event: Literal["hidden", "visible", "fullscreen_exit", "fullscreen_enter", "paste_blocked", "drop_blocked", "fullscreen_unavailable"]


class Draft(Strict):
    phase: Literal["pre", "post"]
    text: str = Field(max_length=2000)


class RuntimeRegistration(Strict):
    pod_id: str = Field(pattern=r'^[a-zA-Z0-9]{5,40}$')


class TestSkip(Strict):
    expected_state: str = Field(max_length=40)
