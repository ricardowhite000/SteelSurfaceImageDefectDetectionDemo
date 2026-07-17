from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload={"timestamp":datetime.now(timezone.utc).isoformat(),"level":record.levelname,"logger":record.name,"message":record.getMessage()}
        for key in ("request_id","project_id","job_id","run_id","method","path","status_code","duration_ms"):
            value=getattr(record,key,None)
            if value is not None:payload[key]=value
        if record.exc_info:payload["exception"]=self.formatException(record.exc_info)
        return json.dumps(payload,ensure_ascii=False)


def configure_logging(log_root: Path) -> None:
    log_root.mkdir(parents=True,exist_ok=True);formatter=JsonFormatter();root=logging.getLogger("steel_platform");root.setLevel(logging.INFO)
    if root.handlers:return
    console=logging.StreamHandler();console.setFormatter(formatter);file_handler=RotatingFileHandler(log_root/"platform.jsonl",maxBytes=5*1024*1024,backupCount=5,encoding="utf-8");file_handler.setFormatter(formatter);root.addHandler(console);root.addHandler(file_handler)
