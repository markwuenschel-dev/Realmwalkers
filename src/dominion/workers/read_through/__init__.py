"""Read-through (ADR 0035): an editor's developmental notes on author-supplied chapter snapshots.

One model call per chapter plus one cross-chapter book pass. The pure pieces live here with no DB or
network: ``anchors`` places the model's quotes back onto the raw snapshot text (never choosing between
repeated passages), ``prompts`` is the single prompt builder shared by admission-time estimation and
execution, and ``validate`` enforces the response bounds item by item.
"""
