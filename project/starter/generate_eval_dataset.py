"""
generate_eval_dataset.py
------------------------
Runs the Bedrock AgentCore customer-support Harness against the test suite in
harness_tests.json and writes the results as a JSONL file in the Bedrock
Evaluations BYOI format.

Output schema (one JSON object per line):

  {
    "prompt": "<the customer message>",
    "referenceResponse": "<description of expected response from the test>",
    "modelResponses": [
      {
        "response": "<the harness's final response text>",
        "modelIdentifier": "us.amazon.nova-pro-v1:0"
      }
    ]
  }

Usage:
  python generate_eval_dataset.py --tests harness_tests.json
  python generate_eval_dataset.py --tests harness_tests.json --out output_eval_dataset.jsonl
"""

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

import boto3


def sanitize_model_identifier(model_id):
    """Bedrock Evaluations modelIdentifier must match [a-zA-Z0-9]([a-zA-Z0-9._-]){0,255} - no colons."""
    return re.sub(r"[^a-zA-Z0-9._-]", "-", model_id)


CONFIG_FILE = Path(__file__).parent / "agentcore_config.json"
DEFAULT_TESTS = Path(__file__).parent / "harness_tests.json"
DEFAULT_OUT = Path(__file__).parent / "output_eval_dataset.jsonl"
DEFAULT_REGION = "us-east-1"


def load_config(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"agentcore_config.json not found at {path} - run setup_gateway.py and create_harness.py first.")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for key in ("harness_arn",):
        if not cfg.get(key):
            raise SystemExit(f"agentcore_config.json missing '{key}'")
    return cfg


def collect_response_text(stream):
    """Consume the AgentCore event stream; accumulate final text + tool call log."""
    text = []
    tool_calls = []
    errors = []

    for event in stream:
        if "contentBlockDelta" in event:
            delta = event["contentBlockDelta"].get("delta") or {}
            if "text" in delta:
                text.append(delta["text"])
            if "toolUse" in delta:
                tu = delta["toolUse"]
                tool_calls.append({"name": tu.get("name"), "input": tu.get("input", {})})
        if "contentBlockStart" in event:
            start = event["contentBlockStart"].get("start") or {}
            if "toolUse" in start:
                tu = start["toolUse"]
                tool_calls.append({"name": tu.get("name"), "input": tu.get("input", {})})
        if "runtimeClientError" in event:
            errors.append(event["runtimeClientError"].get("message") or "client error")

    return "".join(text), tool_calls, errors


def invoke_once(data, harness_arn, model_id, prompt_text):
    """Open a fresh session, invoke the harness once, return the final text."""
    session_id = str(uuid.uuid4()).ljust(33, "0")
    response = data.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=[{"role": "user", "content": [{"text": prompt_text}]}],
        model={"bedrockModelConfig": {"modelId": model_id}} if model_id else None,
    )
    text, tool_calls, errors = collect_response_text(response["stream"])
    if errors:
        return f"[ERROR] {'; '.join(errors)}"
    if tool_calls:
        suffix = "\n\n" + "\n".join(f"[tool call] {tc['name']}({json.dumps(tc['input'])})" for tc in tool_calls)
        return text + suffix
    return text


def main():
    parser = argparse.ArgumentParser(description="Generate a Bedrock Evaluations BYOI JSONL from the harness test suite.")
    parser.add_argument("--tests", default=str(DEFAULT_TESTS))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--config", default=str(CONFIG_FILE))
    args = parser.parse_args()

    tests_path = Path(args.tests)
    cfg_path = Path(args.config)
    out_path = Path(args.out)

    cfg = load_config(cfg_path)
    suite = json.loads(tests_path.read_text(encoding="utf-8"))
    tests = suite.get("tests") or []
    if not tests:
        raise SystemExit(f"No tests found in {tests_path}")

    session = boto3.Session(region_name=args.region)
    data = session.client("bedrock-agentcore")

    model_id = cfg.get("model_id") or "us.amazon.nova-pro-v1:0"
    harness_arn = cfg["harness_arn"]

    print(f"[eval] harness={harness_arn}", flush=True)
    print(f"[eval] model={model_id}", flush=True)
    print(f"[eval] {len(tests)} tests, output -> {out_path}", flush=True)

    n_ok = 0
    with out_path.open("w", encoding="utf-8") as f:
        for t in tests:
            tid = t.get("id") or f"test_{uuid.uuid4().hex[:8]}"
            expected = t.get("expected", "")
            prompt = t.get("prompt", "")
            try:
                response_text = invoke_once(data, harness_arn, model_id, prompt)
                n_ok += 1
            except Exception as exc:
                response_text = f"[FLOW_ERROR] {type(exc).__name__}: {exc}"

            record = {
                "prompt": prompt,
                "referenceResponse": expected,
                "modelResponses": [
                    {"response": response_text, "modelIdentifier": sanitize_model_identifier(model_id)}
                ],
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(f"[eval] {tid}: {'OK' if not response_text.startswith('[') else 'ERR'} wrote line", flush=True)

    print(f"[eval] wrote {len(tests)} lines to {out_path} ({n_ok} invocations succeeded)", flush=True)


if __name__ == "__main__":
    main()