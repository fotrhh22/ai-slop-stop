"""Strict model-independent verdict contract for GPU and backend."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

class Criteria(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject: Literal["MATCH", "MISMATCH"]
    composition: Literal["MATCH", "MISMATCH"]
    color: Literal["MATCH", "MISMATCH"]
    background: Literal["MATCH", "MISMATCH"]

class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verdict: Literal["PASS", "FAIL"]
    reason: str = Field(min_length=1, max_length=500)
    criteria: Criteria
