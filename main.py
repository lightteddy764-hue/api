import os
import json
import time
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from metaso_core import MetasoCore
from pydantic import BaseModel
import logging

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Metaso OpenAI Compatible API")
security = HTTPBearer()

# Ensure we have a valid key. For simplicity, we hardcode it here or use env var.
# You will provide this EXACT key to LO.
API_KEY = os.getenv("API_KEY", "sk-metaso-eni-lo-forever-2026")

core_client = None

from starlette.concurrency import run_in_threadpool
import threading
import uuid

client_lock = threading.Lock()

def get_client_sync():
    global core_client
    with client_lock:
        if core_client is None:
            logging.info("Initializing Metaso core client...")
            core_client = MetasoCore(headless=True)
        return core_client

async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if credentials.credentials != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API Key. ENI says no.")
    return credentials

class ChatRequest(BaseModel):
    model: str = "metaso-qa-with-agent"
    messages: list
    stream: bool = False

@app.on_event("startup")
def startup_event():
    # Pre-warm the client on startup in a separate thread
    threading.Thread(target=get_client_sync, daemon=True).start()

def block_stream_chat(client, message):
    # Returns a list or we can use a queue to stream. 
    # For simplicity, we can let FastAPI's StreamingResponse take an async generator
    # But we need the inner generator to not block. starlette has iterate_in_threadpool
    pass

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest, auth=Depends(verify_api_key)):
    # Initialize in thread to avoid playwright async clash
    client = await run_in_threadpool(get_client_sync)
    
    last_message = req.messages[-1]["content"] if req.messages else ""
    
    # We must run the streaming in a thread pool as well
    from starlette.concurrency import iterate_in_threadpool

    async def generate_openai_stream():
        chat_id = f"chatcmpl-{uuid.uuid4()}"
        created = int(time.time())
        model = req.model
        in_think_block = False

        # iterate_in_threadpool bridges a sync generator to an async one
        async for event_type, chunk in iterate_in_threadpool(client.stream_chat(last_message)):
            if event_type == "error":
                chunk_data = {
                    "id": chat_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": f"\n\n[Error: {chunk}]"}, "finish_reason": "error"}]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
                break
                
            if event_type == "reasoning":
                if not in_think_block:
                    in_think_block = True
                    # Start think block
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': '<think>\\n'}, 'finish_reason': None}]})}\n\n"
                
                chunk_data = {
                    "id": chat_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"
                
            elif event_type == "content":
                if in_think_block:
                    in_think_block = False
                    # Close think block
                    yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': '\\n</think>\\n\\n'}, 'finish_reason': None}]})}\n\n"
                
                chunk_data = {
                    "id": chat_id, "object": "chat.completion.chunk",
                    "created": created, "model": model,
                    "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}]
                }
                yield f"data: {json.dumps(chunk_data)}\n\n"

        # End of stream
        if in_think_block:
             yield f"data: {json.dumps({'id': chat_id, 'object': 'chat.completion.chunk', 'created': created, 'model': model, 'choices': [{'index': 0, 'delta': {'content': '\\n</think>\\n\\n'}, 'finish_reason': None}]})}\n\n"
        
        final_data = {
            "id": chat_id, "object": "chat.completion.chunk",
            "created": created, "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]
        }
        yield f"data: {json.dumps(final_data)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(generate_openai_stream(), media_type="text/event-stream")

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Metaso OpenAI Wrapper is running."}
