import uvicorn
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from .api.servers import router as servers_router
from .api.executor import router as executor_router
from .api.templates import router as templates_router
from .api.logs import router as logs_router
from .backup.api import router as backup_router
from .backup.scheduler import start_backup_scheduler, stop_backup_scheduler
from .websocket import handle_websocket
from .config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_backup_scheduler()
    yield
    stop_backup_scheduler()

app = FastAPI(
    title="远程命令执行和脚本管理平台",
    description="内网SSH批量命令执行、脚本管理、实时输出，包含配置与数据备份还原系统",
    version="1.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_check():
    return {
        "status": "ok",
        "servers_loaded": len(settings.servers),
        "ssh_pool_size": settings.ssh_pool_size,
    }


app.include_router(servers_router, prefix="/api")
app.include_router(executor_router, prefix="/api")
app.include_router(templates_router, prefix="/api")
app.include_router(logs_router, prefix="/api")
app.include_router(backup_router)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await handle_websocket(websocket)


def main():
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        workers=1,
    )


if __name__ == "__main__":
    main()
