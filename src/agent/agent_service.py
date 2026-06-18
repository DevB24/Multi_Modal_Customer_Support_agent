import os
import json
import asyncio
import re
from typing import Dict, Any, List

from src.config import (
    logger,
    MOCK_MODE,
    AZURE_OPENAI_API_KEY,
    AZURE_OPENAI_ENDPOINT,
    AZURE_OPENAI_DEPLOYMENT_NAME,
    sessions
)
from src.tools.db import db_lookup_order, db_initiate_return

# Conditional imports
AsyncOpenAI = None
if not MOCK_MODE:
    try:
        from openai import AsyncOpenAI
        logger.info("Azure OpenAI SDK imported inside agent service.")
    except ImportError:
        logger.warning("Failed to import openai SDK inside agent service.")

SYSTEM_PROMPT = """You are a professional, empathetic, and solution-oriented e-commerce customer support agent.
Your goals:
1. Handle support requests politely and concisely. Ask clarifying questions only when necessary.
2. If a customer is asking about an order or delivery status, you must use the `lookup_order` tool. Ask for their Order ID or email if they haven't provided it.
3. If a customer is asking for a return or refund, you must use the `initiate_return_refund` tool. You must ask for the Order ID, the reason for the return, and which items they want to return before calling the tool.
4. Adhere to guardrails: Do not discuss topics outside of customer support, and do not make custom commitments or refunds the system tools do not support.
5. If the conversation is escalated to a human, inform the customer politely that a live human representative is joining the chat to resolve their issue.
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "lookup_order",
        "description": "Retrieve order status, items, delivery details, and tracking number for a given Order ID or customer email.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The Order ID (e.g. ORD-10001) or customer email address."
                }
            },
            "required": ["query"]
        }
    },
    {
        "type": "function",
        "name": "initiate_return_refund",
        "description": "Initiates a return and refund workflow for specific items in an order.",
        "parameters": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "string",
                    "description": "The Order ID (e.g. ORD-10001)."
                },
                "reason": {
                    "type": "string",
                    "description": "The customer's reason for returning the item."
                },
                "items_to_return": {
                    "type": "array",
                    "items": {
                        "type": "string"
                    },
                    "description": "The product names of the items to return."
                }
            },
            "required": ["order_id", "reason", "items_to_return"]
        }
    }
]

async def execute_tool_call(name: str, args: dict) -> Dict[str, Any]:
    logger.info(f"[TOOL USE] Executing {name} with args: {args}")
    if name == "lookup_order":
        result = db_lookup_order(args.get("query", ""))
        if result:
            return {"success": True, "data": result}
        return {"success": False, "message": "Order or customer email not found in our records."}
    elif name == "initiate_return_refund":
        return db_initiate_return(
            order_id=args.get("order_id", ""),
            reason=args.get("reason", ""),
            items_to_return=args.get("items_to_return", [])
        )
    return {"success": False, "message": f"Unknown tool: {name}"}

async def run_agent_turn_stream(session_id: str, user_message: str):
    session = sessions[session_id]
    
    # Append user message
    session["messages"].append({"role": "user", "content": user_message})
    
    if MOCK_MODE or not AZURE_OPENAI_API_KEY or not AsyncOpenAI:
        # Mock streaming agent response
        logger.info("[AGENT] Running Mock LLM Agent turn")
        await asyncio.sleep(0.5)
        
        # Basic parsing for local triggers
        message_lower = user_message.lower()
        response_text = ""
        tool_to_call = None
        tool_args = {}
        
        # Scenario 1: Order Lookup (ORD-10001, etc. or email)
        if "order" in message_lower or "ord-" in message_lower or "@" in message_lower:
            match = re.search(r'ord-\d+', message_lower)
            email_match = re.search(r'[\w\.-]+@[\w\.-]+', message_lower)
            query_val = None
            if match:
                query_val = match.group(0).upper()
            elif email_match:
                query_val = email_match.group(0)
                
            if query_val:
                tool_to_call = "lookup_order"
                tool_args = {"query": query_val}
            else:
                response_text = "I would be happy to help look up your order. Could you please provide your Order ID (e.g., ORD-10001) or the email address associated with your purchase?"
        
        # Scenario 2: Return/Refund
        elif "return" in message_lower or "refund" in message_lower or "send back" in message_lower:
            match = re.search(r'ord-\d+', message_lower)
            order_id = match.group(0).upper() if match else None
            
            # Guess items
            items = []
            if "headphones" in message_lower:
                items.append("Wireless Headphones")
            elif "mouse" in message_lower:
                items.append("Ergonomic Mouse")
            elif "keyboard" in message_lower:
                items.append("Mechanical Keyboard")
            elif "watch" in message_lower:
                items.append("Smart Watch")
            elif "wallet" in message_lower:
                items.append("Leather Wallet")
                
            if order_id and items:
                tool_to_call = "initiate_return_refund"
                tool_args = {
                    "order_id": order_id,
                    "reason": "Customer reported issue in chat (e.g. damaged or defective product).",
                    "items_to_return": items
                }
            elif not order_id:
                response_text = "I can certainly help you initiate a return or refund. Could you please share your Order ID so I can verify your items?"
            elif not items:
                response_text = f"I see you want to return items from order {order_id}. Which specific items would you like to return, and what is the reason for the return?"

        # Fallbacks/General support
        if not response_text and not tool_to_call:
            if "hello" in message_lower or "hi" in message_lower:
                response_text = "Hello! Welcome to our customer support. How can I assist you today? If you have questions about an order or want to return an item, please let me know!"
            elif session["escalated"]:
                response_text = "I've flagged this conversation for our support team, and a human agent will be with you shortly. Thank you for your patience."
            else:
                response_text = "Thank you for sharing that. To best assist you with this issue, could you please provide your Order ID or email so I can check your account details?"

        # Execute Tool if triggered
        if tool_to_call:
            # Emit tool call log
            yield f"data: {json.dumps({'type': 'tool_call', 'name': tool_to_call, 'arguments': tool_args})}\n\n"
            await asyncio.sleep(0.8)
            
            tool_result = await execute_tool_call(tool_to_call, tool_args)
            yield f"data: {json.dumps({'type': 'tool_result', 'name': tool_to_call, 'result': tool_result})}\n\n"
            await asyncio.sleep(0.5)
            
            # Formulate response based on tool result
            if tool_to_call == "lookup_order":
                if tool_result["success"]:
                    o = tool_result["data"]
                    items_desc = ", ".join([f"{i['quantity']}x {i['product_name']}" for i in o["items"]])
                    response_text = f"I found your order {o['order_id']} for {o['customer_name']}. The order status is **{o['status']}** (Delivered: {o['delivery_date'] or 'N/A'}). It contains: {items_desc}. Tracking number is {o['tracking_number'] or 'N/A'}. Is there anything else I can help you with regarding this order?"
                else:
                    response_text = f"I searched our records but couldn't find an order matching '{tool_args['query']}'. Could you please double-check the Order ID or email?"
            elif tool_to_call == "initiate_return_refund":
                if tool_result["success"]:
                    response_text = f"Successfully initiated your return! Your return reference number is **{tool_result['reference_number']}**. A refund will be processed within {tool_result['refund_timeline']}. {tool_result['return_instructions']}"
                else:
                    response_text = f"I'm sorry, I couldn't initiate the return. {tool_result['message']}"

        # Stream response text word by word
        words = response_text.split(" ")
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
            await asyncio.sleep(0.03)
            
        # Add assistant message to history
        session["messages"].append({"role": "assistant", "content": response_text})
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    if session.get("escalated"):
        logger.info("[AGENT] Session is escalated. Skipping Live OpenAI call and yielding handoff message.")
        response_text = "I've flagged this conversation for our support team, and a human agent will be with you shortly. Thank you for your patience."
        words = response_text.split(" ")
        for i, word in enumerate(words):
            chunk = word + (" " if i < len(words) - 1 else "")
            yield f"data: {json.dumps({'type': 'content', 'content': chunk})}\n\n"
            await asyncio.sleep(0.03)
        session["messages"].append({"role": "assistant", "content": response_text})
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
        return

    # ==========================================
    # LIVE AZURE OPENAI TURN
    # ==========================================
    client = AsyncOpenAI(
        base_url=f"{AZURE_OPENAI_ENDPOINT.rstrip('/')}/openai/v1",
        api_key=AZURE_OPENAI_API_KEY
    )

    try:
        if "last_response_id" not in session:
            session["last_response_id"] = None
            
        current_prev_id = session.get("last_response_id")
        
        # Build current_input from session history to include any system/OCR messages
        if not current_prev_id:
            input_items = []
            for msg in session["messages"]:
                role = msg["role"]
                content = msg.get("content")
                if role == "system":
                    role = "developer"
                if not content:
                    continue
                input_items.append({
                    "role": role,
                    "content": content
                })
            current_input = input_items
        else:
            last_assistant_idx = -1
            for idx in range(len(session["messages"]) - 2, -1, -1):
                if session["messages"][idx]["role"] == "assistant":
                    last_assistant_idx = idx
                    break
            new_messages = session["messages"][last_assistant_idx + 1:]
            input_items = []
            for msg in new_messages:
                role = msg["role"]
                content = msg.get("content")
                if role == "system":
                    role = "developer"
                if not content:
                    continue
                input_items.append({
                    "role": role,
                    "content": content
                })
            current_input = input_items

        if not current_input:
            current_input = user_message
        
        has_tool_calls = True
        
        while has_tool_calls:
            # Call responses.create stream
            async def get_stream(inp, prev_id):
                kwargs = {
                    "model": AZURE_OPENAI_DEPLOYMENT_NAME,
                    "instructions": SYSTEM_PROMPT,
                    "tools": TOOL_SCHEMAS,
                    "temperature": 0.2,
                    "stream": True
                }
                if prev_id:
                    kwargs["previous_response_id"] = prev_id
                    kwargs["input"] = inp
                else:
                    kwargs["input"] = inp
                print('kwargs:',kwargs)
                return await client.responses.create(**kwargs)
                
            response_stream = await get_stream(current_input, current_prev_id)
            
            assistant_content = ""
            active_tool_calls = []
            
            current_tool_call = None
            tool_args_accum = ""
            
            async for event in response_stream:
                event_type = event.type
                
                if event_type == "response.output_item.added":
                    item = getattr(event, "item", None)
                    if item and getattr(item, "type", None) == "function_call":
                        current_tool_call = {
                            "id": item.id,
                            "call_id": item.call_id,
                            "name": item.name,
                            "arguments": ""
                        }
                        tool_args_accum = ""
                        
                elif event_type == "response.function_call_arguments.delta":
                    delta_text = getattr(event, "delta", "")
                    tool_args_accum += delta_text
                    
                elif event_type == "response.function_call_arguments.done":
                    if current_tool_call:
                        current_tool_call["arguments"] = tool_args_accum
                        active_tool_calls.append(current_tool_call)
                        current_tool_call = None
                        
                elif event_type == "response.output_text.delta":
                    delta_text = getattr(event, "delta", "")
                    assistant_content += delta_text
                    yield f"data: {json.dumps({'type': 'content', 'content': delta_text})}\n\n"
                    await asyncio.sleep(0.001)
                    
                elif event_type == "response.completed":
                    response_obj = getattr(event, "response", None)
                    if response_obj:
                        session["last_response_id"] = response_obj.id
                        
                if hasattr(event, "response") and event.response:
                    session["last_response_id"] = event.response.id
                    
            if active_tool_calls:
                # Save the tool call message
                tool_calls_serialized = [
                    {
                        "id": tc["call_id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": tc["arguments"]}
                    } for tc in active_tool_calls
                ]
                session["messages"].append({
                    "role": "assistant",
                    "content": assistant_content if assistant_content else None,
                    "tool_calls": tool_calls_serialized
                })
                
                tool_outputs = []
                for tc in active_tool_calls:
                    yield f"data: {json.dumps({'type': 'tool_call', 'name': tc['name'], 'arguments': json.loads(tc['arguments'])})}\n\n"
                    
                    args = json.loads(tc["arguments"])
                    tool_result = await execute_tool_call(tc["name"], args)
                    
                    yield f"data: {json.dumps({'type': 'tool_result', 'name': tc['name'], 'result': tool_result})}\n\n"
                    
                    session["messages"].append({
                        "role": "tool",
                        "tool_call_id": tc["call_id"],
                        "name": tc["name"],
                        "content": json.dumps(tool_result)
                    })
                    
                    tool_outputs.append({
                        "type": "function_call_output",
                        "call_id": tc["call_id"],
                        "output": json.dumps(tool_result)
                    })
                    
                current_input = tool_outputs
                current_prev_id = session["last_response_id"]
            else:
                has_tool_calls = False
                if assistant_content:
                    session["messages"].append({"role": "assistant", "content": assistant_content})
                yield f"data: {json.dumps({'type': 'done'})}\n\n"

    except Exception as e:
        logger.error(f"[AGENT] Live OpenAI Call Failed: {e}")
        err_msg = "I encountered an error communicating with the AI service. Please check API credentials or retry."
        yield f"data: {json.dumps({'type': 'content', 'content': err_msg})}\n\n"
        yield f"data: {json.dumps({'type': 'done'})}\n\n"
