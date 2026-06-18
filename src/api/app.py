import os
import json
from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.config import logger, MOCK_MODE, sessions
from src.api.manager import manager
from src.tools.db import load_orders
from src.speech.speech_service import transcribe_audio_file, synthesize_speech
from src.vision.vision_service import analyze_image_contents
from src.sentiment.sentiment_service import track_sentiment_and_check_escalation
from src.agent.agent_service import run_agent_turn_stream

# FastAPI Setup
app = FastAPI(title="Real-Time Multi-Modal Customer Support Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    session_id: str
    message: str

@app.post("/api/chat")
async def chat_endpoint(req: ChatRequest):
    session_id = req.session_id
    if session_id not in sessions:
        sessions[session_id] = {
            "messages": [],
            "sentiment_history": [],
            "consecutive_negatives": 0,
            "escalated": False,
            "summary": None,
            "order_context": None,
            "last_response_id": None
        }

    # Run sentiment analysis & check escalation
    await track_sentiment_and_check_escalation(session_id, req.message)

    # Return stream
    return StreamingResponse(
        run_agent_turn_stream(session_id, req.message),
        media_type="text/event-stream"
    )

@app.post("/api/vision/analyze")
async def vision_analyze_endpoint(session_id: str, file: UploadFile = File(...)):
    if session_id not in sessions:
        sessions[session_id] = {
            "messages": [],
            "sentiment_history": [],
            "consecutive_negatives": 0,
            "escalated": False,
            "summary": None,
            "order_context": None,
            "last_response_id": None
        }

    file_bytes = await file.read()
    filename = file.filename
    
    # Run analysis
    analysis = await analyze_image_contents(file_bytes, filename)
    
    # Inject analysis into LLM session history as context
    context_msg = f"[Customer uploaded image '{filename}']. Vision Analysis: Caption: {analysis['caption']}. Identified Tags: {', '.join(analysis['tags'])}. OCR Extracted Text: '{analysis['ocr_text']}'"
    sessions[session_id]["messages"].append({
        "role": "system",
        "content": f"The user uploaded an image. Here is the visual context:\n{context_msg}"
    })
    
    logger.info(f"[VISION] Context appended to session {session_id}")
    return analysis

@app.post("/api/speech/transcribe")
async def speech_transcribe_endpoint(session_id: str, file: UploadFile = File(...)):
    file_bytes = await file.read()
    text = await transcribe_audio_file(file_bytes)
    return {"text": text}

@app.post("/api/speech/tts")
async def speech_tts_endpoint(text: str):
    audio_data = await synthesize_speech(text)
    if audio_data:
        from fastapi.responses import Response
        return Response(content=audio_data, media_type="audio/wav")
    raise HTTPException(status_code=400, detail="TTS Synthesizer not available or failed.")

@app.get("/api/session/{session_id}")
async def get_session_endpoint(session_id: str):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id]

@app.get("/api/orders")
async def get_orders_endpoint():
    return load_orders()

@app.post("/api/session/{session_id}/reset")
async def reset_session_endpoint(session_id: str):
    if session_id in sessions:
        sessions[session_id] = {
            "messages": [],
            "sentiment_history": [],
            "consecutive_negatives": 0,
            "escalated": False,
            "summary": None,
            "order_context": None,
            "last_response_id": None
        }
        return {"status": "success", "message": "Session reset."}
    return {"status": "ignored"}

class ReplyRequest(BaseModel):
    content: str

@app.post("/api/session/{session_id}/reply")
async def session_reply_endpoint(session_id: str, req: ReplyRequest):
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    
    sessions[session_id]["messages"].append({
        "role": "assistant",
        "content": f"[Human Agent]: {req.content}"
    })
    
    await manager.broadcast({
        "type": "agent_reply",
        "session_id": session_id,
        "content": req.content
    })
    return {"status": "success"}

@app.get("/api/config")
async def get_config_endpoint():
    return {"mock_mode": MOCK_MODE}

# WebSocket for supervisor/dashboard escalation notifications
@app.websocket("/api/ws/agent")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            message = json.loads(data)
            logger.info(f"[WS] Received dashboard event: {message}")
            
            if message.get("type") == "agent_reply":
                sid = message.get("session_id")
                content = message.get("content")
                if sid in sessions:
                    sessions[sid]["messages"].append({
                        "role": "assistant",
                        "content": f"[Human Agent]: {content}"
                    })
    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except Exception as e:
        logger.error(f"[WS] Error in websocket connection: {e}")
        manager.disconnect(websocket)

# Mount frontend static files
os.makedirs("frontend", exist_ok=True)
app.mount("/static", StaticFiles(directory="frontend"), name="static")

@app.get("/")
async def serve_index():
    return FileResponse("frontend/index.html")
