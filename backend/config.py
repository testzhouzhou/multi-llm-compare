"""
多模型对比工具 - 后端配置
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# 服务器配置
HOST = "0.0.0.0"
PORT = 6364

# 数据库
DB_PATH = BASE_DIR / "data" / "compare.db"

# 差异分析用的 LLM（默认用千问，快速便宜）
DIFF_ANALYZER_MODEL = "qwen-plus"
DIFF_ANALYZER_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DIFF_ANALYZER_PROTOCOL = "openai"

# 请求超时（秒）
REQUEST_TIMEOUT = 120

# 预定义模型列表
PREDEFINED_MODELS = {
    "deepseek-v4-pro": {
        "id": "deepseek-v4-pro",
        "name": "DeepSeek V4 Pro",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-pro",
        "api_key_env": "DEEPSEEK_API_KEY",
        "enabled": True,
    },
    "qwen-plus": {
        "id": "qwen-plus",
        "name": "通义千问 Plus",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "api_key_env": "DASHSCOPE_API_KEY",
        "enabled": True,
    },
    "qwen-max": {
        "id": "qwen-max",
        "name": "通义千问 Max",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "api_key_env": "DASHSCOPE_API_KEY",
        "enabled": True,
    },
    "qwen-turbo": {
        "id": "qwen-turbo",
        "name": "通义千问 Turbo",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-turbo",
        "api_key_env": "DASHSCOPE_API_KEY",
        "enabled": True,
    },
    "doubao": {
        "id": "doubao",
        "name": "豆包",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "model": "doubao-1-5-pro-32k-250115",
        "api_key_env": "DOUBAO_API_KEY",
        "enabled": True,
    },
    "glm": {
        "id": "glm",
        "name": "智谱 GLM-4",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4",
        "api_key_env": "ZHIPU_API_KEY",
        "enabled": True,
    },
    "hunyuan": {
        "id": "hunyuan",
        "name": "腾讯元宝",
        "base_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "model": "hunyuan-standard",
        "api_key_env": "HUNYUAN_API_KEY",
        "enabled": True,
    },
}


def _read_dotenv_value(env_name: str) -> str:
    """从 backend/.env 读取指定变量（不含别名）"""
    if not env_name:
        return ""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return ""
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith(f"{env_name}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


# 仅在「同名 key 全空」时才启用的别名兜底
_ENV_ALIASES = {
    "DASHSCOPE_API_KEY": ("ALIBABA_API_KEY",),
    "DOUBAO_API_KEY": ("VOLCENGINE_API_KEY",),
}


def get_api_key(
    env_name: str = "",
    api_key: str | None = None,
    allow_env_alias: bool = True,
) -> str:
    """按优先级取 API Key。

    1. 显式传入的 api_key
    2. backend/.env 中的同名 api_key_env
    3. 进程环境变量中的同名 api_key_env
    4. 前三步都空且 allow_env_alias=True 时，别名兜底（ALIBABA/VOLCENGINE）
    5. 都空 → ""
    """
    if api_key:
        return api_key

    if env_name:
        key = _read_dotenv_value(env_name)
        if key:
            return key
        key = os.environ.get(env_name, "") or ""
        if key:
            return key

    if allow_env_alias and env_name:
        for alias in _ENV_ALIASES.get(env_name, ()):
            key = _read_dotenv_value(alias)
            if key:
                return key
            key = os.environ.get(alias, "") or ""
            if key:
                return key

    return ""
