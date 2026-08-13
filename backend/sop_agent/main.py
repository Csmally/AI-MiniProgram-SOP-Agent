"""微信小程序 SOP Agent — FastAPI 入口。"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .core import orchestrator
from .core.config import get_settings

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：退出时关闭 Postgres 连接池。"""
    yield
    orchestrator.close()


app = FastAPI(
    title="微信小程序 SOP Agent",
    description="新版本上线前的新增功能自动化 SOP 检查 Agent",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS 配置（开发模式允许前端跨域）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(router)


@app.get("/health")
async def health_check():
    """健康检查。"""
    return {
        "status": "ok",
        "version": "0.1.0",
        "models": settings.MODEL_ROUTING,
    }


def main():
    """启动服务。"""
    import uvicorn

    uvicorn.run(
        "sop_agent.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )


if __name__ == "__main__":
    main()
