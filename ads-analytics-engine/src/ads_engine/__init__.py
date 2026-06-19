"""Hubara Ads Analytics Engine — deterministic blended unit-economics.

The contract of this package: **the LLM never computes a metric.** Every number
comes from the pure functions here, which are golden-tested with hand-computed
expected values. The agent orchestrates (fetch via Meta MCP, narrate) and this
engine does the arithmetic.
"""

__version__ = "0.1.0"
