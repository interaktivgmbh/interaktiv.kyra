from typing import Optional, List, TypedDict


class PromptMetadata(TypedDict):
    categories: Optional[List[str]]
    action: Optional[str]


class PromptData(TypedDict):
    name: str
    description: Optional[str]
    prompt: str
    metadata: PromptMetadata


class InstructionData(TypedDict):
    query: str
    text: str
    useContext: bool
