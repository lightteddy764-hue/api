import os
import json
import time
import uuid
import logging
import threading
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from starlette.concurrency import run_in_threadpool, iterate_in_threadpool
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from metaso_core import MetasoCore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("metaso_api")

app = FastAPI(title="Metaso OpenAI Compatible API", version="1.1.0")

# Enable CORS for browser-based clients (NextChat, LibreChat, OpenWebUI)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

security = HTTPBearer()

API_KEY = os.getenv("API_KEY", "sk-metaso-eni-lo-forever-2026")
core_client = None
client_lock = threading.Lock()

def get_client_sync() -> MetasoCore:
    global core_client
    with client_lock:
        if core_client is None:
            logger.info("Initializing Metaso core client...")
            core_client = MetasoCore(headless=True)
        return core_client

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key. Authentication failed.")
    return credentials

class ChatMessage(BaseModel):
    role: str = "user"
    content: str = ""

class ChatRequest(BaseModel):
    model: str = "metaso-qa-with-agent"
    messages: List[Dict[str, Any]] = []
    stream: bool = False
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

@app.on_event("startup")
def startup_event():
    # Pre-warm the client on background startup
    threading.Thread(target=get_client_sync, daemon=True).start()

def format_messages_to_prompt(messages: List[Dict[str, Any]]) -> str:
    """Format conversation turns to preserve multi-turn context."""
    if not messages:
        return ""
    if len(messages) == 1:
        return str(messages[0].get("content", ""))
    
    formatted_turns = []
    for msg in messages[:-1]:
        role = str(msg.get("role", "user")).capitalize()
        content = str(msg.get("content", ""))
        formatted_turns.append(f"[{role}]: {content}")
    
    current_content = str(messages[-1].get("content", ""))
    return (
        "Conversation History:\n"
        + "\n".join(formatted_turns)
        + f"\n\n[Current User Message]: {current_content}"
    )

@app.get("/")
def health_check():
    return {
        "status": "ok",
        "service": "Metaso OpenAI Wrapper",
        "version": "1.1.0"
    }

@app.get("/v1/models")
@app.get("/models")
def list_models():
    """Returns available models for OpenAI-compatible frontends."""
    models = [
        {"id": "metaso-qa-with-agent", "object": "model", "created": 1700000000, "owned_by": "metaso"},
        {"id": "gpt-4o", "object": "model", "created": 1700000000, "owned_by": "openai"},
        {"id": "gpt-3.5-turbo", "object": "model", "created": 1700000000, "owned_by": "openai"},
        {"id": "claude-3-5-sonnet", "object": "model", "created": 1700000000, "owned_by": "anthropic"}
    ]
    return {
        "object": "list",
        "data": models
    }

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, auth=Depends(verify_api_key)):
    client = await run_in_threadpool(get_client_sync)
    prompt = format_messages_to_prompt(req.messages)

    chat_id = f"chatcmpl-{uuid.uuid4()}"
    created = int(time.time())
    model = req.model

    # STREAMING MODE (stream=true)
    if req.stream:
        async def generate_openai_stream():
            in_think_block = False

            async for event_type, chunk in iterate_in_threadpool(client.stream_chat(prompt)):
                if event_type == "error":
                    chunk_data = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": f"\n\n[Error: {chunk}]"}, "finish_reason": "error"}]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"
                    break

                if event_type == "reasoning":
                    if not in_think_block:
                        in_think_block = True
                        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': '<think>\\n'}, 'finish_reason': None}]})}\n\n"

                    chunk_data = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"

                elif event_type == "content":
                    if in_think_block:
                        in_think_block = False
                        yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': '\\n</think>\\n\\n'}, 'finish_reason': None}]})}\n\n"

                    chunk_data = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                    }
                    yield f"data: {json.dumps(chunk_data)}\n\n"

            if in_think_block:
                yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': '\\n</think>\\n\\n'}, 'finish_reason': None}]})}\n\n"

            final_data = {
                "id": chat_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
            }
            yield f"data: {json.dumps(final_data)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(generate_openai_stream(), media_type="text/event-stream")

    # NON-STREAMING MODE (stream=false)
    def collect_full_response():
        full_reasoning = []
        full_content = []
        for event_type, chunk in client.stream_chat(prompt):
            if event_type == "reasoning":
                full_reasoning.append(chunk)
            elif event_type == "content":
                full_content.append(chunk)
            elif event_type == "error":
                raise RuntimeError(chunk)

        result = ""
        if full_reasoning:
            result += f"<think>\n{''.join(full_reasoning)}\n</think>\n\n"
        result += "".join(full_content)
        return result

    try:
        reply_content = await run_in_threadpool(collect_full_response)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Upstream provider error: {str(e)}")

    prompt_words = len(prompt.split()) if prompt else 0
    completion_words = len(reply_content.split()) if reply_content else 0

    return {
        "id": chat_id,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": reply_content
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": prompt_words,
            "completion_tokens": completion_words,
            "total_tokens": prompt_words + completion_words
        }
    }
