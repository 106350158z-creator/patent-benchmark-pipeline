import argparse
import json
import os
from pathlib import Path
from typing import Any

from openai import OpenAI

from generate_analysis_json import extract_json_object


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not os.environ.get(key):
            os.environ[key] = value


def contains_cjk(text: str) -> bool:
    return any("\u4e00" <= char <= "\u9fff" for char in text)


def normalize_item(item: Any) -> tuple[str, str]:
    if isinstance(item, dict):
        original = str(item.get("original_text") or "").strip()
        translation = str(item.get("translation") or "").strip()
        source = original or translation
        if original and translation and not contains_cjk(original):
            return original, translation
        if source:
            return source, source
    source = str(item).strip()
    return source, source


def translate_items(items: list[Any], client: OpenAI, model: str, temperature: float) -> list[dict[str, str]]:
    normalized = [normalize_item(item) for item in items]
    to_translate = [
        {"index": index, "text": source, "direction": "zh_to_en" if contains_cjk(source) else "en_to_zh"}
        for index, (source, translation) in enumerate(normalized)
        if source and (source == translation or (contains_cjk(source) == contains_cjk(translation)))
    ]
    output = [{"original_text": source, "translation": translation} for source, translation in normalized]
    if not to_translate:
        return output

    prompt = {
        "task": "Return bilingual patent-analysis bullet points. If direction is zh_to_en, translate the text into concise English original_text and keep the Chinese text as translation. If direction is en_to_zh, keep the English text as original_text and translate it into concise Chinese translation.",
        "output_schema": {"items": [{"index": 0, "original_text": "English sentence", "translation": "Chinese sentence"}]},
        "items": to_translate,
    }
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": "Return strict JSON only. Do not add commentary."},
            {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
        ],
        response_format={"type": "json_object"},
        temperature=temperature,
    )
    content = response.choices[0].message.content or "{}"
    if not content.strip():
        return output
    data = extract_json_object(content)
    translated = data.get("items") or data.get("result") or data.get("translations") or []
    if not isinstance(translated, list):
        translated = []
    fallback_by_order = iter(to_translate)
    for item in translated:
        if not isinstance(item, dict):
            continue
        fallback = next(fallback_by_order, {})
        index = item.get("index", fallback.get("index"))
        if not isinstance(index, int) or index < 0 or index >= len(output):
            continue
        source = output[index]["original_text"]
        output[index] = {
            "original_text": str(item.get("original_text") or item.get("english") or source),
            "translation": str(item.get("translation") or item.get("chinese") or output[index]["translation"]),
        }
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate risk/action lists in an analysis JSON into bilingual objects.")
    parser.add_argument("analysis_json")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OHMYGPT_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--temperature", type=float, default=0.0)
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
    data["top_risk_reasons"] = translate_items(data.get("top_risk_reasons") or [], client, model, args.temperature)
    data["recommended_actions"] = translate_items(data.get("recommended_actions") or [], client, model, args.temperature)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Updated bilingual lists: {path}")


if __name__ == "__main__":
    main()
