from __future__ import annotations

from pydantic import BaseModel


class Mention(BaseModel):
    position: int
    timecode: float | None = None


class Node(BaseModel):
    id: str
    label: str
    type: str
    enriched: bool
    mentions: list[Mention]


class Edge(BaseModel):
    source: str
    target: str
    label: str


class GraphResponse(BaseModel):
    nodes: list[Node]
    edges: list[Edge]
    enriched: bool
