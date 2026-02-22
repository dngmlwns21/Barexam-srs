from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

class ChatRequest(BaseModel):
    card_id: str
    message: str

class ChatResponse(BaseModel):
    response: str

@router.post("/explain", response_model=ChatResponse)
async def chat_explain(request: ChatRequest):
    """
    AI Tutor endpoint to explain a specific card.
    """
    # Placeholder implementation
    return {"response": "This is a placeholder response from the AI Tutor. Real RAG integration coming in Phase 3."}
