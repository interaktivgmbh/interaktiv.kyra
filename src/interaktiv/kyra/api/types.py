from typing import Optional, List, TypedDict


class PromptMetadata(TypedDict, total=False):
    categories: Optional[List[str]]
    action: Optional[str]


class PromptData(TypedDict, total=False):
    name: str
    description: str
    prompt: str
    metadata: PromptMetadata


class InstructionData(TypedDict, total=False):
    query: str
    text: str
    useContext: bool
