import argparse
import base64
import json
import os
import re
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
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not os.environ.get(key):
            os.environ[key] = value


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_api(args: argparse.Namespace) -> tuple[str, str, str]:
    load_dotenv(Path(args.env_file))
    api_key = os.environ.get(args.api_key_env, "")
    base_url = args.base_url or os.environ.get("OHMYGPT_API_BASE") or os.environ.get("OPENAI_API_BASE") or ""
    model = args.model or os.environ.get("OPENAI_MODEL") or "gpt-5.5"
    if not api_key:
        raise RuntimeError(f"Missing API key env var: {args.api_key_env}")
    return api_key, base_url, model


def case_dir_from_benchmark(path: Path, data: dict[str, Any]) -> Path:
    trace = data.get("source_trace") or {}
    case_dir = trace.get("case_dir")
    if case_dir:
        candidate = Path(case_dir)
        if candidate.exists():
            return candidate
    return path.parent


def strip_drawing_pages(text: str) -> str:
    return re.split(r"\s*---\s*PAGE\s+\d+\s*---\s*[^-]*\bDRAWING\b", text, maxsplit=1, flags=re.I)[0]


def normalize_claim_ocr(text: str) -> str:
    text = strip_drawing_pages(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\b(?:5|10|15|20|25|30|35|40)\s+(?=[A-Za-z(])", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:12000]


def fallback_claim_1(raw_text: str) -> str:
    text = normalize_claim_ocr(raw_text)
    match = re.search(r"(?is)(?:claim\s*)?1[\.)]?\s*(.*?)(?=\s+2\s*[\.)]\s*[A-Z]|\s+Claim\s+2\b|$)", text)
    claim = match.group(1).strip() if match else text
    claim = re.sub(r"\s+", " ", claim)
    return claim[:2500]


def refine_claim(client: OpenAI, model: str, raw_claim: str, source: str, temperature: float) -> dict[str, Any]:
    raw = normalize_claim_ocr(raw_claim)
    if not raw:
        return {"claim_number": 1, "text": "", "source": source, "extraction_note": "No raw claim text."}

    prompt = {
        "task": "Extract exactly one complete Claim 1 from noisy OCR patent claim text.",
        "requirements": [
            "Return strict JSON only.",
            "Keep the original English claim language.",
            "Return only Claim 1, not claims 2 or later.",
            "Remove OCR page markers, drawing text, line numbers, and accidental spaces inside words.",
            "Do not translate and do not summarize.",
            "If Claim 1 is a single sentence, keep it as one complete sentence.",
        ],
        "output_schema": {
            "claim_number": 1,
            "text": "complete cleaned Claim 1 text",
            "source": source,
            "extraction_note": "short note",
        },
        "raw_ocr": raw,
    }
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return strict JSON only. You are cleaning OCR patent claim text."},
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        text = str(data.get("text") or "").strip()
        if text:
            return {
                "claim_number": 1,
                "text": text,
                "source": str(data.get("source") or source),
                "extraction_note": str(data.get("extraction_note") or "Cleaned by LLM from OCR claim text."),
            }
    except Exception as exc:
        return {
            "claim_number": 1,
            "text": fallback_claim_1(raw),
            "source": source,
            "extraction_note": f"Fallback regex extraction after LLM failure: {exc}",
        }
    return {
        "claim_number": 1,
        "text": fallback_claim_1(raw),
        "source": source,
        "extraction_note": "Fallback regex extraction because LLM returned no claim text.",
    }


def image_data_uri(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def select_markush_image(
    client: OpenAI,
    model: str,
    benchmark_path: Path,
    candidates: list[dict[str, Any]],
    claim_text: str,
    max_images: int,
    temperature: float,
) -> dict[str, Any] | None:
    usable = [item for item in candidates if isinstance(item, dict) and item.get("image_path")]
    usable = usable[:max_images]
    if not usable:
        return None

    content: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                "Select exactly one best Markush / chemical formula image from the candidates. "
                "Prefer a clean chemical structure or Markush scaffold over text-only snippets, page screenshots, plots, or tables. "
                "For this case, the claim context is:\n"
                f"{claim_text[:1600]}\n\n"
                "Return strict JSON: {\"selected_id\":\"M01\", \"reason\":\"short reason\"}."
            ),
        }
    ]
    id_to_candidate: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(usable, start=1):
        image_id = f"M{index:02d}"
        id_to_candidate[image_id] = item
        image_path = benchmark_path.parent / str(item["image_path"])
        content.append(
            {
                "type": "text",
                "text": (
                    f"{image_id}: path={item.get('image_path')}; "
                    f"score={item.get('score')}; page={item.get('page')}; source={item.get('pdf') or item.get('source')}"
                ),
            }
        )
        if image_path.exists():
            content.append({"type": "image_url", "image_url": {"url": image_data_uri(image_path), "detail": "low"}})

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Return strict JSON only. You choose the best chemical Markush/formula image."},
                {"role": "user", "content": content},
            ],
            response_format={"type": "json_object"},
            temperature=temperature,
        )
        data = json.loads(response.choices[0].message.content or "{}")
        selected_id = str(data.get("selected_id") or "").strip().upper()
        selected = id_to_candidate.get(selected_id)
        if selected:
            result = dict(selected)
            result["llm_selected"] = True
            result["selection_id"] = selected_id
            result["selection_reason"] = str(data.get("reason") or "")
            return result
    except Exception as exc:
        fallback = dict(usable[0])
        fallback["llm_selected"] = False
        fallback["selection_reason"] = f"Fallback to top-ranked candidate after LLM vision failure: {exc}"
        return fallback

    fallback = dict(usable[0])
    fallback["llm_selected"] = False
    fallback["selection_reason"] = "Fallback to top-ranked candidate because LLM returned no valid selected_id."
    return fallback


def main() -> None:
    parser = argparse.ArgumentParser(description="Use LLM to keep one cleaned claim and one selected Markush image.")
    parser.add_argument("benchmark_input")
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--api-key-env", default="OHMYGPT_API_KEY")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--max-images", type=int, default=24)
    parser.add_argument("--temperature", type=float, default=0.0)
    args = parser.parse_args()

    api_key, base_url, model = resolve_api(args)
    client = OpenAI(api_key=api_key, base_url=base_url)

    path = Path(args.benchmark_input)
    data = load_json(path)
    bench_input = data.get("benchmark_input") or {}
    claim = bench_input.get("claim_text") or {}
    structure = bench_input.get("drug_structure") or {}

    raw_claim = str(claim.get("raw_extracted_text") or claim.get("claim_1") or "")
    source = str(claim.get("source") or "")
    cleaned = refine_claim(client, model, raw_claim, source, args.temperature)
    claim["raw_extracted_text"] = raw_claim
    claim["claim_1"] = cleaned["text"]
    claim["target_claims"] = [{"claim_number": 1, "text": cleaned["text"]}] if cleaned["text"] else []
    claim["llm_claim_extraction"] = cleaned
    bench_input["claim_text"] = claim

    candidates = structure.get("markush_candidate_images") or structure.get("markush_images") or []
    selected = select_markush_image(client, model, path, candidates, cleaned["text"], args.max_images, args.temperature)
    if selected:
        structure["markush_images"] = [selected]
    else:
        structure["markush_images"] = []
    structure["markush_selection_note"] = "LLM selected one main Markush/formula image from cropped candidates."
    bench_input["drug_structure"] = structure

    data["benchmark_input"] = bench_input
    write_json(path, data)
    print(f"Refined claim and selected {len(structure.get('markush_images') or [])} Markush image(s): {path}")


if __name__ == "__main__":
    main()
