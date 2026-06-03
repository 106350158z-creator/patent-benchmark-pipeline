import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def translate_items(items: list[Any], client: OpenAI, model: str) -> list[dict[str, str]]:
    normalized = [item.get("translation") if isinstance(item, dict) else str(item) for item in items]
    prompt = {
        "task": "Translate Chinese patent-analysis bullet points into concise English original text while preserving the Chinese as translation.",
        "output_schema": [{"original_text": "English sentence", "translation": "Chinese source sentence"}],
        "items": normalized,
    }
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return strict JSON only. Do not add commentary."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    data = json.loads(content)
    translated = data.get("items") or data.get("result") or data.get("translations") or []
    if not isinstance(translated, list):
        translated = []
    output: list[dict[str, str]] = []
    for index, source in enumerate(normalized):
        item = translated[index] if index < len(translated) and isinstance(translated[index], dict) else {}
        output.append(
            {
                "original_text": str(item.get("original_text") or item.get("english") or source),
                "translation": str(item.get("translation") or item.get("chinese") or source),
            }
        )
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate risk/action lists in an analysis JSON into bilingual objects.")
    parser.add_argument("analysis_json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OHMYGPT_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    args = parser.parse_args()

    load_dotenv(Path(args.env_file))
    api_key = os.environ.get(args.api_key_env, "")
    base_url = args.base_url or os.environ.get("OHMYGPT_API_BASE") or os.environ.get("OPENAI_API_BASE") or ""
    model = args.model or os.environ.get("OPENAI_MODEL") or "gpt-5.5"
    if not api_key:
        raise RuntimeError(f"Missing API key env var: {args.api_key_env}")

    path = Path(args.analysis_json)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    client = OpenAI(api_key=api_key, base_url=base_url)
    data["top_risk_reasons"] = translate_items(data.get("top_risk_reasons") or [], client, model)
    data["recommended_actions"] = translate_items(data.get("recommended_actions") or [], client, model)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Updated bilingual lists: {path}")


if __name__ == "__main__":
    main()
