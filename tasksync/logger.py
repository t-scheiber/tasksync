"""Logging configuration for TaskSync."""

import logging
import sys

try:
    import colorlog
    HAS_COLORLOG = True
except ImportError:
    HAS_COLORLOG = False


def setup_logger(name: str = "tasksync", level: str = "INFO") -> logging.Logger:
    """Set up and return a configured logger.
    
    Args:
        name: Logger name.
        level: Log level (DEBUG, INFO, WARNING, ERROR).
        
    Returns:
        Configured logger instance.
    """
    # Always use the root tasksync logger to avoid duplicate messages
    root_name = name.split('.')[0] if '.' in name else name
    logger = logging.getLogger(root_name)
    
    # Avoid adding handlers multiple times
    if logger.handlers:
        return logger
    
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False  # Don't propagate to root logger
    
    # Create console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.DEBUG)
    
    # Format string
    fmt = "%(asctime)s | %(levelname)-8s | %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"
    
    if HAS_COLORLOG:
        formatter = colorlog.ColoredFormatter(
            f"%(log_color)s{fmt}",
            datefmt=datefmt,
            log_colors={
                "DEBUG": "cyan",
                "INFO": "green",
                "WARNING": "yellow",
                "ERROR": "red",
                "CRITICAL": "red,bg_white",
            },
        )
    else:
        formatter = logging.Formatter(fmt, datefmt=datefmt)
    
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    
    return logger
