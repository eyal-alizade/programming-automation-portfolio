"""Public entry point. Run `uvicorn stockpilot:create_app --factory`."""
from stockpilot_core.app import create_app
from stockpilot_core.schemas import MAX_ID, MAX_STOCK

__all__ = ["create_app", "MAX_ID", "MAX_STOCK"]
