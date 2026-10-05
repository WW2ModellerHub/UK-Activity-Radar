from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.state import LiveState
from app.traffic_flow import TrafficFlowService


def main() -> int:
    print('UK Activity Radar v1.1 — live traffic-flow test')
    if not settings.tomtom_traffic_api_key:
        print('NOT CONFIGURED: run configure_traffic.bat first.')
        return 2
    service = TrafficFlowService(settings, LiveState())
    try:
        ok = service.self_test()
    except Exception as exc:
        print(f'FAILED: {type(exc).__name__}: {exc}')
        return 1
    status = service.status_payload()
    print('OK: live traffic tile received.' if ok else 'FAILED: no live traffic tile received.')
    print(status)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
