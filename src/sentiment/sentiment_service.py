import asyncio
from datetime import datetime
from typing import Dict, Any, List

from src.config import (
    logger,
    MOCK_MODE,
    AZURE_LANGUAGE_KEY,
    AZURE_LANGUAGE_ENDPOINT,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_DEPLOYMENT_NAME,
    sessions
)
from src.api.manager import manager

# Conditional imports
TextAnalyticsClient = None
AzureKeyCredential = None
OpenAI = None

if not MOCK_MODE:
    try:
        from azure.ai.textanalytics import TextAnalyticsClient
        from azure.core.credentials import AzureKeyCredential
        logger.info("Azure Language TextAnalytics SDK imported in sentiment service.")
    except ImportError:
        logger.warning("Failed to import azure-ai-textanalytics in sentiment service.")

    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("Failed to import openai inside sentiment service.")

async def analyze_message_sentiment(text: str) -> Dict[str, Any]:
    if MOCK_MODE or not TextAnalyticsClient or not AZURE_LANGUAGE_KEY:
        # Simple heuristic sentiment analysis
        text_lower = text.lower()
        negative_words = ["angry", "terrible", "worst", "broken", "useless", "hate", "refund", "frustrated", "delay", "damaged", "fail", "bad", "crap", "no", "stop", "rude", "poor", "annoyed", "garbage", "awful"]
        positive_words = ["thanks", "great", "helpful", "good", "love", "perfect", "excellent", "happy", "satisfied", "wonderful"]
        
        neg_count = sum(1 for word in negative_words if word in text_lower)
        pos_count = sum(1 for word in positive_words if word in text_lower)
        
        if neg_count > pos_count:
            sentiment = "negative"
            confidence = {"positive": 0.05, "neutral": 0.15, "negative": 0.80}
        elif pos_count > neg_count:
            sentiment = "positive"
            confidence = {"positive": 0.80, "neutral": 0.15, "negative": 0.05}
        else:
            sentiment = "neutral"
            confidence = {"positive": 0.10, "neutral": 0.80, "negative": 0.10}
            
        logger.info(f"[SENTIMENT] Mock sentiment: {sentiment} (Neg: {neg_count}, Pos: {pos_count})")
        return {
            "sentiment": sentiment,
            "confidence_scores": confidence
        }

    try:
        client = TextAnalyticsClient(
            endpoint=AZURE_LANGUAGE_ENDPOINT, 
            credential=AzureKeyCredential(AZURE_LANGUAGE_KEY)
        )
        # Call SDK in thread pool
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None, 
            lambda: client.analyze_sentiment(documents=[text])[0]
        )
        
        scores = response.confidence_scores
        logger.info(f"[SENTIMENT] Azure Sentiment: {response.sentiment} (Pos: {scores.positive}, Neg: {scores.negative})")
        return {
            "sentiment": response.sentiment,
            "confidence_scores": {
                "positive": scores.positive,
                "neutral": scores.neutral,
                "negative": scores.negative
            }
        }
    except Exception as e:
        logger.error(f"[SENTIMENT] Exception calling Azure Language: {e}")
        return {"sentiment": "neutral", "confidence_scores": {"positive": 0.33, "neutral": 0.34, "negative": 0.33}}

async def track_sentiment_and_check_escalation(session_id: str, text: str) -> Dict[str, Any]:
    session = sessions[session_id]
    analysis = await analyze_message_sentiment(text)
    sentiment = analysis["sentiment"]
    
    # Update rolling history
    session["sentiment_history"].append({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "text": text[:60] + "..." if len(text) > 60 else text,
        "sentiment": sentiment,
        "scores": analysis["confidence_scores"]
    })
    
    # Check for consecutive negative messages
    if sentiment == "negative":
        session["consecutive_negatives"] += 1
    else:
        session["consecutive_negatives"] = max(0, session["consecutive_negatives"] - 1 if sentiment == "positive" else session["consecutive_negatives"])
        if sentiment == "negative":
            session["consecutive_negatives"] += 1 

    # Explicit threshold check: if 3 consecutive negative messages, escalate!
    last_three = [msg["sentiment"] for msg in session["sentiment_history"][-3:]]
    is_escalating = len(last_three) >= 3 and all(s == "negative" for s in last_three)
    
    if is_escalating and not session["escalated"]:
        session["escalated"] = True
        logger.warning(f"[ESCALATION] Triggered for session {session_id} due to 3 consecutive negative sentiments!")
        
        # We will generate the escalation summary
        summary = await generate_escalation_summary(session_id)
        session["summary"] = summary
        
        # Broadcast to Human Agent Dashboard via WebSocket
        await manager.broadcast({
            "type": "escalation",
            "session_id": session_id,
            "summary": summary,
            "history": session["messages"],
            "sentiment_trend": [m["sentiment"] for m in session["sentiment_history"]]
        })
        
    return {
        "sentiment": sentiment,
        "confidence_scores": analysis["confidence_scores"],
        "consecutive_negatives": session["consecutive_negatives"],
        "escalated": session["escalated"],
        "summary": session["summary"]
    }

async def generate_escalation_summary(session_id: str) -> str:
    session = sessions[session_id]
    history_text = "\n".join([f"{msg['role']}: {msg['content']}" for msg in session["messages"]])
    
    # Calculate sentiment trend
    sentiment_labels = [h["sentiment"] for h in session["sentiment_history"]]
    trend = "Stable"
    if len(sentiment_labels) >= 2:
        if sentiment_labels[-1] == "negative" and sentiment_labels[0] != "negative":
            trend = "Worsening (frustrated)"
        elif sentiment_labels[-1] == "positive" and sentiment_labels[0] != "positive":
            trend = "Improving"
            
    # List actions taken
    actions = []
    for msg in session["messages"]:
        if msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                func = tc.get("function", {})
                actions.append(f"Used tool '{func.get('name')}' with arguments {func.get('arguments')}")
    if not actions:
        actions = ["None yet"]

    if MOCK_MODE or not AZURE_OPENAI_API_KEY or not OpenAI:
        # Generate mock summary
        await asyncio.sleep(0.5)
        
        # Guess the customer issue
        issue = "Customer is frustrated with product defects or delivery issues."
        for msg in reversed(session["messages"]):
            if msg["role"] == "user":
                content = msg["content"].lower()
                if "headphones" in content:
                    issue = "Customer received broken or damaged headphones and is seeking a refund."
                    break
                elif "laptop" in content or "arrive" in content:
                    issue = "Customer is inquiring about a late laptop delivery."
                    break
                elif "damage" in content:
                    issue = "Customer reports damaged package/shipping boxes."
                    break

        summary = f"""### 🚨 Escalation Summary
* **Identified Customer Issue:** {issue}
* **Customer Sentiment Trend:** {trend} ({' -> '.join(sentiment_labels)})
* **Actions Taken by AI Agent:**
{chr(10).join([f"  - {act}" for act in actions])}
* **Recommended Next Steps for Human Agent:**
  1. Review any uploaded photos for physical package damage.
  2. Issue a manual shipping refund voucher for the inconvenience.
  3. Confirm the customer's shipping address before issuing a replacement.
"""
        return summary

    # Live Azure OpenAI Summary Generation
    client = OpenAI(
        base_url=f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/v1",
        api_key=AZURE_OPENAI_API_KEY
    )
    
    summary_prompt = f"""Analyze the following customer support conversation history. Provide a concise, structured handoff summary for a human agent in Markdown.
Include:
1. Identified Customer Issue (1 sentence)
2. Customer Sentiment Trend (e.g. Worsening, Improving, Stable)
3. Actions Already Taken by AI Agent (tools called, order queries, refund outputs)
4. Recommended Next Steps for the human agent (bulleted list)

Conversation history:
{history_text}
"""
    try:
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: client.responses.create(
                model=AZURE_OPENAI_DEPLOYMENT_NAME,
                input=summary_prompt,
                max_output_tokens=300,
                temperature=0.3
            )
        )
        return response.output[0].content[0].text.strip()
    except Exception as e:
        logger.error(f"[AGENT] Summary generation failed: {e}")
        return f"Escalation Summary could not be generated. Trend: {trend}."
