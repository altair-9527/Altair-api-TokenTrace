"""各 API 提供商的适配器"""

import httpx
import logging
from typing import Optional, Tuple, List, Any
from datetime import datetime, timedelta, timezone
from app.models import BalanceInfo, UsageItem, SubscriptionInfo

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
logger = logging.getLogger(__name__)


def is_in_date_range(date_str: str, start_date: Optional[str], end_date: Optional[str]) -> bool:
    """健壮的辅助函数，过滤符合日期范围的明细数据"""
    if not date_str or date_str == "Unknown":
        return True
    
    # 提取并规范化 YYYY-MM-DD
    # 例如 "2026-05-26 15:30:22" -> "2026-05-26"
    norm_date = date_str.split()[0]
    if len(norm_date) == 8 and norm_date.isdigit():  # "20260526" 
        norm_date = f"{norm_date[:4]}-{norm_date[4:6]}-{norm_date[6:]}"
    
    # 兼容格式如 "2026/05/26" -> "2026-05-26"
    norm_date = norm_date.replace("/", "-")
    
    if start_date and norm_date < start_date:
        return False
    if end_date and norm_date > end_date:
        return False
    return True


def build_url(base_url: str, path: str) -> str:
    """智能拼接 Base URL 和 API 路径，防范 /v1/v1 以及 /v1/api 等双重路径导致的 404 问题"""
    base_url = base_url.rstrip("/")
    path = path.lstrip("/")
    
    # 如果路径以 v1 开头，且 base_url 以 /v1 结尾
    if path.startswith("v1/") and base_url.endswith("/v1"):
        base_url = base_url[:-3]
    # 如果路径以 api 开头，且 base_url 包含了 /v1
    elif path.startswith("api/") and base_url.endswith("/v1"):
        base_url = base_url[:-3]
        
    return f"{base_url}/{path}"


def pick_value(data: dict, keys: List[str], default: Any = None) -> Any:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return default


def to_int(value: Any, default: int = 0) -> int:
    if value is None or value == "":
        return default
    try:
        return int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return default


def to_float(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return default


def normalize_datetime(value: Any) -> str:
    if value is None or value == "":
        return "Unknown"
    if isinstance(value, (int, float)) or str(value).isdigit():
        timestamp = float(value)
        if timestamp > 100000000000:
            timestamp = timestamp / 1000
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(text[:len(fmt)], fmt)
            if fmt == "%Y-%m-%d":
                return parsed.strftime("%Y-%m-%d")
            return parsed.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return text.replace("T", " ")[:19]


def extract_records(payload: Any) -> List[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    candidates = [payload.get("data"), payload]
    for candidate in candidates:
        if isinstance(candidate, list):
            return [item for item in candidate if isinstance(item, dict)]
        if isinstance(candidate, dict):
            for key in ("items", "logs", "rows", "records", "list", "data"):
                value = candidate.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def parse_usage_record(record: dict) -> UsageItem:
    prompt_tokens = to_int(pick_value(record, [
        "prompt_tokens", "promptTokens", "PromptTokens", "prompt_token", "input_tokens", "InputTokens"
    ]))
    completion_tokens = to_int(pick_value(record, [
        "completion_tokens", "completionTokens", "CompletionTokens", "completion_token", "output_tokens", "OutputTokens"
    ]))
    total_tokens = to_int(pick_value(record, [
        "total_tokens", "totalTokens", "TotalTokens", "tokens", "Tokens", "token_used", "tokenUsed"
    ]), prompt_tokens + completion_tokens)
    if prompt_tokens + completion_tokens > 0:
        total_tokens = prompt_tokens + completion_tokens

    quota = pick_value(record, ["quota", "Quota", "used_quota", "usedQuota", "consume_quota", "quota_used"])
    cost_value = pick_value(record, ["cost", "Cost", "amount", "Amount"], 0)
    cost = to_float(quota) / 500000.0 if quota is not None else to_float(cost_value)

    return UsageItem(
        date=normalize_datetime(pick_value(record, [
            "created_at", "createdAt", "CreatedAt", "date", "Date", "day", "time", "timestamp"
        ])),
        model=str(pick_value(record, [
            "model_name", "modelName", "ModelName", "model", "Model", "name"
        ], "Unknown")),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        cost=cost,
        currency="USD",
    )


def extract_total(payload: Any) -> Optional[int]:
    if not isinstance(payload, dict):
        return None
    for candidate in (payload.get("data"), payload):
        if isinstance(candidate, dict):
            total = pick_value(candidate, ["total", "count", "total_count", "totalCount"])
            if total is not None:
                return to_int(total)
    return None


def record_identity(record: dict) -> str:
    value = pick_value(record, ["id", "ID"])
    if value is not None:
        return f"id:{value}"
    return "|".join(str(pick_value(record, keys, "")) for keys in (
        ["created_at", "createdAt", "CreatedAt", "time", "timestamp"],
        ["model_name", "modelName", "ModelName", "model", "Model"],
        ["quota", "Quota", "used_quota", "usedQuota"],
        ["prompt_tokens", "PromptTokens"],
        ["completion_tokens", "CompletionTokens"],
    ))


async def fetch_paginated_records(
    client: httpx.AsyncClient,
    url: str,
    params: dict,
    headers: Optional[dict],
    label: str,
    max_pages: int = 50,
) -> List[dict]:
    page_size = to_int(params.get("page_size"), 100) or 100
    all_records: List[dict] = []
    seen = set()
    total = None

    for page in range(1, max_pages + 1):
        page_params = dict(params)
        page_params["p"] = page
        page_params["page_size"] = page_size
        resp = await client.get(
            url,
            headers=headers,
            params=page_params,
            timeout=15.0,
        )
        logger.info(f"{label} 第 {page} 页响应状态码: {resp.status_code}")
        if resp.status_code != 200:
            if page == 1:
                raise ValueError(f"{label}返回非200状态码: {resp.status_code}")
            break

        data = resp.json()
        if isinstance(data, dict) and data.get("success") is False:
            raise ValueError(str(data.get("message") or data.get("error") or f"{label}不可用"))

        if total is None:
            total = extract_total(data)

        records = extract_records(data)
        if not records:
            break

        new_count = 0
        for record in records:
            identity = record_identity(record)
            if identity in seen:
                continue
            seen.add(identity)
            all_records.append(record)
            new_count += 1

        if new_count == 0:
            break
        if total is not None and len(all_records) >= total:
            break
        if len(records) < page_size:
            break

    logger.info(f"{label} 分页累计解析到候选记录数: {len(all_records)}")
    return all_records


def handle_httpx_error(e: Exception, context: str) -> str:
    """统一处理 httpx 异常并返回友好中文提示"""
    if isinstance(e, httpx.TimeoutException):
        return f"{context}超时，请检查网络或 Base URL 是否通畅"
    elif isinstance(e, httpx.ConnectError):
        return f"无法连接到服务器，请检查 Base URL 是否正确，或网络是否代理正常"
    elif isinstance(e, httpx.HTTPStatusError):
        status = e.response.status_code
        if status == 401:
            return "API Key 无效或未授权 (401 Unauthorized)，请检查 Key 是否填写正确"
        elif status == 403:
            return "拒绝访问 (403 Forbidden)，您的账户可能被封禁或无此接口权限"
        elif status == 404:
            return "接口未找到 (404 Not Found)，请检查 Base URL 的路径（有些中转需要带 /v1，有些不需要）"
        elif status == 429:
            return "请求过于频繁 (429 Too Many Requests)，或您的账户额度已耗尽"
        else:
            return f"服务器返回错误 (状态码: {status})"
    else:
        return f"发生未知错误: {str(e)}"


class BaseAdapter:
    """适配器基类"""

    @staticmethod
    def build_headers(api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        return None, None

    async def query_usage(
        self, client: httpx.AsyncClient, base_url: str, api_key: str,
        start_date: Optional[str], end_date: Optional[str]
    ) -> Tuple[Optional[List[UsageItem]], Optional[str]]:
        return None, None

    async def query_subscription(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[SubscriptionInfo], Optional[str]]:
        return None, None


class OpenAIAdapter(BaseAdapter):
    """OpenAI API 适配器"""

    @staticmethod
    def build_headers(api_key: str) -> dict:
        return {"Authorization": f"Bearer {api_key}"}

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        # -1. NewAPI 官方 Token 用量接口：仅凭 sk- Key 查询余额、已用、到期时间
        token_usage_url = build_url(base_url, "api/usage/token")
        try:
            logger.info(f"尝试 NewAPI Token 用量接口: {token_usage_url}")
            resp = await client.get(
                token_usage_url,
                params={"key": api_key},
                timeout=15.0,
            )
            logger.info(f"NewAPI Token 用量接口响应状态码: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                payload = data.get("data", data) if isinstance(data, dict) else {}
                if isinstance(payload, dict):
                    available = pick_value(payload, ["total_available", "available", "quota", "remain_quota"])
                    granted = pick_value(payload, ["total_granted", "granted", "total_quota"])
                    used = pick_value(payload, ["total_used", "used", "used_quota"], 0)
                    if available is not None or granted is not None:
                        available_usd = to_float(available) / 500000.0
                        granted_usd = to_float(granted if granted is not None else available) / 500000.0
                        used_usd = to_float(used) / 500000.0
                        logger.info(f"成功通过 NewAPI Token 用量接口获取余额: {available_usd}")
                        return [BalanceInfo(
                            currency="USD",
                            total_balance=f"{available_usd:.4f}",
                            granted_balance=f"{granted_usd:.4f}",
                            topped_up_balance=f"{used_usd:.4f}",
                            is_available=True,
                        )], None
        except Exception as e_token_usage:
            logger.warning(f"尝试 NewAPI Token 用量接口异常: {str(e_token_usage)}")

        # 0. 尝试 OneAPI / NewAPI 的高级管理个人信息接口（如果用户输入的是 Access Token）
        admin_self_url = build_url(base_url, "api/user/self")
        try:
            logger.info(f"尝试 NewAPI 个人信息接口: {admin_self_url}")
            resp = await client.get(
                admin_self_url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"NewAPI 个人信息接口响应状态码: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                if data.get("success") is True and "data" in data:
                    user_data = data.get("data", {})
                    quota = float(user_data.get("quota", 0))
                    balance_usd = quota / 500000.0 # OneAPI 额度转美元换算率
                    logger.info(f"成功通过 NewAPI 个人信息接口算得余额: {balance_usd}")
                    return [BalanceInfo(
                        currency="USD",
                        total_balance=f"{balance_usd:.4f}",
                        granted_balance=f"{balance_usd:.4f}",
                        topped_up_balance="0.0000",
                        is_available=True,
                    )], None
        except Exception as e_admin_self:
            logger.warning(f"尝试 NewAPI 个人信息接口异常: {str(e_admin_self)}")

        # 优先使用 OpenAI/NewAPI 核心订阅接口，通过 (总额度 hard_limit_usd - 已用额度 total_usage) 计算真实余额
        sub_url = build_url(base_url, "v1/dashboard/billing/subscription")
        try:
            logger.info(f"正在通过订阅接口查询余额: {sub_url}")
            resp = await client.get(
                sub_url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"订阅接口响应状态码: {resp.status_code}")
            
            if resp.status_code == 200:
                text_content = resp.text
                if text_content.strip().startswith("<!doctype") or text_content.strip().startswith("<html") or "html" in resp.headers.get("content-type", "").lower():
                    logger.info("订阅接口返回了 HTML 页面而非 JSON，跳过此接口")
                else:
                    data = resp.json()
                    if isinstance(data, dict) and "hard_limit_usd" in data:
                        hard_limit = float(data.get("hard_limit_usd", 0))
                        
                        # 进而查询过去一年的总用量
                        from datetime import datetime, timedelta
                        start_date = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
                        end_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
                        
                        total_usage = 0.0
                        try:
                            usage_url = build_url(base_url, "v1/dashboard/billing/usage")
                            logger.info(f"正在查询用量以计算余额: {usage_url}")
                            usage_resp = await client.get(
                                usage_url,
                                headers=self.build_headers(api_key),
                                params={"start_date": start_date, "end_date": end_date},
                                timeout=15.0,
                            )
                            if usage_resp.status_code == 200:
                                usage_data = usage_resp.json()
                                # 官方及 OneAPI/NewAPI 均返回美分，需除以 100
                                total_usage = float(usage_data.get("total_usage", 0)) / 100.0
                        except Exception as e_usage:
                            logger.warning(f"获取用量失败，默认使用已用 0: {str(e_usage)}")
                            
                        remaining = max(0.0, hard_limit - total_usage)
                        logger.info(f"成功通过 (额度 {hard_limit} - 已用 {total_usage}) 计算出余额: {remaining}")
                        return [BalanceInfo(
                            currency="USD",
                            total_balance=f"{remaining:.4f}",
                            granted_balance=f"{hard_limit:.4f}",
                            topped_up_balance="0.0000",
                            is_available=True,
                        )], None
        except Exception as e:
            logger.warning(f"通过订阅接口查询余额异常: {str(e)}")

        # 备用方案：尝试老的 /v1/dashboard/billing/credit_grants 接口
        url = build_url(base_url, "v1/dashboard/billing/credit_grants")
        try:
            logger.info(f"尝试老余额接口: {url}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"老余额接口响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            text_content = resp.text
            if text_content.strip().startswith("<!doctype") or text_content.strip().startswith("<html") or "html" in resp.headers.get("content-type", "").lower():
                return None, "接口返回了 HTML（SPA 重定向）"

            data = resp.json()
            grants = data.get("grants", []) or data.get("data", [])
            result = []
            for g in grants:
                result.append(BalanceInfo(
                    currency="USD",
                    total_balance=str(g.get("grant_amount", 0)),
                    granted_balance=str(g.get("grant_amount", 0)),
                    topped_up_balance=str(g.get("used_amount", 0)),
                    is_available=g.get("effective_at") is not None,
                ))
            return (result if result else None), None
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询 OpenAI 余额")
            logger.warning(f"OpenAI 余额查询最终异常: {err_msg}. 详情: {str(e)}")
            return None, err_msg

    async def query_usage(
        self, client: httpx.AsyncClient, base_url: str, api_key: str,
        start_date: Optional[str], end_date: Optional[str]
    ) -> Tuple[Optional[List[UsageItem]], Optional[str]]:
        date_filter_requested = bool(start_date or end_date)
        if start_date and not end_date:
            end_date = start_date
        elif end_date and not start_date:
            start_date = end_date

        # 未选择日期时，优先返回 NewAPI 针对该 sk- Token 的累计总消费
        if not date_filter_requested:
            token_usage_url = build_url(base_url, "api/usage/token")
            try:
                logger.info(f"正在尝试 NewAPI Token 累计用量接口: {token_usage_url}")
                resp = await client.get(
                    token_usage_url,
                    params={"key": api_key},
                    timeout=15.0,
                )
                logger.info(f"NewAPI Token 累计用量接口响应状态码: {resp.status_code}")
                if resp.status_code == 200:
                    data = resp.json()
                    payload = data.get("data", data) if isinstance(data, dict) else {}
                    if isinstance(payload, dict):
                        used_value = pick_value(payload, ["total_used", "used", "used_quota"])
                        if used_value is not None:
                            used_usd = to_float(used_value) / 500000.0
                            logger.info(f"成功通过 NewAPI Token 累计用量接口获取总消费: {used_usd}")
                            return [UsageItem(
                                date="累计总消费",
                                model="全部模型合计",
                                prompt_tokens=0,
                                completion_tokens=0,
                                total_tokens=0,
                                cost=used_usd,
                                currency="USD",
                            )], None
            except Exception as e_token_usage:
                logger.warning(f"尝试 NewAPI Token 累计用量接口异常: {str(e_token_usage)}")

        # -1. NewAPI 官方公开接口：仅凭 sk- Token 查询该 Token 的调用日志
        token_log_url = build_url(base_url, "api/log/token")
        try:
            logger.info(f"正在尝试 NewAPI Token 日志接口: {token_log_url}")
            token_log_params = {"key": api_key, "p": 1, "page_size": 100, "type": 2}
            records = await fetch_paginated_records(
                client,
                token_log_url,
                token_log_params,
                None,
                "NewAPI Token 日志接口",
            )
            if records:
                items = [parse_usage_record(record) for record in records]
                items = [item for item in items if is_in_date_range(item.date, start_date, end_date)]
                if items:
                    logger.info(f"成功通过 NewAPI Token 日志接口并经过滤，得到 {len(items)} 条明细")
                    return items, None
        except Exception as e_token_log:
            logger.warning(f"尝试 NewAPI Token 日志接口异常: {str(e_token_log)}")

        # 0. 尝试 OneAPI / NewAPI 的高级管理日志接口（需要提供用户的 Personal Access Token）
        admin_log_url = build_url(base_url, "api/log/self")
        try:
            logger.info(f"正在尝试 NewAPI 个人日志接口: {admin_log_url}")
            # 计算秒级时间戳参数，支持直接在接口层面进行过滤
            log_params = {"p": 1, "page_size": 100, "type": 2}
            if start_date:
                try:
                    dt = datetime.strptime(start_date, "%Y-%m-%d")
                    log_params["start_timestamp"] = int(dt.timestamp())
                except Exception:
                    pass
            if end_date:
                try:
                    dt = datetime.strptime(end_date, "%Y-%m-%d")
                    log_params["end_timestamp"] = int(dt.timestamp()) + 86399
                except Exception:
                    pass

            records = await fetch_paginated_records(
                client,
                admin_log_url,
                log_params,
                self.build_headers(api_key),
                "NewAPI 个人日志接口",
            )
            if records:
                items = [parse_usage_record(record) for record in records]
                items = [item for item in items if is_in_date_range(item.date, start_date, end_date)]
                if items:
                    logger.info(f"成功通过 NewAPI 个人日志接口并经过滤，得到 {len(items)} 条明细")
                    return items, None
        except Exception as e_admin_log:
            logger.warning(f"尝试 NewAPI 个人日志接口异常: {str(e_admin_log)}")

        # 0.5 尝试 NewAPI 的用户数据看板接口（按天按模型聚合，可能对 sk- 令牌也开放）
        dashboard_url = build_url(base_url, "api/user/dashboard")
        try:
            logger.info(f"正在尝试 NewAPI 数据看板接口: {dashboard_url}")
            dashboard_params = {"p": 1, "page_size": 100}
            records = await fetch_paginated_records(
                client,
                dashboard_url,
                dashboard_params,
                self.build_headers(api_key),
                "NewAPI 数据看板接口",
            )
            if records:
                items = [parse_usage_record(record) for record in records]
                items = [item for item in items if is_in_date_range(item.date, start_date, end_date)]
                if items:
                    logger.info(f"成功通过数据看板接口并经过滤，得到 {len(items)} 条明细")
                    return items, None
        except Exception as e_dashboard:
            logger.warning(f"尝试数据看板接口异常: {str(e_dashboard)}")

        # 1. 优先尝试 OneAPI / NewAPI / 旧版 OpenAI 的 /v1/dashboard/billing/usage 接口（带明细）
        billing_usage_url = build_url(base_url, "v1/dashboard/billing/usage")
        try:
            params = {}
            from datetime import datetime, timedelta
            if not start_date:
                start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
            if not end_date:
                end_date = (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%d")
                
            params["start_date"] = start_date
            params["end_date"] = end_date
            
            logger.info(f"正在尝试账单明细用量接口: {billing_usage_url}, params: {params}")
            resp = await client.get(
                billing_usage_url,
                headers=self.build_headers(api_key),
                params=params,
                timeout=15.0,
            )
            logger.info(f"账单明细用量接口响应状态码: {resp.status_code}")
            
            if resp.status_code == 200:
                text_content = resp.text
                if not (text_content.strip().startswith("<!doctype") or text_content.strip().startswith("<html") or "html" in resp.headers.get("content-type", "").lower()):
                    data = resp.json()
                    # 记录原始响应用于调试
                    logger.info(f"账单接口原始响应字段: {list(data.keys())}")
                    logger.debug(f"账单接口原始响应内容（前500字符）: {text_content[:500]}")

                    items = []
                    # 如果返回的是 "daily_costs" 格式（代表 OpenAI 经典账单或 OneAPI/NewAPI）
                    if "daily_costs" in data:
                        daily = data.get("daily_costs", [])
                        for d in daily:
                            timestamp = d.get("timestamp", 0)
                            date_str = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
                            
                            line_items = d.get("line_items", [])
                            for item in line_items:
                                items.append(UsageItem(
                                    date=date_str,
                                    model=item.get("name", "Unknown"),
                                    prompt_tokens=0,
                                    completion_tokens=0,
                                    total_tokens=0,
                                    cost=float(item.get("cost", 0)) / 100.0,
                                    currency="USD",
                                ))
                        # 内存精确日期范围过滤
                        if items:
                            items = [item for item in items if is_in_date_range(item.date, start_date, end_date)]
                    
                    # 如果没有 daily_costs 但有 total_usage，只在未选择日期时展示累计总消费
                    if not items and "total_usage" in data:
                        total = float(data.get("total_usage", 0)) / 100.0
                        if total > 0 and not date_filter_requested:
                            logger.info(f"账单接口仅返回总用量（无每日明细）: {total}")
                            items.append(UsageItem(
                                date="累计总消费",
                                model="全部模型合计",
                                prompt_tokens=0,
                                completion_tokens=0,
                                total_tokens=0,
                                cost=total,
                                currency="USD",
                            ))
                        elif total > 0:
                            logger.info("账单接口仅返回累计总用量，已跳过，避免将累计总费用误当作所选日期费用")
                    
                    if items:
                        logger.info(f"成功解析账单用量，共计 {len(items)} 条明细")
                        return items, None
        except Exception as e_billing:
            logger.warning(f"账单明细用量接口请求异常: {str(e_billing)}")

        # 2. 备用方案：尝试 OpenAI 官方新版 /v1/usage 接口
        url = build_url(base_url, "v1/usage")
        try:
            params = {}
            if start_date:
                params["start_date"] = start_date
            if end_date:
                params["end_date"] = end_date
            logger.info(f"正在查询 OpenAI 官方用量接口: {url}, params: {params}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                params=params if params else None,
                timeout=15.0,
            )
            logger.info(f"OpenAI 官方用量接口响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            text_content = resp.text
            if text_content.strip().startswith("<!doctype") or text_content.strip().startswith("<html") or "html" in resp.headers.get("content-type", "").lower():
                return None, "接口返回了 HTML（SPA 重定向）"

            data = resp.json()
            daily = data.get("data", [])
            items = []
            for d in daily:
                items.append(UsageItem(
                    date=str(d.get("aggregation_timestamp", "")),
                    model="",
                    prompt_tokens=d.get("n_context_tokens_total", 0),
                    completion_tokens=d.get("n_generated_tokens_total", 0),
                    total_tokens=d.get("n_context_tokens_total", 0) + d.get("n_generated_tokens_total", 0),
                    cost=float(d.get("total_usage", 0)) / 100.0,
                    currency="USD",
                ))
            return (items if items else None), None
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询 OpenAI 用量")
            logger.warning(f"OpenAI 用量查询最终异常: {err_msg}. 详情: {str(e)}")

        # 3. 选了日期但所有日志接口都不可用时的兜底：从 /api/usage/token 获取累计消费
        if date_filter_requested:
            token_usage_url = build_url(base_url, "api/usage/token")
            try:
                logger.info(f"所有日志接口均不可用，尝试从 Token 用量接口获取累计消费作为兜底: {token_usage_url}")
                resp = await client.get(
                    token_usage_url,
                    params={"key": api_key},
                    timeout=15.0,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    payload = data.get("data", data) if isinstance(data, dict) else {}
                    if isinstance(payload, dict):
                        used_value = pick_value(payload, ["total_used", "used", "used_quota"])
                        if used_value is not None:
                            used_usd = to_float(used_value) / 500000.0
                            logger.info(f"兜底：获取到累计总消费 {used_usd}，该站点不支持按日期查询")
                            return [UsageItem(
                                date="累计总消费（该站点不支持按日期查询）",
                                model="全部模型合计",
                                prompt_tokens=0,
                                completion_tokens=0,
                                total_tokens=0,
                                cost=used_usd,
                                currency="USD",
                            )], None
            except Exception as e_fallback:
                logger.warning(f"兜底获取累计消费异常: {str(e_fallback)}")

        return None, err_msg

    async def query_subscription(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[SubscriptionInfo], Optional[str]]:
        token_usage_url = build_url(base_url, "api/usage/token")
        try:
            logger.info(f"正在通过 NewAPI Token 用量接口查询有效期: {token_usage_url}")
            resp = await client.get(
                token_usage_url,
                params={"key": api_key},
                timeout=15.0,
            )
            logger.info(f"NewAPI Token 有效期接口响应状态码: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                payload = data.get("data", data) if isinstance(data, dict) else {}
                if isinstance(payload, dict):
                    expires_at = pick_value(payload, ["expires_at", "expired_time", "access_until"], 0)
                    expire_time_str = "永不过期"
                    if to_int(expires_at) > 0:
                        expire_time_str = datetime.fromtimestamp(to_int(expires_at)).strftime("%Y-%m-%d %H:%M:%S")
                    return SubscriptionInfo(
                        plan=str(pick_value(payload, ["name", "token_name"], "API Token")),
                        status="active",
                        billing_cycle="",
                        hard_limit=to_float(pick_value(payload, ["total_granted", "granted", "total_quota"], 0)) / 500000.0,
                        soft_limit=to_float(pick_value(payload, ["total_available", "available", "quota"], 0)) / 500000.0,
                        expire_time=expire_time_str,
                    ), None
        except Exception as e_token_subscription:
            logger.warning(f"尝试 NewAPI Token 有效期接口异常: {str(e_token_subscription)}")

        url = build_url(base_url, "v1/dashboard/billing/subscription")
        try:
            logger.info(f"正在查询 OpenAI 订阅: {url}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"OpenAI 订阅响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            data = resp.json()
            access_until = data.get("access_until", 0) or data.get("expired_time", 0)
            expire_time_str = "永不过期"
            if access_until and access_until > 0:
                try:
                    expire_time_str = datetime.fromtimestamp(access_until).strftime("%Y-%m-%d %H:%M:%S")
                except Exception:
                    expire_time_str = "永不过期"

            sub = SubscriptionInfo(
                plan=data.get("plan", {}).get("title", "") if isinstance(data.get("plan"), dict) else str(data.get("plan", "")),
                status=data.get("has_payment_method", False) and "active" or "inactive",
                billing_cycle="monthly",
                hard_limit=float(data.get("hard_limit_usd", 0)),
                soft_limit=float(data.get("soft_limit_usd", 0)),
                expire_time=expire_time_str
            )
            return sub, None
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询 OpenAI 订阅")
            logger.warning(f"OpenAI 订阅查询异常: {err_msg}. 详情: {str(e)}")
            return None, err_msg


class DeepSeekAdapter(BaseAdapter):
    """DeepSeek API 适配器"""

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        clean_base = base_url.rstrip("/")
        if clean_base.endswith("/v1"):
            clean_base = clean_base[:-3]
        url = f"{clean_base}/user/balance"
        try:
            logger.info(f"正在查询 DeepSeek 余额: {url}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"DeepSeek 余额响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            data = resp.json()
            infos = data.get("balance_infos", [])
            result = []
            for info in infos:
                result.append(BalanceInfo(
                    currency=info.get("currency", ""),
                    total_balance=info.get("total_balance", "0"),
                    granted_balance=info.get("granted_balance", "0"),
                    topped_up_balance=info.get("topped_up_balance", "0"),
                    is_available=data.get("is_available", True),
                ))
            return (result if result else None), None
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询 DeepSeek 余额")
            logger.warning(f"DeepSeek 余额查询异常: {err_msg}. 详情: {str(e)}")
            return None, err_msg


class SiliconFlowAdapter(BaseAdapter):
    """硅基流动 (SiliconFlow) API 适配器"""

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        clean_base = base_url.rstrip("/")
        if clean_base.endswith("/v1"):
            clean_base = clean_base[:-3]
        url = f"{clean_base}/v1/user/info"
        try:
            logger.info(f"正在查询硅基流动 (SiliconFlow) 余额: {url}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"硅基流动余额响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            data = resp.json()
            if data.get("status") is True and "data" in data:
                account_data = data["data"]
                balance_val = account_data.get("balance", "0")
                result = [BalanceInfo(
                    currency="CNY",
                    total_balance=f"{to_float(balance_val):.4f}",
                    granted_balance="0.0000",
                    topped_up_balance=f"{to_float(balance_val):.4f}",
                    is_available=account_data.get("status") == "active" or account_data.get("status", "") == "",
                )]
                return result, None
            return None, "返回的数据结构不匹配"
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询硅基流动余额")
            logger.warning(f"硅基流动余额查询异常: {err_msg}. 详情: {str(e)}")
            return None, err_msg


class MiMoAdapter(BaseAdapter):
    """小米 MiMo 大模型适配器 - 仅支持推理，不开放余额查询 API"""

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        # 小米 MiMo 平台未开放任何余额/订阅查询 API，所有管理端点均返回 404
        # 先尝试标准端点，若全部 404 则返回明确提示
        clean_base = base_url.rstrip("/")
        if clean_base.endswith("/v1"):
            clean_base = clean_base[:-3]

        probe_endpoints = [
            f"{clean_base}/v1/dashboard/billing/subscription",
            f"{clean_base}/api/user/self",
            f"{clean_base}/api/usage/token",
        ]
        for ep in probe_endpoints:
            try:
                resp = await client.get(ep, headers=self.build_headers(api_key), timeout=10.0)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and "error" not in data:
                        # 意外命中了有效接口，尝试解析
                        return None, None
            except Exception:
                pass

        return None, (
            "小米 MiMo 平台暂未开放余额查询 API，"
            "请前往控制台查看：https://platform.xiaomimimo.com "
            "（账户余额 > 订阅管理 页面可查看额度与到期时间）"
        )


class OpenRouterAdapter(BaseAdapter):
    """OpenRouter API 适配器"""

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        clean_base = base_url.rstrip("/")
        if clean_base.endswith("/v1"):
            clean_base = clean_base[:-3]
        url = f"{clean_base}/api/v1/key"
        try:
            logger.info(f"正在查询 OpenRouter 额度: {url}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                timeout=15.0,
            )
            logger.info(f"OpenRouter 响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            data = resp.json()
            if "data" in data:
                key_data = data["data"]
                limit = key_data.get("limit")
                usage = key_data.get("usage", 0.0)
                is_active = key_data.get("is_active", True)
                
                if limit is not None:
                    total_bal = max(0.0, to_float(limit) - to_float(usage))
                else:
                    total_bal = 99999.0
                    
                result = [BalanceInfo(
                    currency="USD",
                    total_balance=f"{to_float(total_bal):.4f}",
                    granted_balance=f"{to_float(limit) if limit is not None else 0.0:.4f}",
                    topped_up_balance=f"{to_float(total_bal):.4f}",
                    is_available=is_active,
                )]
                return result, None
            return None, "返回的数据结构不匹配"
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询 OpenRouter 余额")
            logger.warning(f"OpenRouter 余额查询异常: {err_msg}. 详情: {str(e)}")
            return None, err_msg


class AnthropicAdapter(BaseAdapter):
    """Anthropic Claude API 适配器"""

    @staticmethod
    def build_headers(api_key: str) -> dict:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

    async def query_usage(
        self, client: httpx.AsyncClient, base_url: str, api_key: str,
        start_date: Optional[str], end_date: Optional[str]
    ) -> Tuple[Optional[List[UsageItem]], Optional[str]]:
        url = f"{base_url}/v1/organizations/cost_report"
        try:
            from datetime import datetime, timedelta
            now = datetime.utcnow()
            if not start_date:
                start_date = (now - timedelta(days=30)).strftime("%Y-%m-%d")
            if not end_date:
                end_date = now.strftime("%Y-%m-%d")
            params = {
                "starting_at": f"{start_date}T00:00:00Z",
                "ending_at": f"{end_date}T00:00:00Z",
            }
            logger.info(f"正在查询 Anthropic 用量: {url}, params: {params}")
            resp = await client.get(
                url,
                headers=self.build_headers(api_key),
                params=params,
                timeout=15.0,
            )
            logger.info(f"Anthropic 用量响应状态码: {resp.status_code}")
            resp.raise_for_status()
            
            data = resp.json()
            items = []
            for entry in data.get("data", []):
                items.append(UsageItem(
                    date=entry.get("date", ""),
                    model=entry.get("model", ""),
                    prompt_tokens=entry.get("input_tokens", 0),
                    completion_tokens=entry.get("output_tokens", 0),
                    total_tokens=entry.get("input_tokens", 0) + entry.get("output_tokens", 0),
                    cost=float(entry.get("cost", {}).get("amount", 0)) if isinstance(entry.get("cost"), dict) else 0.0,
                    currency="USD",
                ))
            return (items if items else None), None
        except Exception as e:
            err_msg = handle_httpx_error(e, "查询 Anthropic 用量")
            logger.warning(f"Anthropic 用量查询异常: {err_msg}. 详情: {str(e)}")
            return None, err_msg


class CustomAdapter(OpenAIAdapter):
    """自定义适配器 - 尝试多种通用查询"""

    async def query_balance(self, client: httpx.AsyncClient, base_url: str, api_key: str) -> Tuple[Optional[List[BalanceInfo]], Optional[str]]:
        balance, balance_err = await super().query_balance(client, base_url, api_key)
        if balance:
            return balance, None

        # 1. 尝试常见的余额和订阅端点 (优先测试 NewAPI/OneAPI 的订阅接口，因为最通用且最准)
        endpoints = [
            "/v1/dashboard/billing/subscription",
            "/dashboard/billing/subscription",
            "/user/balance",
            "/v1/user/balance",
            "/dashboard/billing/credit_grants",
            "/v1/dashboard/billing/credit_grants",
            "/v1/balance",
            "/api/balance",
            "/balance"
        ]
        
        last_error = balance_err or "未探测到任何可用的余额查询端点"
        for ep in endpoints:
            url = build_url(base_url, ep)
            try:
                logger.info(f"Custom 模式：尝试探测端点 {url}")
                headers = self.build_headers(api_key)
                resp = await client.get(
                    url,
                    headers=headers,
                    timeout=10.0,
                )
                logger.info(f"Custom 模式端点 {ep} 响应状态码: {resp.status_code}")
                
                if resp.status_code == 200:
                    text_content = resp.text
                    
                    # 强力防范 SPA 单页应用重定向返回 HTML 的情况（例如某些网站在 404 时也会返回 200 伴随 index.html）
                    if text_content.strip().startswith("<!doctype") or text_content.strip().startswith("<html") or "html" in resp.headers.get("content-type", "").lower():
                        logger.info(f"Custom 模式端点 {ep} 返回了 HTML 内容而非 JSON，判定为 SPA 前端路由重定向，跳过")
                        continue
                        
                    logger.info(f"Custom 模式端点 {ep} 返回 200 OK, 响应内容: {text_content[:1000]}")
                    data = resp.json()
                    if not isinstance(data, dict):
                        continue
                        
                    # 场景 A: OneAPI/NewAPI 核心订阅接口 (最具普适性，通过 hard_limit_usd 和 total_usage 计算)
                    if "hard_limit_usd" in data:
                        hard_limit = float(data.get("hard_limit_usd", 0))
                        
                        # 进而查询过去一年的总用量
                        from datetime import datetime, timedelta
                        start_date = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
                        # 兼容有些端点的时间边界，往后多加一天
                        end_date = (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%d")
                        
                        total_usage = 0.0
                        try:
                            usage_url = build_url(base_url, "v1/dashboard/billing/usage")
                            logger.info(f"Custom 模式：探测到 NewAPI/OneAPI 订阅，正在请求用量接口以计算余额 {usage_url}")
                            usage_resp = await client.get(
                                usage_url,
                                headers=headers,
                                params={"start_date": start_date, "end_date": end_date},
                                timeout=10.0,
                            )
                            if usage_resp.status_code == 200:
                                usage_data = usage_resp.json()
                                # 官方及 OneAPI/NewAPI 均返回美分，需除以 100
                                total_usage = float(usage_data.get("total_usage", 0)) / 100.0
                        except Exception as e_usage:
                            logger.warning(f"Custom 模式：获取用量失败，默认已用额度为 0: {str(e_usage)}")
                            
                        remaining = max(0.0, hard_limit - total_usage)
                        logger.info(f"Custom 模式：NewAPI/OneAPI 成功算得余额: 额度 {hard_limit} - 已用 {total_usage} = 余额 {remaining}")
                        return [BalanceInfo(
                            currency="USD",
                            total_balance=f"{remaining:.4f}",
                            granted_balance=f"{hard_limit:.4f}",
                            topped_up_balance="0.0000",
                            is_available=True,
                        )], None
                        
                    # 场景 B: 包含 balance_infos 的 DeepSeek 风格
                    elif "balance_infos" in data:
                        infos = data.get("balance_infos", [])
                        result = []
                        for info in infos:
                            result.append(BalanceInfo(
                                currency=info.get("currency", "USD"),
                                total_balance=str(info.get("total_balance", "0")),
                                granted_balance=str(info.get("granted_balance", "0")),
                                topped_up_balance=str(info.get("topped_up_balance", "0")),
                                is_available=data.get("is_available", True),
                            ))
                        if result:
                            logger.info(f"Custom 模式：成功解析到 DeepSeek 风格余额数据 (接口 {ep})")
                            return result, None
                            
                    # 场景 C: 包含 grants / data (OpenAI 风格)
                    elif "grants" in data or "total_available" in data or "total_granted" in data:
                        grants = data.get("grants", []) or data.get("data", []) or []
                        result = []
                        if isinstance(grants, list) and len(grants) > 0:
                            for g in grants:
                                result.append(BalanceInfo(
                                    currency="USD",
                                    total_balance=str(g.get("grant_amount", g.get("balance", 0))),
                                    granted_balance=str(g.get("grant_amount", 0)),
                                    topped_up_balance=str(g.get("used_amount", 0)),
                                    is_available=True,
                                ))
                        else:
                            # 扁平结构
                            total_balance = data.get("total_available", data.get("total_balance", data.get("balance", "0")))
                            result.append(BalanceInfo(
                                currency=data.get("currency", "USD"),
                                total_balance=str(total_balance),
                                granted_balance=str(data.get("total_granted", "0")),
                                topped_up_balance=str(data.get("total_used", "0")),
                                is_available=True,
                            ))
                        if result:
                            logger.info(f"Custom 模式：成功解析到 OpenAI/中转 风格余额数据 (接口 {ep})")
                            return result, None
                            
                    # 场景 D: 最基础的扁平字典返回 {"balance": 100} 或 {"total_balance": 100}
                    else:
                        for balance_key in ["total_balance", "balance", "total", "amount", "available_balance"]:
                            if balance_key in data:
                                val = data[balance_key]
                                logger.info(f"Custom 模式：在 {ep} 中找到 '{balance_key}': {val}")
                                return [BalanceInfo(
                                    currency=data.get("currency", data.get("unit", "USD")),
                                    total_balance=str(val),
                                    granted_balance=str(data.get("granted_balance", data.get("granted", "0"))),
                                    topped_up_balance=str(data.get("topped_up_balance", data.get("topped_up", "0"))),
                                    is_available=data.get("is_available", True),
                                )], None
                else:
                    try:
                        resp.raise_for_status()
                    except Exception as e:
                        last_error = handle_httpx_error(e, f"探测 {ep}")
            except Exception as e:
                last_error = handle_httpx_error(e, f"探测 {ep}")
                logger.debug(f"Custom 模式：探测 {url} 发生异常: {str(e)}")
                continue
                
        logger.warning(f"Custom 模式：尝试了所有探测接口，均无法获取有效余额数据 (Base URL: {base_url})")
        return None, last_error


# 适配器注册表
ADAPTERS: dict[str, BaseAdapter] = {
    "openai": OpenAIAdapter(),
    "deepseek": DeepSeekAdapter(),
    "anthropic": AnthropicAdapter(),
    "siliconflow": SiliconFlowAdapter(),
    "openrouter": OpenRouterAdapter(),
    "mimo": MiMoAdapter(),
    "custom": CustomAdapter(),
}

# 默认 Base URL
DEFAULT_BASE_URLS: dict[str, str] = {
    "openai": "https://api.openai.com",
    "deepseek": "https://api.deepseek.com",
    "anthropic": "https://api.anthropic.com",
    "siliconflow": "https://api.siliconflow.cn",
    "openrouter": "https://openrouter.ai",
    "mimo": "https://api.xiaomimimo.com",
}

