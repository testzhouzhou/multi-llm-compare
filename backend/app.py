"""
多模型对比工具 - 主应用
"""
import asyncio
import csv
import io
import json
import random
import re
import time
import unicodedata
from datetime import datetime
from typing import Any, Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy import text
from sse_starlette.sse import EventSourceResponse

from config import (
    HOST, PORT, PREDEFINED_MODELS, DIFF_ANALYZER_MODEL,
    DIFF_ANALYZER_BASE_URL, DIFF_ANALYZER_PROTOCOL, REQUEST_TIMEOUT, get_api_key
)
from database import get_db, CompareHistory, CustomModel, SessionLocal, engine

# 精确集合，禁止用 Unicode range（会误删中文）。So/Sk 分类兜底，中文属 Lo 不会被伤。
_EMOJI_EXACT = set(
    "\U00002B05\U0001F4AF\U0001F44E\U00002757\U000026AB\U0001F604\U0001F4CD\U0001F60B\U000023F1\U0001F5D1\U0001F602\U0000FE0F\U0001F44D\U0001F6E0\U0001F527\U0001F916\U0001F609\U0001F9EA\U0001F4DA\U0001F7E2\U0001F970\U0001F4E4\U000026AA\U0001F64C\U0001F4CA\U0001F50D\U0001F4C9\U0001F504\U0001F60E\U0001F603\U0001F333\U0001F4BB\U000026A1\U0001F512\U0001F600\U0001F644\U0001F3AF\U0001F7E1\U00002705\U0001F61C\U0001F310\U0001F306\U0001F929\U0001F5FD\U0001F601\U0001F60A\U00002744\U0001F3F0\U0001F525\U0001F4A7\U0001F3DB\U0001F4A1\U0001F308\U00002728\U0001F60D\U0001F9E0\U00002B50\U000026A0\U0001F4CC\U0001F389\U0001F303\U0001F305\U0001F606\U0001F914\U0001F9E9\U0001F6E1\U000023F3\U0001F511\U0001F60F\U00002699\U0001F618\U0001F343\U0001F680\U0001F4AA\U0001F5FC\U0001F4DD\U00002B06\U00002600\U0001F4E6\U0001F3EF\U0001F514\U0001F4C8\U0000274C\U0001F534\U0001F617\U00002B07\U0001F64F\U0001F535\U0001F3E1\U00002753\U0001F6D1\U0001F5C2\U000027A1\U0001F4C1\U0001F4E5\U0001F917\U0001F4CB\U0001F331\U0001F605\U0001F923"
) | {
    "\ufe0f",
    "\u23ed",
    "\u2699",
    "\u26a0",
    "\u26a1",
    "\u2705",
    "\u2728",
    "\u274c",
    "\U0001f3db",
    "\U0001f4cd",
    "\U0001f343",
    "\U0001f4a1",
    "\U0001f916",
    "\U0001f4ca",
}

_KEEP_SYMBOLS = set("→←↑↓│├└┤┬┴┼─━┃①②③④⑤⑥⑦⑧⑨⑩×≈¥※·•°℃℉‰")


def strip_emoji(text: str) -> str:
    """去掉 emoji / 装饰符号，保留中文和技术符号。"""
    if not text:
        return text
    chars = []
    for ch in text:
        if ch in _KEEP_SYMBOLS:
            chars.append(ch)
            continue
        if ch in _EMOJI_EXACT:
            continue
        cat = unicodedata.category(ch)
        if cat in ("So", "Sk") and ord(ch) > 0x2000:
            continue
        chars.append(ch)
    cleaned = "".join(chars)
    cleaned = re.sub(r"[（(]\s*[）)]", "", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() if cleaned.strip() != cleaned else cleaned


def strip_emoji_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        return strip_emoji(obj)
    if isinstance(obj, list):
        return [strip_emoji_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: strip_emoji_obj(v) for k, v in obj.items()}
    return obj


def strip_result_emoji(result: dict) -> dict:
    if not isinstance(result, dict):
        return result
    out = dict(result)
    if out.get("content"):
        out["content"] = strip_emoji(out["content"])
    if out.get("error"):
        out["error"] = strip_emoji(out["error"])
    return out

app = FastAPI(title="多模型对比工具", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==================== 覆盖层表 ====================

def ensure_override_table():
    """创建 model_overrides（不改动既有表结构）"""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS model_overrides (
              model_id   TEXT PRIMARY KEY,
              base_url   TEXT,
              model      TEXT,
              api_key    TEXT,
              protocol   TEXT,
              enabled    INTEGER,
              updated_at DATETIME,
              name       TEXT
            )
        """))
        # 兼容：若旧库缺 name 列则补上
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(model_overrides)")).fetchall()}
        if "name" not in cols:
            conn.execute(text("ALTER TABLE model_overrides ADD COLUMN name TEXT"))


ensure_override_table()


def ensure_diff_detail_column():
    """给 compare_history 追加 diff_detail 列（不删库、不改 ORM 模型文件）"""
    with engine.begin() as conn:
        cols = {r[1] for r in conn.execute(text("PRAGMA table_info(compare_history)")).fetchall()}
        if "diff_detail" not in cols:
            conn.execute(text("ALTER TABLE compare_history ADD COLUMN diff_detail TEXT"))


ensure_diff_detail_column()


def _row_to_override(row) -> dict:
    if not row:
        return {}
    # sqlite Row / mapping
    keys = row._mapping.keys() if hasattr(row, "_mapping") else row.keys()
    data = dict(row._mapping) if hasattr(row, "_mapping") else {k: row[k] for k in keys}
    return data


def get_override(db, model_id: str) -> Optional[dict]:
    row = db.execute(
        text("SELECT * FROM model_overrides WHERE model_id = :mid"),
        {"mid": str(model_id)},
    ).fetchone()
    return _row_to_override(row) if row else None


def list_overrides(db) -> dict[str, dict]:
    rows = db.execute(text("SELECT * FROM model_overrides")).fetchall()
    out = {}
    for row in rows:
        d = _row_to_override(row)
        out[str(d["model_id"])] = d
    return out


def upsert_override(db, model_id: str, fields: dict):
    """fields 中值为 None 表示该列设为 NULL（清除覆盖）"""
    mid = str(model_id)
    existing = get_override(db, mid)
    now = datetime.now().isoformat(sep=" ", timespec="seconds")
    cols = ["base_url", "model", "api_key", "protocol", "enabled", "name"]
    if not existing:
        values = {c: fields.get(c) for c in cols if c in fields}
        # 未提及的列保持 NULL
        for c in cols:
            values.setdefault(c, None)
        db.execute(
            text("""
                INSERT INTO model_overrides
                (model_id, base_url, model, api_key, protocol, enabled, updated_at, name)
                VALUES (:model_id, :base_url, :model, :api_key, :protocol, :enabled, :updated_at, :name)
            """),
            {"model_id": mid, "updated_at": now, **values},
        )
    else:
        sets = ["updated_at = :updated_at"]
        params = {"model_id": mid, "updated_at": now}
        for c in cols:
            if c in fields:
                sets.append(f"{c} = :{c}")
                params[c] = fields[c]
        db.execute(
            text(f"UPDATE model_overrides SET {', '.join(sets)} WHERE model_id = :model_id"),
            params,
        )
    db.commit()


def delete_override(db, model_id: str) -> bool:
    result = db.execute(
        text("DELETE FROM model_overrides WHERE model_id = :mid"),
        {"mid": str(model_id)},
    )
    db.commit()
    return (result.rowcount or 0) > 0


def _override_has_any(ov: Optional[dict]) -> bool:
    if not ov:
        return False
    for k in ("base_url", "model", "api_key", "protocol", "enabled", "name"):
        v = ov.get(k)
        if v is not None and v != "":
            return True
    return False


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****" + key[-2:] if len(key) > 4 else "****"
    return f"{key[:6]}****{key[-4:]}"


# ==================== Pydantic Models ====================

class ChatRequest(BaseModel):
    question: str
    models: list[str] = []
    stream: bool = False


class DiffRequest(BaseModel):
    question: str
    results: list[dict]


class ModelAddRequest(BaseModel):
    name: str
    base_url: str
    model: str
    api_key: str


class ModelUpdateRequest(BaseModel):
    name: Optional[str] = None
    base_url: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    protocol: Optional[str] = None
    enabled: Optional[bool] = None


class ExportCurrentRequest(BaseModel):
    question: str
    results: list[dict]
    diff_summary: str = ""
    diff_detail: Optional[dict] = None


class HistoryResponse(BaseModel):
    id: int
    question: str
    models: list[str]
    diff_summary: str
    created_at: str


# ==================== 核心逻辑 ====================

def _base_models_from_db(db) -> dict:
    """预定义 + 自定义（含未启用），不含覆盖层、不含明文 key 输出字段"""
    models = {}
    for mid, cfg in PREDEFINED_MODELS.items():
        models[mid] = {
            **cfg,
            "id": mid,
            "is_custom": False,
            "protocol": cfg.get("protocol", "openai"),
            "api_key": "",  # 预置默认不内嵌 key
        }
    if db:
        customs = db.query(CustomModel).all()
        for c in customs:
            cid = str(c.id)
            models[cid] = {
                "id": cid,
                "name": c.name,
                "base_url": c.base_url,
                "model": c.model,
                "api_key": c.api_key or "",
                "api_key_env": "",
                "enabled": bool(c.enabled),
                "is_custom": True,
                "protocol": "openai",
            }
    return models


def apply_override_to_config(base: dict, ov: Optional[dict]) -> dict:
    """DB 覆盖 > 代码/自定义默认；返回供 call_llm 使用的配置（可含明文 key）"""
    out = dict(base)
    if not ov:
        return out
    if ov.get("name") not in (None, ""):
        out["name"] = ov["name"]
    if ov.get("base_url") not in (None, ""):
        out["base_url"] = ov["base_url"]
    if ov.get("model") not in (None, ""):
        out["model"] = ov["model"]
    if ov.get("api_key") not in (None, ""):
        out["api_key"] = ov["api_key"]
    if ov.get("protocol") not in (None, ""):
        out["protocol"] = ov["protocol"]
    if ov.get("enabled") is not None:
        out["enabled"] = bool(ov["enabled"])
    return out


def get_all_models(db=None, for_call: bool = True) -> dict:
    """获取全部模型（预定义 + 自定义 + 覆盖层合并）"""
    models = _base_models_from_db(db)
    overrides = list_overrides(db) if db else {}
    for mid, base in list(models.items()):
        ov = overrides.get(str(mid))
        models[mid] = apply_override_to_config(base, ov)
    return models


def resolve_key_meta(cfg: dict, ov: Optional[dict]) -> tuple[str, str, str]:
    """返回 (plain_key_for_internal, key_source, key_masked)。plain 仅内部用。"""
    if ov and ov.get("api_key"):
        k = ov["api_key"]
        return k, "db", mask_key(k)
    # 自定义模型表内的 key（覆盖层 api_key 为 NULL 表示不覆盖，仍用表内值）
    if cfg.get("is_custom") and cfg.get("api_key"):
        k = cfg["api_key"]
        return k, "db", mask_key(k)
    env_name = cfg.get("api_key_env", "") or ""
    k = get_api_key(env_name, allow_env_alias=True)
    if k:
        return k, "env", mask_key(k)
    return "", "none", ""


def public_model_view(cfg: dict, ov: Optional[dict]) -> dict:
    """对外模型对象：绝不含明文 key"""
    plain, source, masked = resolve_key_meta(cfg, ov)
    return {
        "id": cfg["id"],
        "name": cfg.get("name", ""),
        "base_url": cfg.get("base_url", ""),
        "model": cfg.get("model", ""),
        "protocol": cfg.get("protocol", "openai"),
        "enabled": bool(cfg.get("enabled", True)),
        "api_key_env": cfg.get("api_key_env", ""),
        "is_custom": bool(cfg.get("is_custom", False)),
        "has_key": bool(plain),
        "key_masked": masked,
        "key_source": source,
        "overridden": _override_has_any(ov),
    }


def build_call_config(cfg: dict, ov: Optional[dict]) -> dict:
    """供 call_llm 使用：合并覆盖并解析最终 api_key"""
    merged = apply_override_to_config(cfg, ov)
    plain, _, _ = resolve_key_meta(merged, ov)
    # 显式 key 走 get_api_key 第 1 步；无则走 env/.env
    merged["api_key"] = get_api_key(
        merged.get("api_key_env", "") or "",
        api_key=plain or None,
        allow_env_alias=True,
    )
    return merged


async def call_llm(model_config: dict, question: str, stream: bool = False):
    """调用单个 LLM，返回结果或流式生成器"""
    api_key = model_config.get("api_key") or get_api_key(
        model_config.get("api_key_env", ""),
        allow_env_alias=True,
    )
    if not api_key:
        return {
            "model": model_config["name"],
            "model_id": model_config["id"],
            "content": "",
            "tokens": 0,
            "latency": 0,
            "error": f"未配置 API Key（{model_config.get('api_key_env', 'custom')}）",
        }

    protocol = model_config.get("protocol", "openai")
    base_url = model_config["base_url"].rstrip("/")

    if protocol == "anthropic":
        return await _call_anthropic(model_config, api_key, base_url, question, stream)
    else:
        return await _call_openai(model_config, api_key, base_url, question, stream)


async def _call_openai(model_config, api_key, base_url, question, stream):
    """OpenAI 兼容协议"""
    client = httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )

    payload = {
        "model": model_config["model"],
        "messages": [{"role": "user", "content": question}],
    }

    if stream:
        payload["stream"] = True

    start = time.time()
    try:
        if stream:
            return await _stream_response(client, model_config, payload, start)
        else:
            resp = await client.post("/chat/completions", json=payload)
            resp.raise_for_status()
            data = resp.json()
            latency = round(time.time() - start, 2)
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {
                "model": model_config["name"],
                "model_id": model_config["id"],
                "content": content,
                "tokens": tokens,
                "latency": latency,
                "error": None,
            }
    except Exception as e:
        latency = round(time.time() - start, 2)
        return {
            "model": model_config["name"],
            "model_id": model_config["id"],
            "content": "",
            "tokens": 0,
            "latency": latency,
            "error": str(e)[:200],
        }
    finally:
        await client.aclose()


async def _call_anthropic(model_config, api_key, base_url, question, stream):
    """Anthropic 协议"""
    client = httpx.AsyncClient(
        base_url=base_url,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01", "Content-Type": "application/json"},
        timeout=REQUEST_TIMEOUT,
    )

    payload = {
        "model": model_config["model"],
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": question}],
    }

    if stream:
        payload["stream"] = True

    start = time.time()
    try:
        if stream:
            return await _stream_anthropic(client, model_config, payload, start)
        else:
            resp = await client.post("/v1/messages", json=payload)
            resp.raise_for_status()
            data = resp.json()
            latency = round(time.time() - start, 2)
            content_parts = data.get("content", [])
            content = ""
            for part in content_parts:
                if part.get("type") == "text":
                    content += part.get("text", "")
                elif part.get("type") == "thinking":
                    pass
            tokens = data.get("usage", {}).get("input_tokens", 0) + data.get("usage", {}).get("output_tokens", 0)
            return {
                "model": model_config["name"],
                "model_id": model_config["id"],
                "content": content,
                "tokens": tokens,
                "latency": latency,
                "error": None,
            }
    except Exception as e:
        latency = round(time.time() - start, 2)
        return {
            "model": model_config["name"],
            "model_id": model_config["id"],
            "content": "",
            "tokens": 0,
            "latency": latency,
            "error": str(e)[:200],
        }
    finally:
        await client.aclose()


async def _stream_anthropic(client, model_config, payload, start_time):
    """Anthropic 流式响应生成器"""
    full_content = ""
    tokens = 0
    error = None
    try:
        async with client.stream("POST", "/v1/messages", json=payload) as resp:
            if resp.status_code != 200:
                error_text = (await resp.aread()).decode()
                error = f"HTTP {resp.status_code}: {error_text[:200]}"
                yield {
                    "type": "error",
                    "model": model_config["name"],
                    "model_id": model_config["id"],
                    "error": error,
                    "latency": round(time.time() - start_time, 2),
                }
                return

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    event_type = data.get("type")
                    if event_type == "content_block_delta":
                        delta = data.get("delta", {}).get("text", "")
                        if delta:
                            full_content += delta
                            yield {
                                "type": "content",
                                "model": model_config["name"],
                                "model_id": model_config["id"],
                                "delta": delta,
                                "content": full_content,
                            }
                    elif event_type == "message_stop":
                        usage = data.get("usage", {})
                        if not usage:
                            pass
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        error = str(e)[:200]
        yield {
            "type": "error",
            "model": model_config["name"],
            "model_id": model_config["id"],
            "error": error,
            "latency": round(time.time() - start_time, 2),
        }
        return

    latency = round(time.time() - start_time, 2)
    yield {
        "type": "done",
        "model": model_config["name"],
        "model_id": model_config["id"],
        "content": full_content,
        "tokens": tokens,
        "latency": latency,
        "error": error,
    }


async def _stream_response(client, model_config, payload, start_time):
    """流式响应生成器"""
    full_content = ""
    tokens = 0
    error = None
    try:
        async with client.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                error_text = (await resp.aread()).decode()
                error = f"HTTP {resp.status_code}: {error_text[:200]}"
                yield {
                    "type": "error",
                    "model": model_config["name"],
                    "model_id": model_config["id"],
                    "error": error,
                    "latency": round(time.time() - start_time, 2),
                }
                return

            async for line in resp.aiter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    data = json.loads(data_str)
                    delta = data.get("choices", [{}])[0].get("delta", {}).get("content", "")
                    if delta:
                        full_content += delta
                        yield {
                            "type": "content",
                            "model": model_config["name"],
                            "model_id": model_config["id"],
                            "delta": delta,
                            "content": full_content,
                        }
                    usage = data.get("usage")
                    if usage:
                        tokens = usage.get("total_tokens", tokens)
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        error = str(e)[:200]
        yield {
            "type": "error",
            "model": model_config["name"],
            "model_id": model_config["id"],
            "error": error,
            "latency": round(time.time() - start_time, 2),
        }
        return

    latency = round(time.time() - start_time, 2)
    yield {
        "type": "done",
        "model": model_config["name"],
        "model_id": model_config["id"],
        "content": full_content,
        "tokens": tokens,
        "latency": latency,
        "error": error,
    }


def _analyzer_config(db=None) -> tuple[str, str, str, str]:
    """默认评委配置（仅作兜底）。"""
    model = DIFF_ANALYZER_MODEL
    base_url = DIFF_ANALYZER_BASE_URL
    protocol = DIFF_ANALYZER_PROTOCOL or "openai"
    api_key = get_api_key("DASHSCOPE_API_KEY", allow_env_alias=True)

    target_id = None
    if DIFF_ANALYZER_MODEL in PREDEFINED_MODELS:
        target_id = DIFF_ANALYZER_MODEL
    else:
        for mid, cfg in PREDEFINED_MODELS.items():
            if cfg.get("model") == DIFF_ANALYZER_MODEL:
                target_id = mid
                break
        if not target_id and DIFF_ANALYZER_MODEL.startswith("qwen"):
            target_id = "qwen-plus"

    if db and target_id and target_id in PREDEFINED_MODELS:
        ov = get_override(db, target_id)
        base = {**PREDEFINED_MODELS[target_id], "api_key": "", "is_custom": False}
        merged = apply_override_to_config(base, ov)
        base_url = merged.get("base_url") or base_url
        model = merged.get("model") or model
        protocol = merged.get("protocol") or protocol
        plain, _, _ = resolve_key_meta(merged, ov)
        if plain:
            api_key = plain

    return model, base_url, protocol, api_key


def _model_family(cfg: dict) -> str:
    blob = f"{cfg.get('id', '')} {cfg.get('name', '')} {cfg.get('model', '')}".lower()
    if any(x in blob for x in ("qwen", "千问", "dashscope")):
        return "qwen"
    if "deepseek" in blob:
        return "deepseek"
    if any(x in blob for x in ("doubao", "豆包", "volces", "ark.")):
        return "doubao"
    if any(x in blob for x in ("glm", "智谱", "zhipu", "bigmodel")):
        return "glm"
    if any(x in blob for x in ("hunyuan", "元宝")):
        return "hunyuan"
    return str(cfg.get("id") or "other")


def _list_analyzer_candidates(
    db,
    competing_ids: set[str],
    competing_families: set[str],
    skip_ids: Optional[set[str]] = None,
) -> list[tuple[str, dict]]:
    """评委候选：没参赛、不同厂商优先；跳过本轮已失败/限流的模型；豆包靠后（易 429）。"""
    skip_ids = skip_ids or set()
    all_models: dict[str, dict] = {}
    if db:
        bases = _base_models_from_db(db)
        overrides = list_overrides(db)
        for mid, base in bases.items():
            all_models[mid] = build_call_config(base, overrides.get(str(mid)))
    else:
        for mid, base in PREDEFINED_MODELS.items():
            all_models[mid] = build_call_config({**base, "api_key": ""}, None)

    candidates = []
    for mid, cfg in all_models.items():
        if mid in skip_ids:
            continue
        if not cfg.get("enabled", True) or not cfg.get("api_key"):
            continue
        family = _model_family(cfg)
        not_self = mid not in competing_ids
        not_family = family not in competing_families
        if not_self and not_family:
            rank = 0
        elif not_self:
            rank = 1
        else:
            rank = 2
        unstable = 1 if family == "doubao" else 0
        prefer_non_qwen = 1 if family == "qwen" else 0
        candidates.append((rank, unstable, prefer_non_qwen, mid, cfg))

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))
    return [(mid, cfg) for _, _, _, mid, cfg in candidates]


def _pick_analyzer(
    db,
    competing_ids: set[str],
    competing_families: set[str],
    skip_ids: Optional[set[str]] = None,
) -> tuple[str, str, str, str, str]:
    """选评委：优先没参赛、且不是同一厂商；避免千问审千问。"""
    listed = _list_analyzer_candidates(db, competing_ids, competing_families, skip_ids)
    if not listed:
        model, base_url, protocol, api_key = _analyzer_config(db)
        return model, base_url, protocol, api_key, model
    mid, cfg = listed[0]
    return (
        cfg.get("model") or mid,
        (cfg.get("base_url") or "").rstrip("/"),
        cfg.get("protocol") or "openai",
        cfg.get("api_key") or "",
        cfg.get("name") or mid,
    )


def _is_rate_limited(exc: BaseException) -> bool:
    text = str(exc)
    return "429" in text or "Too Many Requests" in text


def _short_judge_error(exc: BaseException) -> str:
    if _is_rate_limited(exc):
        return "评委接口限流（429），请稍后再试"
    return str(exc)[:80]


async def _ask_judge(model: str, base_url: str, protocol: str, api_key: str, prompt: str) -> str:
    if protocol == "anthropic":
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        path = "/v1/messages"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }
    else:
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        path = "/chat/completions"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt}],
        }

    client = httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        headers=headers,
        timeout=90,
    )
    try:
        resp = await client.post(path, json=payload)
        resp.raise_for_status()
        data = resp.json()
        if protocol == "anthropic":
            content = ""
            for part in data.get("content", []):
                if part.get("type") == "text":
                    content += part.get("text", "")
            return content
        return data["choices"][0]["message"]["content"] or ""
    finally:
        await client.aclose()


def _unblind_text(text: str, alias_to_name: dict[str, str]) -> str:
    if not text:
        return text
    for alias, name in sorted(alias_to_name.items(), key=lambda x: -len(x[0])):
        text = text.replace(alias, name)
    return text


def _unblind_obj(obj: Any, alias_to_name: dict[str, str]) -> Any:
    if isinstance(obj, str):
        return _unblind_text(obj, alias_to_name)
    if isinstance(obj, list):
        return [_unblind_obj(x, alias_to_name) for x in obj]
    if isinstance(obj, dict):
        return {k: _unblind_obj(v, alias_to_name) for k, v in obj.items()}
    return obj


def _empty_diff_detail(overall: str = "", parse_error: bool = False) -> dict:
    return {
        "overall": overall or "",
        "dimensions": [],
        "models": [],
        "verdict": "",
        "parse_error": parse_error,
    }


def extract_json_object(text: str) -> Optional[dict]:
    """从模型输出中提取 JSON 对象：支持裸 JSON、```json 代码块、前后缀废话。"""
    if not text or not str(text).strip():
        return None
    raw = str(text).strip()

    # 直接 loads
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # ```json ... ``` / ``` ... ```
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
    if fence:
        chunk = fence.group(1).strip()
        try:
            obj = json.loads(chunk)
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass

    # 取第一个 { 到最后一个 }
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(raw[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    return None


def normalize_diff_detail(obj: dict, expected_names: list[str]) -> dict:
    """规范化结构；尽量保留模型条目。"""
    detail = {
        "overall": str(obj.get("overall") or "").strip(),
        "dimensions": [],
        "models": [],
        "verdict": str(obj.get("verdict") or "").strip(),
        "parse_error": False,
    }
    for d in obj.get("dimensions") or []:
        if not isinstance(d, dict):
            continue
        detail["dimensions"].append({
            "dimension": str(d.get("dimension") or "").strip(),
            "detail": str(d.get("detail") or "").strip(),
        })
    for m in obj.get("models") or []:
        if not isinstance(m, dict):
            continue
        detail["models"].append({
            "name": str(m.get("name") or "").strip(),
            "core": str(m.get("core") or "").strip(),
            "unique": str(m.get("unique") or "").strip(),
            "diff_vs_others": str(m.get("diff_vs_others") or "").strip(),
            "pros": str(m.get("pros") or "").strip(),
            "cons": str(m.get("cons") or "").strip(),
            "best_for": str(m.get("best_for") or "").strip(),
        })
    # 若缺某些模型名，用占位补齐（不编造内容，字段留空提示）
    have = {m["name"] for m in detail["models"]}
    for name in expected_names:
        if name not in have:
            detail["models"].append({
                "name": name,
                "core": "",
                "unique": "",
                "diff_vs_others": "",
                "pros": "",
                "cons": "",
                "best_for": "",
            })
    return detail


def build_diff_summary_md(detail: dict) -> str:
    """由结构化 diff_detail 拼出兼容用的 markdown 纯文本。"""
    lines = []
    overall = detail.get("overall") or ""
    if overall:
        lines.append(f"**一句话总评**：{overall}")
        lines.append("")
    if detail.get("judge"):
        lines.append(f"**评委**：{detail['judge']}（匿名评卷，不看厂商名）")
        lines.append("")
    dims = detail.get("dimensions") or []
    if dims:
        lines.append("## 横向维度对比")
        lines.append("")
        for d in dims:
            lines.append(f"- **{d.get('dimension') or '维度'}**：{d.get('detail') or ''}")
        lines.append("")
    models = detail.get("models") or []
    if models:
        lines.append("## 各模型要点")
        lines.append("")
        for m in models:
            name = m.get("name") or "未知模型"
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"- **核心观点**：{m.get('core') or ''}")
            lines.append(f"- **独有点**：{m.get('unique') or ''}")
            lines.append(f"- **与其他差异**：{m.get('diff_vs_others') or ''}")
            lines.append(f"- **优势**：{m.get('pros') or ''}")
            lines.append(f"- **短板**：{m.get('cons') or ''}")
            lines.append(f"- **适合场景**：{m.get('best_for') or ''}")
            lines.append("")
    verdict = detail.get("verdict") or ""
    if verdict:
        lines.append("## 总结推荐")
        lines.append("")
        lines.append(verdict)
    return "\n".join(lines).strip() or overall


def parse_diff_response(raw_text: str, expected_names: list[str]) -> tuple[str, dict]:
    """解析分析模型输出 → (diff_summary, diff_detail)；失败不抛异常。"""
    raw = (raw_text or "").strip() or "分析失败"
    obj = extract_json_object(raw)
    if not obj:
        detail = _empty_diff_detail(overall=raw, parse_error=True)
        return raw, detail
    detail = normalize_diff_detail(obj, expected_names)
    summary = build_diff_summary_md(detail)
    if not summary:
        summary = raw
    return summary, detail


def _load_history_diff_detail(db, history_id: int) -> Optional[dict]:
    row = db.execute(
        text("SELECT diff_detail FROM compare_history WHERE id = :id"),
        {"id": history_id},
    ).fetchone()
    if not row:
        return None
    val = row[0] if not hasattr(row, "_mapping") else row._mapping.get("diff_detail")
    if not val:
        return None
    if isinstance(val, dict):
        return val
    try:
        return json.loads(val)
    except Exception:
        return None


def _save_history_diff_detail(db, history_id: int, detail: Optional[dict]):
    if detail is None:
        return
    db.execute(
        text("UPDATE compare_history SET diff_detail = :dd WHERE id = :id"),
        {"dd": json.dumps(detail, ensure_ascii=False), "id": history_id},
    )
    db.commit()


async def analyze_diff(question: str, results: list[dict], db=None) -> tuple[str, dict]:
    """用 LLM 分析差异，返回 (diff_summary_md, diff_detail_obj)。

    评委看不到真实模型名（模型A/B/C），并尽量选一个没参赛、不同厂商的模型当评委。
    """
    valid = [r for r in results if r.get("content")]
    if len(valid) < 2:
        msg = "至少需要 2 个成功返回的模型才能进行差异分析。"
        return msg, _empty_diff_detail(overall=msg)

    competing_ids = {str(r.get("model_id") or "") for r in results if r.get("model_id")}
    skip_ids = {
        str(r.get("model_id") or "")
        for r in results
        if r.get("error") and ("429" in str(r.get("error")) or "Too Many Requests" in str(r.get("error")))
    }
    competing_families = set()
    for r in valid:
        competing_families.add(_model_family({
            "id": r.get("model_id") or "",
            "name": r.get("model") or "",
            "model": r.get("model_id") or "",
        }))

    shuffled = list(valid)
    random.shuffle(shuffled)
    alias_to_name: dict[str, str] = {}
    alias_list: list[str] = []
    parts = []
    for i, r in enumerate(shuffled):
        alias = f"模型{chr(ord('A') + i)}"
        real_name = r.get("model") or r.get("model_id") or "未知模型"
        alias_to_name[alias] = real_name
        alias_list.append(alias)
        content = strip_emoji(r["content"][:1500])
        parts.append(f"--- {alias} ---\n{content}\n")

    names_hint = "、".join(alias_list)
    example = {
        "overall": "一句话总评（哪个代号最适合干什么）",
        "dimensions": [
            {"dimension": "内容深度", "detail": f"点名对比：{names_hint} 在此维度的差异"},
            {"dimension": "准确性/严谨性", "detail": "..."},
            {"dimension": "结构化程度", "detail": "..."},
            {"dimension": "实用性", "detail": "..."},
            {"dimension": "篇幅与冗余度", "detail": "..."},
        ],
        "models": [
            {
                "name": alias_list[0],
                "core": "核心观点 1-2 句",
                "unique": "独有点",
                "diff_vs_others": "与其余代号的主要差异（点名代号）",
                "pros": "优势",
                "cons": "短板",
                "best_for": "适合场景",
            }
        ],
        "verdict": "按场景总结推荐（只用代号）",
    }
    for n in alias_list[1:]:
        example["models"].append({
            "name": n,
            "core": "...",
            "unique": "...",
            "diff_vs_others": "...",
            "pros": "...",
            "cons": "...",
            "best_for": "...",
        })

    prompt = f"""问题：{question}

以下是 {len(shuffled)} 份匿名回复（代号随机分配，与厂商无关）：

{''.join(parts)}

请严格输出 **一个 JSON 对象**（不要输出 Markdown 标题），字段与含义如下：
{json.dumps(example, ensure_ascii=False, indent=2)}

硬性约束：
1. 只能使用代号：{names_hint}。禁止出现任何厂商名、产品名（如千问、DeepSeek、豆包、智谱、元宝、GPT）。
2. models 数组必须恰好包含上述每一个代号各一条（共 {len(alias_list)} 条），字段 core/unique/diff_vs_others/pros/cons/best_for 均非空。
3. dimensions 至少覆盖：内容深度、准确性/严谨性、结构化程度、实用性（有无代码/可操作步骤）、篇幅与冗余度。
4. 只根据上文回复归纳，不许编造回复里没有的事实。
5. 只输出 JSON（可以包在 ```json 代码块里），不要其它说明文字。
6. 全文不要 emoji、不要转述原文里的表情符号；结构化用「标题 / 条目 / 分区」这类文字描述。
7. 不要因为某份回复更长或更像你自己的文风就判它更好。"""

    candidates = _list_analyzer_candidates(db, competing_ids, competing_families, skip_ids)
    if not candidates:
        msg = "差异分析需要至少一个可用的评委模型 API Key。"
        return msg, _empty_diff_detail(overall=msg, parse_error=True)

    last_err = None
    for _mid, cfg in candidates:
        model = cfg.get("model") or _mid
        base_url = (cfg.get("base_url") or "").rstrip("/")
        protocol = cfg.get("protocol") or "openai"
        analyzer_key = cfg.get("api_key") or ""
        judge_name = cfg.get("name") or _mid
        if not analyzer_key or not base_url:
            continue
        try:
            content = await _ask_judge(model, base_url, protocol, analyzer_key, prompt)
        except Exception as e:
            last_err = e
            if _is_rate_limited(e):
                continue
            msg = f"差异分析失败：{_short_judge_error(e)}"
            return msg, _empty_diff_detail(overall=msg, parse_error=True)
        if not (content or "").strip():
            last_err = RuntimeError("空回复")
            continue
        summary, detail = parse_diff_response(content, alias_list)
        detail = _unblind_obj(detail, alias_to_name)
        detail["judge"] = judge_name
        summary = _unblind_text(build_diff_summary_md(detail), alias_to_name)
        return strip_emoji(summary), strip_emoji_obj(detail)

    msg = f"差异分析失败：{_short_judge_error(last_err) if last_err else '没有可用评委'}"
    return msg, _empty_diff_detail(overall=msg, parse_error=True)


# ==================== 导出工具 ====================

def _export_filename(fmt: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    ext = {"md": "md", "json": "json", "csv": "csv"}.get(fmt, fmt)
    return f"mlc_{stamp}.{ext}"


def _content_disposition(filename: str) -> str:
    return f"attachment; filename*=UTF-8''{quote(filename)}"


def build_export_payload(
    question: str,
    results: list[dict],
    diff_summary: str,
    diff_detail: Optional[dict] = None,
) -> dict:
    return {
        "question": question,
        "exported_at": datetime.now().isoformat(sep=" ", timespec="seconds"),
        "results": strip_emoji_obj(results or []),
        "diff_summary": strip_emoji(diff_summary or ""),
        "diff_detail": strip_emoji_obj(diff_detail) if diff_detail else diff_detail,
    }


def render_export_md(payload: dict) -> str:
    lines = [
        f"# 多模型对比导出",
        f"",
        f"- 问题：{payload['question']}",
        f"- 导出时间：{payload['exported_at']}",
        f"",
        f"## 差异汇总",
        f"",
        payload.get("diff_summary") or "（无）",
        f"",
    ]
    detail = payload.get("diff_detail") or {}
    models_detail = detail.get("models") if isinstance(detail, dict) else None
    if models_detail:
        lines.append("## 分模型小结")
        lines.append("")
        for m in models_detail:
            name = m.get("name") or ""
            lines.append(f"### {name}")
            lines.append("")
            lines.append(f"- 核心观点：{m.get('core') or ''}")
            lines.append(f"- 独有点：{m.get('unique') or ''}")
            lines.append(f"- 与其他差异：{m.get('diff_vs_others') or ''}")
            lines.append(f"- 优势：{m.get('pros') or ''}")
            lines.append(f"- 短板：{m.get('cons') or ''}")
            lines.append(f"- 适合场景：{m.get('best_for') or ''}")
            lines.append("")
    lines.append("## 各模型回复")
    lines.append("")
    for r in payload.get("results") or []:
        name = r.get("model") or ""
        mid = r.get("model_id") or ""
        lines.append(f"### {name} (`{mid}`)")
        lines.append(f"")
        lines.append(f"- 耗时：{r.get('latency', '')}s")
        lines.append(f"- Tokens：{r.get('tokens', '')}")
        if r.get("error"):
            lines.append(f"- 错误：{r['error']}")
            lines.append("")
        else:
            lines.append("")
            lines.append(r.get("content") or "")
            lines.append("")
    return "\n".join(lines)


def render_export_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def render_export_csv(payload: dict) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow([
        "question", "exported_at", "model", "model_id", "latency", "tokens",
        "error", "content", "diff_summary", "diff_detail",
    ])
    q = payload.get("question", "")
    ts = payload.get("exported_at", "")
    diff = payload.get("diff_summary", "")
    detail = payload.get("diff_detail")
    detail_s = json.dumps(detail, ensure_ascii=False) if detail else ""
    results = payload.get("results") or []
    if not results:
        writer.writerow([q, ts, "", "", "", "", "", "", diff, detail_s])
    else:
        for i, r in enumerate(results):
            writer.writerow([
                q if i == 0 else "",
                ts if i == 0 else "",
                r.get("model", ""),
                r.get("model_id", ""),
                r.get("latency", ""),
                r.get("tokens", ""),
                r.get("error") or "",
                r.get("content") or "",
                diff if i == 0 else "",
                detail_s if i == 0 else "",
            ])
    return "\ufeff" + buf.getvalue()


def export_response(fmt: str, payload: dict) -> Response:
    fmt = (fmt or "md").lower()
    if fmt not in ("md", "json", "csv"):
        raise HTTPException(400, "format 必须是 md|json|csv")
    filename = _export_filename(fmt)
    if fmt == "md":
        body = render_export_md(payload)
        media = "text/markdown; charset=utf-8"
    elif fmt == "json":
        body = render_export_json(payload)
        media = "application/json; charset=utf-8"
    else:
        body = render_export_csv(payload)
        media = "text/csv; charset=utf-8"
    data = body.encode("utf-8")
    return Response(
        content=data,
        media_type=media,
        headers={"Content-Disposition": _content_disposition(filename)},
    )


# ==================== API 路由 ====================

@app.get("/api/models")
def list_models():
    """获取可用模型列表（含覆盖层元数据，不含明文 key）"""
    db = SessionLocal()
    try:
        bases = _base_models_from_db(db)
        overrides = list_overrides(db)
        data = []
        for mid, base in bases.items():
            ov = overrides.get(str(mid))
            merged = apply_override_to_config(base, ov)
            data.append(public_model_view(merged, ov))
        return {"code": 0, "data": data}
    finally:
        db.close()


def _get_one_public_model(db, model_id: str) -> dict:
    bases = _base_models_from_db(db)
    mid = str(model_id)
    if mid not in bases:
        raise HTTPException(404, "模型不存在")
    ov = get_override(db, mid)
    merged = apply_override_to_config(bases[mid], ov)
    return public_model_view(merged, ov)


@app.put("/api/models/{model_id}")
def update_model(model_id: str, req: ModelUpdateRequest):
    """更新模型覆盖层（字段不传=不动；null/\"\"=清除覆盖；api_key=__KEEP__=保留）"""
    db = SessionLocal()
    try:
        bases = _base_models_from_db(db)
        mid = str(model_id)
        if mid not in bases:
            raise HTTPException(404, "模型不存在")

        raw = req.model_dump(exclude_unset=True)
        fields: dict[str, Any] = {}

        if "api_key" in raw:
            if raw["api_key"] == "__KEEP__":
                pass
            elif raw["api_key"] is None or raw["api_key"] == "":
                fields["api_key"] = None
            else:
                fields["api_key"] = raw["api_key"]

        for col in ("name", "base_url", "model", "protocol"):
            if col in raw:
                val = raw[col]
                fields[col] = None if val is None or val == "" else val

        if "enabled" in raw:
            if raw["enabled"] is None:
                fields["enabled"] = None
            else:
                fields["enabled"] = 1 if raw["enabled"] else 0

        if fields:
            upsert_override(db, mid, fields)

        return {"code": 0, "data": _get_one_public_model(db, mid)}
    finally:
        db.close()


@app.delete("/api/models/{model_id}/override")
def reset_model_override(model_id: str):
    """删除覆盖，回到代码/自定义默认配置"""
    db = SessionLocal()
    try:
        bases = _base_models_from_db(db)
        mid = str(model_id)
        if mid not in bases:
            raise HTTPException(404, "模型不存在")
        delete_override(db, mid)
        return {"code": 0, "data": _get_one_public_model(db, mid)}
    finally:
        db.close()


@app.post("/api/models/{model_id}/test")
async def test_model(model_id: str):
    """真发一次最小请求，验证连通性"""
    db = SessionLocal()
    try:
        bases = _base_models_from_db(db)
        mid = str(model_id)
        if mid not in bases:
            raise HTTPException(404, "模型不存在")
        ov = get_override(db, mid)
        merged = apply_override_to_config(bases[mid], ov)

        # 解析 key：DB > .env > env；测试接口禁止别名兜底
        plain = ""
        if ov and ov.get("api_key"):
            plain = ov["api_key"]
        elif merged.get("is_custom") and merged.get("api_key"):
            plain = merged["api_key"]
        api_key = get_api_key(
            merged.get("api_key_env", "") or "",
            api_key=plain or None,
            allow_env_alias=False,
        )
        if not api_key:
            return {
                "code": 0,
                "data": {
                    "ok": False,
                    "status": 0,
                    "latency": 0,
                    "error": f"未配置 API Key（{merged.get('api_key_env') or 'custom'}）",
                    "content": "",
                },
            }

        protocol = merged.get("protocol", "openai") or "openai"
        base_url = (merged.get("base_url") or "").rstrip("/")
        start = time.time()
        try:
            if protocol == "anthropic":
                async with httpx.AsyncClient(
                    base_url=base_url,
                    headers={
                        "x-api-key": api_key,
                        "anthropic-version": "2023-06-01",
                        "Content-Type": "application/json",
                    },
                    timeout=60,
                ) as client:
                    resp = await client.post("/v1/messages", json={
                        "model": merged["model"],
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": '说"好"'}],
                    })
                    latency = round(time.time() - start, 2)
                    text_out = ""
                    err = None
                    if resp.status_code == 200:
                        data = resp.json()
                        for part in data.get("content", []):
                            if part.get("type") == "text":
                                text_out += part.get("text", "")
                    else:
                        err = resp.text
                    return {
                        "code": 0,
                        "data": {
                            "ok": resp.status_code == 200,
                            "status": resp.status_code,
                            "latency": latency,
                            "error": err,
                            "content": text_out,
                        },
                    }
            else:
                async with httpx.AsyncClient(
                    base_url=base_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    timeout=60,
                ) as client:
                    resp = await client.post("/chat/completions", json={
                        "model": merged["model"],
                        "max_tokens": 10,
                        "messages": [{"role": "user", "content": '说"好"'}],
                    })
                    latency = round(time.time() - start, 2)
                    text_out = ""
                    err = None
                    if resp.status_code == 200:
                        data = resp.json()
                        text_out = (
                            data.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "")
                            or ""
                        )
                    else:
                        err = resp.text
                    return {
                        "code": 0,
                        "data": {
                            "ok": resp.status_code == 200,
                            "status": resp.status_code,
                            "latency": latency,
                            "error": err,
                            "content": text_out,
                        },
                    }
        except Exception as e:
            latency = round(time.time() - start, 2)
            return {
                "code": 0,
                "data": {
                    "ok": False,
                    "status": 0,
                    "latency": latency,
                    "error": str(e),
                    "content": "",
                },
            }
    finally:
        db.close()


@app.post("/api/chat/compare")
async def compare_chat(req: ChatRequest):
    """并发请求多个模型（非流式）"""
    db = SessionLocal()
    try:
        bases = _base_models_from_db(db)
        overrides = list_overrides(db)
        all_models = {}
        for mid, base in bases.items():
            ov = overrides.get(str(mid))
            all_models[mid] = build_call_config(base, ov)

        if req.models:
            model_ids = [m for m in req.models if m in all_models and all_models[m].get("enabled")]
        else:
            model_ids = [m for m in all_models if all_models[m].get("enabled")]

        tasks = [call_llm(all_models[mid], req.question) for mid in model_ids]
        results = await asyncio.gather(*tasks) if tasks else []
        results_list = [strip_result_emoji(r) for r in results]

        diff_summary, diff_detail = await analyze_diff(req.question, results_list, db=db)

        history = CompareHistory(
            question=req.question,
            models=model_ids,
            results=results_list,
            diff_summary=diff_summary,
        )
        db.add(history)
        db.commit()
        _save_history_diff_detail(db, history.id, diff_detail)

        return {
            "code": 0,
            "data": {
                "results": results_list,
                "diff_summary": diff_summary,
                "diff_detail": diff_detail,
                "history_id": history.id,
            }
        }
    finally:
        db.close()


@app.get("/api/chat/compare/stream")
async def compare_chat_stream(question: str, models: str = ""):
    """并发请求多个模型（流式 SSE）"""
    db = SessionLocal()
    bases = _base_models_from_db(db)
    overrides = list_overrides(db)
    all_models = {}
    for mid, base in bases.items():
        ov = overrides.get(str(mid))
        all_models[mid] = build_call_config(base, ov)
    model_ids = [m for m in models.split(",") if m] if models else [m for m in all_models if all_models[m].get("enabled")]
    model_ids = [m for m in model_ids if m in all_models and all_models[m].get("enabled")]
    db.close()

    async def event_generator():
        tasks = []
        for mid in model_ids:
            if mid in all_models:
                tasks.append(_stream_model_wrapper(all_models[mid], question))
        await asyncio.gather(*tasks)

    return EventSourceResponse(event_generator())


async def _stream_model_wrapper(model_config, question):
    """包装流式调用，通过文件传递结果（SSE 限制）"""
    import tempfile
    import os

    tmpfile = tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.jsonl')
    tmp_path = tmpfile.name

    async def _write_stream():
        try:
            async for event in call_llm(model_config, question, stream=True):
                tmpfile.write(json.dumps(event, ensure_ascii=False) + "\n")
                tmpfile.flush()
        finally:
            tmpfile.close()

    task = asyncio.create_task(_write_stream())

    last_pos = 0
    while not task.done():
        try:
            with open(tmp_path, 'r') as f:
                f.seek(last_pos)
                new_data = f.read()
                last_pos = f.tell()
                for line in new_data.strip().split('\n'):
                    if line:
                        yield {"event": "model-stream", "data": line}
        except FileNotFoundError:
            pass
        await asyncio.sleep(0.1)

    try:
        with open(tmp_path, 'r') as f:
            f.seek(last_pos)
            new_data = f.read()
            for line in new_data.strip().split('\n'):
                if line:
                    yield {"event": "model-stream", "data": line}
    except FileNotFoundError:
        pass

    os.unlink(tmp_path)


@app.post("/api/chat/diff")
async def diff_only(req: DiffRequest):
    """单独调用差异分析"""
    db = SessionLocal()
    try:
        diff_summary, diff_detail = await analyze_diff(req.question, req.results, db=db)
        return {"code": 0, "data": {"diff_summary": diff_summary, "diff_detail": diff_detail}}
    finally:
        db.close()


@app.get("/api/history")
def get_history(limit: int = 20):
    """获取历史记录"""
    db = SessionLocal()
    try:
        records = db.query(CompareHistory).order_by(
            CompareHistory.created_at.desc()
        ).limit(limit).all()
        return {
            "code": 0,
            "data": [
                {
                    "id": r.id,
                    "question": r.question,
                    "models": r.models,
                    "diff_summary": r.diff_summary,
                    "created_at": r.created_at.isoformat() if r.created_at else "",
                }
                for r in records
            ]
        }
    finally:
        db.close()


@app.get("/api/history/export")
def export_all_history(format: str = Query("csv", alias="format")):
    """导出全部历史记录为 CSV"""
    if format.lower() != "csv":
        raise HTTPException(400, "目前仅支持 format=csv")
    db = SessionLocal()
    try:
        records = db.query(CompareHistory).order_by(CompareHistory.created_at.desc()).all()
        buf = io.StringIO()
        writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
        writer.writerow(["id", "问题", "模型列表", "差异汇总", "差异详情JSON", "时间"])
        for r in records:
            models_str = ",".join(r.models) if isinstance(r.models, list) else str(r.models or "")
            detail = _load_history_diff_detail(db, r.id)
            detail_s = json.dumps(detail, ensure_ascii=False) if detail else ""
            writer.writerow([
                r.id,
                r.question or "",
                models_str,
                r.diff_summary or "",
                detail_s,
                r.created_at.isoformat() if r.created_at else "",
            ])
        body = ("\ufeff" + buf.getvalue()).encode("utf-8")
        filename = _export_filename("csv")
        return Response(
            content=body,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": _content_disposition(filename)},
        )
    finally:
        db.close()


@app.get("/api/history/{history_id}")
def get_history_detail(history_id: int):
    """获取历史记录详情"""
    db = SessionLocal()
    try:
        record = db.query(CompareHistory).filter(CompareHistory.id == history_id).first()
        if not record:
            raise HTTPException(404, "记录不存在")
        results = strip_emoji_obj(record.results or [])
        detail = strip_emoji_obj(_load_history_diff_detail(db, record.id))
        return {
            "code": 0,
            "data": {
                "id": record.id,
                "question": record.question,
                "models": record.models,
                "results": results,
                "diff_summary": strip_emoji(record.diff_summary or ""),
                "diff_detail": detail,
                "created_at": record.created_at.isoformat() if record.created_at else "",
            }
        }
    finally:
        db.close()


@app.delete("/api/history/{history_id}")
def delete_history(history_id: int):
    """删除历史记录"""
    db = SessionLocal()
    try:
        record = db.query(CompareHistory).filter(CompareHistory.id == history_id).first()
        if record:
            db.delete(record)
            db.commit()
        return {"code": 0, "message": "已删除"}
    finally:
        db.close()


@app.get("/api/export/history/{history_id}")
def export_history(history_id: int, format: str = Query("md", alias="format")):
    db = SessionLocal()
    try:
        record = db.query(CompareHistory).filter(CompareHistory.id == history_id).first()
        if not record:
            raise HTTPException(404, "记录不存在")
        detail = _load_history_diff_detail(db, record.id)
        payload = build_export_payload(
            record.question, record.results or [], record.diff_summary or "", detail
        )
        return export_response(format, payload)
    finally:
        db.close()


@app.post("/api/export/current")
def export_current(req: ExportCurrentRequest, format: str = Query("md", alias="format")):
    payload = build_export_payload(
        req.question, req.results, req.diff_summary, req.diff_detail
    )
    return export_response(format, payload)


# ==================== 自定义模型管理 ====================

@app.post("/api/models/custom")
def add_custom_model(req: ModelAddRequest):
    """添加自定义模型"""
    db = SessionLocal()
    try:
        model = CustomModel(
            name=req.name,
            base_url=req.base_url,
            model=req.model,
            api_key=req.api_key,
        )
        db.add(model)
        db.commit()
        db.refresh(model)
        return {"code": 0, "data": {"id": model.id}}
    finally:
        db.close()


@app.put("/api/models/custom/{model_id}")
def update_custom_model(model_id: int, req: ModelAddRequest):
    """更新自定义模型"""
    db = SessionLocal()
    try:
        model = db.query(CustomModel).filter(CustomModel.id == model_id).first()
        if not model:
            raise HTTPException(404, "模型不存在")
        model.name = req.name
        model.base_url = req.base_url
        model.model = req.model
        model.api_key = req.api_key
        db.commit()
        return {"code": 0}
    finally:
        db.close()


@app.delete("/api/models/custom/{model_id}")
def delete_custom_model(model_id: int):
    """删除自定义模型"""
    db = SessionLocal()
    try:
        model = db.query(CustomModel).filter(CustomModel.id == model_id).first()
        if model:
            delete_override(db, str(model_id))
            db.delete(model)
            db.commit()
        return {"code": 0}
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT)
