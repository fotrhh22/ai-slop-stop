"""Versioned GPU telemetry contract; J is canonical and Wh is derived here."""
from __future__ import annotations

from typing import Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnergyMeasurement(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1] = 1
    measurement_id: UUID
    status: Literal["measured", "unavailable"]
    energy_j: Optional[float] = Field(default=None, gt=0)
    duration_s: float = Field(ge=0)
    reason: Optional[str] = None
    method: Literal["zeus_nvml"] = "zeus_nvml"
    scope: Literal["gpu_board_generation_window"] = "gpu_board_generation_window"
    includes_idle: Literal[True] = True
    gpu_name: str
    gpu_uuid: str
    model_id: str
    model_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    zeus_version: str
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    steps: int = Field(gt=0)
    seed: int = Field(ge=0)
    started_at: str
    finished_at: str

    @model_validator(mode="after")
    def valid_status(self):
        if self.status == "measured" and self.energy_j is None:
            raise ValueError("measured energy requires positive Joules")
        if self.status == "unavailable" and (self.energy_j is not None or not self.reason):
            raise ValueError("unavailable energy requires a reason and null Joules")
        return self

    def public(self):
        return {**self.model_dump(mode="json"),
                "energy_wh": self.energy_j / 3600 if self.energy_j is not None else None}


class RouterEnergyMeasurement(EnergyMeasurement):
    scope: Literal["gpu_board_router_window"] = "gpu_board_router_window"
    width: Optional[int] = None
    height: Optional[int] = None
    steps: Optional[int] = None
    seed: Optional[int] = None
    input_tokens: Optional[int] = Field(default=None, ge=0)
    output_tokens: Optional[int] = Field(default=None, ge=0)
    max_new_tokens: int = 256
    thinking: Literal[False] = False


def energy_summary(calls):
    measured, missing, total = 0, 0, 0.0
    routers, router_missing, router_j = 0, 0, 0.0
    for call in calls:
        if call["operation"] == "router":
            try:
                router = RouterEnergyMeasurement.model_validate(call.get("energy_measurement"))
                if router.status != "measured":
                    raise ValueError("unavailable")
                routers += 1
                router_j += router.energy_j
            except (ValueError, TypeError):
                router_missing += 1
            continue
        if call["operation"] != "generate":
            continue
        value = call.get("energy_measurement")
        try:
            item = EnergyMeasurement.model_validate(value)
        except (ValueError, TypeError):
            missing += 1
            continue
        if item.status == "measured":
            measured += 1
            total += item.energy_j
        else:
            missing += 1
    return {"scope": "gpu_board_generation_window", "measured_generation_calls": measured,
            "unmeasured_generation_calls": missing,
            "unmeasured_router_calls": router_missing,
            "measured_router_calls": routers,
            "router_energy_wh": router_j / 3600 if routers else None,
            "total_gpu_energy_wh": (total + router_j) / 3600 if measured or routers else None,
            "energy_j": total if measured else None,
            "energy_wh": total / 3600 if measured else None,
            "generation_complete": measured > 0 and missing == 0}
