import argparse
import os

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="OntoBridge local gateway and console")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    args = parser.parse_args()
    if os.environ.get("ONTOBRIDGE_DEMO", "1") == "1" and args.host not in ("127.0.0.1", "::1"):
        parser.error("演示模式必须绑定回环地址；私有部署请关闭 ONTOBRIDGE_DEMO 并配置访问令牌")
    uvicorn.run("ontobridge.api:create_app", factory=True, host=args.host, port=args.port, workers=1)
