import os
import json
import random
from datetime import datetime
from typing import List, Dict, Any, Optional
from src.config import logger

DB_FILE = os.path.join("mock-data", "orders.json")

def load_orders() -> List[Dict[str, Any]]:
    try:
        if os.path.exists(DB_FILE):
            with open(DB_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Error loading database: {e}")
    return []

def save_orders(orders: List[Dict[str, Any]]):
    try:
        with open(DB_FILE, "w") as f:
            json.dump(orders, f, indent=2)
    except Exception as e:
        logger.error(f"Error saving database: {e}")

def db_lookup_order(query: str) -> Optional[Dict[str, Any]]:
    orders = load_orders()
    query_clean = query.strip().lower()
    for order in orders:
        if order["order_id"].lower() == query_clean:
            return order
        if order["customer_email"].lower() == query_clean:
            return order
    return None

def db_initiate_return(order_id: str, reason: str, items_to_return: List[str]) -> Dict[str, Any]:
    orders = load_orders()
    order = None
    for o in orders:
        if o["order_id"].lower() == order_id.strip().lower():
            order = o
            break

    if not order:
        return {"success": False, "message": f"Order {order_id} not found."}

    if order["status"] == "returned":
        return {"success": False, "message": f"Order {order_id} has already been returned."}

    if order["status"] != "delivered":
        return {"success": False, "message": f"Order {order_id} is not eligible for return because its current status is '{order['status']}'."}

    # Verify 30-day return window from delivery
    delivery_date_str = order.get("delivery_date")
    if not delivery_date_str:
        return {"success": False, "message": "Delivery date not recorded. Unable to verify return window."}

    try:
        # Current simulated date: 2026-06-17
        current_date = datetime(2026, 6, 17)
        delivery_date = datetime.strptime(delivery_date_str, "%Y-%m-%d")
        days_since_delivery = (current_date - delivery_date).days
        if days_since_delivery > 30:
            return {
                "success": False,
                "message": f"Return window expired. The order was delivered on {delivery_date_str} ({days_since_delivery} days ago). Returns must be initiated within 30 days of delivery."
            }
    except Exception as e:
        logger.error(f"Error checking return window: {e}")
        return {"success": False, "message": "Error verifying return eligibility window."}

    # Verify items exist in order
    ordered_item_names = [item["product_name"].lower() for item in order["items"]]
    invalid_items = []
    for item in items_to_return:
        if item.lower() not in ordered_item_names:
            invalid_items.append(item)

    if invalid_items:
        valid_items_list = ", ".join([item["product_name"] for item in order["items"]])
        return {
            "success": False,
            "message": f"The following items do not exist in this order: {', '.join(invalid_items)}. Ordered items are: {valid_items_list}."
        }

    # Update order status in mock database
    order["status"] = "returned"
    save_orders(orders)

    ref_num = f"RET-{order_id.split('-')[-1]}-{random.randint(10000, 99999)}"
    logger.info(f"[TOOL USE] Refund initiated for {order_id}. Items: {items_to_return}. Ref: {ref_num}")

    return {
        "success": True,
        "reference_number": ref_num,
        "refund_timeline": "3-5 business days after warehouse receipt",
        "return_instructions": "A return shipping label has been generated. Please pack the items securely, attach the label, and drop off at any shipping facility.",
        "message": f"Return successfully initiated. Reference: {ref_num}."
    }
