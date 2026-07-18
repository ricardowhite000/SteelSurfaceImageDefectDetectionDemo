from __future__ import annotations

import argparse
import json
from pathlib import Path

from steel_platform.infrastructure.config import PlatformSettings
from steel_platform.infrastructure.workbench_executor import execute_job


def main() -> int:
    parser = argparse.ArgumentParser(description="钢材视觉平台人工任务执行器")
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    if not args.settings.is_file():
        parser.error(f"执行器设置文件不存在：{args.settings}")
    settings = PlatformSettings.model_validate(
        json.loads(args.settings.read_text(encoding="utf-8"))
    )
    status = execute_job(settings, args.job)
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
