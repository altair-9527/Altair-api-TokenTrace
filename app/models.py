"""API 余额查询服务 - 数据模型"""

from pydantic import BaseModel, Field
from typing import Optional
from enum import Enum


class ProviderType(str, Enum):
    openai = "openai"
    deepseek = "deepseek"
    anthropic = "anthropic"
    siliconflow = "siliconflow"
    openrouter = "openrouter"
    mimo = "mimo"
    custom = "custom"


class QueryRequest(BaseModel):
    provider: ProviderType = Field(description="API 提供商类型")
    base_url: str = Field(default="", description="API Base URL，custom 模式必填")
    api_key: str = Field(description="API Key")
    start_date: Optional[str] = Field(default=None, description="用量查询起始日期 YYYY-MM-DD")
    end_date: Optional[str] = Field(default=None, description="用量查询结束日期 YYYY-MM-DD")


class BalanceInfo(BaseModel):
    currency: str = ""
    total_balance: str = ""
    granted_balance: str = ""
    topped_up_balance: str = ""
    is_available: bool = True


class UsageItem(BaseModel):
    date: str = ""
    model: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost: float = 0.0
    currency: str = "USD"


class SubscriptionInfo(BaseModel):
    plan: str = ""
    status: str = ""
    billing_cycle: str = ""
    hard_limit: float = 0.0
    soft_limit: float = 0.0
    expire_time: Optional[str] = Field(default=None, description="密钥过期时间，为空或'永不过期'表示长期有效")


class QueryResponse(BaseModel):
    success: bool
    provider: str
    balance: Optional[list[BalanceInfo]] = None
    usage: Optional[list[UsageItem]] = None
    usage_detail_level: str = "full"  # "full"=每次调用明细, "daily"=按天/模型聚合, "summary"=仅总消费
    subscription: Optional[SubscriptionInfo] = None
    raw_response: Optional[dict] = None
    error: Optional[str] = None
