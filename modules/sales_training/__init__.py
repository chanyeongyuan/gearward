"""
modules.sales_training — Sales Training module (blueprint §4.1).

Connects to CLIENT HubSpot via OAuth. Reads pipeline + deals + activity.
Tier-0/1: analysis-heavy, low-volume. Outputs playbooks + daily guidance.
"""
from .graph import build_sales_training_graph, SalesTrainingState

__all__ = ["build_sales_training_graph", "SalesTrainingState"]
