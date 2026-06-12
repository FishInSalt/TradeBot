"""python -m src.webui — 本机启动观察台。"""
import os
import uvicorn

if __name__ == "__main__":
    uvicorn.run("src.webui.app:app", host="127.0.0.1",
                port=int(os.environ.get("TRADEBOT_WEBUI_PORT", "8000")), reload=False)
