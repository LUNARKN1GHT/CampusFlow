"""D013 视觉识别适配器评测脚本：智谱 glm-4v-flash × B011 真实样本。

用法（仓库根目录，密钥读取 backend/.env）：
    python scripts/evaluate_vision_adapter.py

对 samples/synthetic/b011/ 的 4 张样本调用视觉模型，提取结构化信息，
与 expected.json 的人工预期对比，把结果写入 docs/evaluation/d013-results.json，
由人工整理进 docs/evaluation/d013-vision-adapter.md。

本脚本只记录模型输出与对比结果，不把模型输出当作事实（AGENTS 规则）。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DOCS = [
    ROOT / "samples" / "synthetic" / "b011" / "expected.json",
    ROOT / "samples" / "synthetic" / "d013" / "expected.json",
]
RESULTS_PATH = ROOT / "docs" / "evaluation" / "d013-results.json"
API_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
MODEL = "glm-4v-flash"

PROMPT = (
    "请阅读这张课程通知图片，提取以下信息并以 JSON 返回：\n"
    "1. title：文档标题（无则 null）\n"
    "2. task：作业/实验名称（无则 null）\n"
    "3. deadline：截止时间原文（无则 null）\n"
    "4. section：教学班/班级代码（无则 null）\n"
    "5. declared_page_count：图片中声明的总页数（无则 null）\n"
    "6. unreadable_parts：无法辨认、被模糊处理或缺失的部分说明（无则 null）\n"
    "只提取图片中真实可见的文字，不要推测补全看不清的内容。"
)


def load_api_key() -> str:
    env_file = ROOT / "backend" / ".env"
    key = os.environ.get("ZHIPU_API_KEY")
    if key:
        return key
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith("ZHIPU_API_KEY="):
                return line.split("=", 1)[1].strip()
    raise SystemExit("未找到 ZHIPU_API_KEY（backend/.env 或环境变量）")


def call_vision_api(api_key: str, image_path: Path) -> dict:
    image_b64 = base64.b64encode(image_path.read_bytes()).decode()
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            }
        ],
    }
    request = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def evaluate_sample(api_key: str, sample: dict) -> dict:
    image_path = ROOT / sample["file"]
    raw = call_vision_api(api_key, image_path)
    message = raw["choices"][0]["message"]["content"]
    usage = raw.get("usage", {})

    extracted = None
    try:
        text = message.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        extracted = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        extracted = {"_unparsed_output": message[:500]}

    expected = sample.get("expected_facts", {})
    fact_checks = {}
    haystack = json.dumps(extracted, ensure_ascii=False)
    for field, expected_value in expected.items():
        expected_text = str(expected_value)
        # 日期匹配放宽到日期部分；其余按子串匹配
        candidates = {expected_text}
        if "T" in expected_text:
            candidates.add(expected_text.split("T")[0])
        fact_checks[field] = any(candidate in haystack for candidate in candidates)

    return {
        "sample_id": sample["sample_id"],
        "expected_state": sample["expected_processing_state"],
        "extracted": extracted,
        "fact_checks": fact_checks,
        "facts_hit": sum(fact_checks.values()),
        "facts_total": len(fact_checks),
        "token_usage": usage,
    }


def main() -> None:
    api_key = load_api_key()
    results = {
        "model": MODEL,
        "note": "模型输出仅用于评测记录，不作为事实（AGENTS 规则）",
        "samples": [],
    }
    for doc_path in SAMPLE_DOCS:
        expected_doc = json.loads(doc_path.read_text(encoding="utf-8"))
        for sample in expected_doc["samples"]:
            print(f"评测 {sample['sample_id']} ...", flush=True)
            try:
                results["samples"].append(evaluate_sample(api_key, sample))
            except Exception as exc:
                results["samples"].append({"sample_id": sample["sample_id"], "error": str(exc)})
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"结果已写入 {RESULTS_PATH}")


if __name__ == "__main__":
    sys.exit(main())
