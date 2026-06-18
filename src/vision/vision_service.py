import asyncio
import httpx
from typing import Dict, Any
from fastapi import HTTPException
from src.config import (
    logger,
    MOCK_MODE,
    AZURE_VISION_KEY,
    AZURE_VISION_ENDPOINT
)

async def analyze_image_contents(file_bytes: bytes, filename: str) -> Dict[str, Any]:
    if MOCK_MODE or not AZURE_VISION_KEY or not AZURE_VISION_ENDPOINT:
        await asyncio.sleep(1.0)
        logger.info(f"[VISION] Mocking image analysis for {filename}")
        
        filename_lower = filename.lower()
        if "damage" in filename_lower or "box" in filename_lower or "package" in filename_lower:
            return {
                "caption": "A heavily crushed cardboard box with shipping label, showing tears and water damage on the side.",
                "tags": ["box", "cardboard", "package", "damaged", "shipping", "delivery"],
                "ocr_text": "FRAGILE\nHANDLE WITH CARE\nTRACKING: TRK-987654321"
            }
        elif "headphones" in filename_lower or "broken" in filename_lower or "device" in filename_lower:
            return {
                "caption": "A pair of black over-ear wireless headphones with a clean crack splitting the plastic headband.",
                "tags": ["headphones", "electronics", "broken", "audio", "plastic", "damage"],
                "ocr_text": "WIRELESS HEADPHONES\nMODEL: WH-1000\nS/N: 2026-991A"
            }
        elif "error" in filename_lower or "screen" in filename_lower:
            return {
                "caption": "A screenshot of an e-commerce checkout page showing an error popup in red text.",
                "tags": ["screenshot", "webpage", "text", "error", "interface"],
                "ocr_text": "ERROR CODE: 503\nService Unavailable\nPlease contact customer support."
            }
        else:
            return {
                "caption": "A product related to a customer support inquiry.",
                "tags": ["product", "item", "customer_upload"],
                "ocr_text": "SERIAL NUMBER: SN-88271A\nORDER REF"
            }

    # Azure AI Vision v4.0 REST Call
    base_url = AZURE_VISION_ENDPOINT.rstrip('/')
    url = f"{base_url}/computervision/imageanalysis:analyze?api-version=2023-10-01&features=caption,tags,read"
    
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_VISION_KEY,
        "Content-Type": "application/octet-stream"
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=headers, content=file_bytes, timeout=15.0)
            
            if response.status_code != 200:
                logger.error(f"[VISION] Azure Vision API Error: {response.status_code} - {response.text}")
                raise HTTPException(status_code=500, detail="Azure Vision API returned an error.")
                
            result = response.json()
            
            # Parse results
            caption = result.get("captionResult", {}).get("text", "No description available.")
            tags = [tag.get("name") for tag in result.get("tagsResult", {}).get("values", [])]
            
            # OCR Text extraction
            ocr_lines = []
            for block in result.get("readResult", {}).get("blocks", []):
                for line in block.get("lines", []):
                    ocr_lines.append(line.get("text", ""))
            ocr_text = "\n".join(ocr_lines)
            
            logger.info(f"[VISION] Azure Vision Success. Caption: {caption}")
            return {
                "caption": caption,
                "tags": tags,
                "ocr_text": ocr_text
            }
    except Exception as e:
        logger.error(f"[VISION] Exception calling Azure Vision: {e}")
        # Fallback to simple mock instead of crashing
        return {
            "caption": "Uploaded customer image (analysis failed).",
            "tags": ["upload"],
            "ocr_text": ""
        }
