import os, sys
from loguru import logger

# logger
os.makedirs("logs", exist_ok=True)
logger.remove()
logger.add("logs/output.log", level="INFO", colorize=False)
logger.add(sys.stdout, format="{message}", level="INFO", colorize=True)