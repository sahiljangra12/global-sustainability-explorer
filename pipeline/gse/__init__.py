"""Global Sustainability Explorer data pipeline.

Fetches open sustainability datasets (UN SDG Global Database, World Bank WDI,
Our World in Data), normalises them into one long table, validates them, and
exports Parquet files (for the chat API) plus compact JSON files (for the
static explorer website).
"""

__version__ = "0.1.0"
