from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_id: Literal["pasilla", "synthetic"] = "pasilla"
    treatment: str = "treated"
    reference: str = "untreated"
    alpha: float = Field(default=0.05, ge=0.001, le=0.2)
    lfc_threshold: float = Field(default=1.0, ge=0, le=5)
    adjust_library_type: bool = True
    min_count: int = Field(default=10, ge=1, le=100)
    min_samples: int = Field(default=3, ge=2, le=4)
    rationale: str = Field(default="控制测序类型差异，执行 treated 相对 untreated 的差异表达分析。", max_length=1000)

    @property
    def formula(self):
        return "~ library_type + condition" if self.adjust_library_type else "~ condition"


class TaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(min_length=5, max_length=3000)
    dataset_id: Literal["pasilla", "synthetic"] = "pasilla"
    planner: Literal["offline", "live"] = "offline"
    recovery_demo: bool = False


class Approval(BaseModel):
    plan_hash: str
