from dataclasses import dataclass, field


@dataclass
class AgentState:
    question: str
    evidence_ids: list[int] = field(default_factory=list)
