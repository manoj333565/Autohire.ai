"""
agent/nodes/pdf_drip_node.py — Safe HR Drip Outreach Node (Compatibility Wrapper)
================================================================================
Wraps agent/nodes/drip_node.py for backward compatibility.
Supports both CSV drip staging (DripService) and legacy PDF HR contact preview.
"""

from agent.nodes.drip_node import drip_campaign_node, pdf_drip_node, hr_drip_node

__all__ = ["drip_campaign_node", "pdf_drip_node", "hr_drip_node"]
