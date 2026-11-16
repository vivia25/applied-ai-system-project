import logging
import os

from pawpal_ai.retriever import ROOT

LOG_PATH = os.path.join(ROOT, "logs", "pawpal_ai.log")


def setup_logging() -> None:
    """Send pawpal.* logs to logs/pawpal_ai.log (idempotent, safe under Streamlit reruns)."""
    logger = logging.getLogger("pawpal")
    if logger.handlers:
        return
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
