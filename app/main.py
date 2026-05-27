"""API 余额查询服务 - FastAPI 主入口（Linux 生产部署版）"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.models import QueryRequest, QueryResponse
from app.adapters import ADAPTERS, DEFAULT_BASE_URLS
import httpx
import os

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static")

app = FastAPI(
    title="API 余额查询服务",
    description="支持 OpenAI、DeepSeek、Anthropic、硅基流动、OpenRouter、小米 MiMo 及自定义 API 的余额和用量查询",
    version="1.0.0",
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
    return {"status": "ok"}


@app.post("/api/query", response_model=QueryResponse)
async def query_api(req: QueryRequest):
    provider = req.provider.value
    base_url = req.base_url.strip()

    # 智能自动识别官方提供商
    if base_url:
        low_url = base_url.lower()
        if "deepseek.com" in low_url:
            provider = "deepseek"
        elif "anthropic.com" in low_url:
            provider = "anthropic"
        elif "openai.com" in low_url:
            provider = "openai"
        elif "siliconflow.cn" in low_url or "siliconflow.com" in low_url:
            provider = "siliconflow"
        elif "openrouter.ai" in low_url:
            provider = "openrouter"
        elif "xiaomimimo.com" in low_url:
            provider = "mimo"

    adapter = ADAPTERS.get(provider)
    if not adapter:
        raise HTTPException(status_code=400, detail=f"不支持的提供商: {provider}")

    # 如果 base_url 为空，使用对应提供商的默认地址
    if not base_url:
        base_url = DEFAULT_BASE_URLS.get(provider, "")
    if not base_url:
        raise HTTPException(status_code=400, detail="请提供 Base URL")

    # 去掉末尾斜杠
    base_url = base_url.rstrip("/")
    api_key = req.api_key.strip()

    result = QueryResponse(success=False, provider=provider)
    errors = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        balance, balance_err = await adapter.query_balance(client, base_url, api_key)
        usage, usage_err = await adapter.query_usage(client, base_url, api_key, req.start_date, req.end_date)
        subscription, sub_err = await adapter.query_subscription(client, base_url, api_key)

        if balance_err:
            errors.append(f"余额: {balance_err}")
        if usage_err:
            errors.append(f"用量: {usage_err}")
        if sub_err:
            errors.append(f"订阅: {sub_err}")

    if balance is None and usage is None and subscription is None:
        result.error = " | ".join(errors) if errors else "未能获取到任何数据，请检查 Base URL 和 API Key 是否正确，或确认该提供商是否支持余额/用量查询 API"
        return result

    result.success = True
    result.balance = balance
    result.usage = usage
    result.subscription = subscription

    # 根据用量数据特征推断详细程度
    if usage:
        has_tokens = any(u.total_tokens > 0 for u in usage)
        is_summary = len(usage) == 1 and usage[0].model == "全部模型合计"
        if is_summary:
            result.usage_detail_level = "summary"
        elif has_tokens:
            result.usage_detail_level = "full"
        else:
            result.usage_detail_level = "daily"
    else:
        result.usage_detail_level = "summary"

    return result


# 托管前端静态文件（放在所有 API 路由之后）
if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/")
    async def serve_index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    # SPA 兜底：所有未匹配的 GET 请求返回 index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        file_path = os.path.join(STATIC_DIR, full_path)
        if os.path.isfile(file_path):
            return FileResponse(file_path)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=29180)
