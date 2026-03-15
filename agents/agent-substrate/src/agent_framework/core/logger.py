import logging
import sys
import io
from pythonjsonlogger import jsonlogger

class CustomJsonFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter ensuring consistent fields for all logs.
    Adds `timestamp` and ensures `level` is uppercase.
    """
    def add_fields(self, log_record, record, message_dict):
        super(CustomJsonFormatter, self).add_fields(log_record, record, message_dict)
        if not log_record.get('timestamp'):
            # this doesn't use record.created, so it is slightly off but standard ISO format
            from datetime import datetime
            log_record['timestamp'] = datetime.utcnow().isoformat()
        if log_record.get('level'):
            log_record['level'] = log_record['level'].upper()
        else:
            log_record['level'] = record.levelname

def setup_logging(level=logging.INFO, service_name="agent-framework"):
    """
    Configures the root logger with the CustomJsonFormatter.
    Call this once at application startup.
    """
    logger = logging.getLogger()
    # Remove existing handlers to avoid duplicates if called multiple times
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        
    # In Jupyter notebooks sys.stdout is an OutStream with no .buffer attribute;
    # wrap only when the real binary buffer is available (normal terminal / server).
    if hasattr(sys.stdout, "buffer"):
        stream = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    else:
        stream = sys.stdout
    handler = logging.StreamHandler(stream)
    formatter = CustomJsonFormatter(
        '%(timestamp)s %(level)s %(name)s %(message)s',
        json_ensure_ascii=False
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(level)

# Create a module-level logger for internal use
logger = logging.getLogger("agent_framework")
