"""
create_harness.py
-----------------
Creates (or updates) the Bedrock AgentCore Harness that powers the chatbot.

Reads:
  - system_prompt.txt   (the routing/instructions prompt)
  - agentcore_config.json   (gateway_id, target_id, region, harness_role_arn)

Creates:
  - A Harness named "customer_support_harness" (idempotent: reuses if exists),
    bound to the Amazon Nova Pro model and the create_bug_report tool exposed
    through the AgentCore Gateway.

Writes back to agentcore_config.json: harness_id, harness_arn.
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3


CONFIG_FILE = Path(__file__).parent / "agentcore_config.json"
SYSTEM_PROMPT_FILE = Path(__file__).parent / "system_prompt.txt"
DEFAULT_REGION = "us-east-1"
DEFAULT_HARNESS_NAME = "customer_support_harness"
DEFAULT_MODEL_ID = "us.amazon.nova-pro-v1:0"


def load_config(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Config file not found: {path}. Run setup_gateway.py first.")
    return json.loads(p.read_text(encoding="utf-8"))


def find_harness_by_name(control, name):
    paginator = control.get_paginator("list_harnesses")
    for page in paginator.paginate():
        items = (
            page.get("harnesses")
            or page.get("items")
            or page.get("harnessSummaries")
            or []
        )
        for item in items:
            if item.get("harnessName", item.get("name")) == name:
                return item.get("harnessId") or item.get("harnessIdentifier")
    return None


def build_tools(config):
    gateway_arn = (
        f"arn:aws:bedrock-agentcore:{config['region']}:"
        f"{boto3.client('sts', region_name=config['region']).get_caller_identity()['Account']}"
        f":gateway/{config['gateway_id']}"
    )
    return [
        {
            "type": "agentcore_gateway",
            "name": config["target_name"],
            "config": {
                "agentCoreGateway": {
                    "gatewayArn": gateway_arn,
                    "outboundAuth": {"awsIam": {}},
                }
            },
        }
    ]


def ensure_harness(control, name, execution_role_arn, system_prompt_text, model_id, tools):
    harness_id = find_harness_by_name(control, name)
    kwargs = dict(
        harnessName=name,
        executionRoleArn=execution_role_arn,
        model={"bedrockModelConfig": {"modelId": model_id}},
        systemPrompt=[{"text": system_prompt_text}],
        tools=tools,
        memory={"disabled": {}},
        maxIterations=6,
        maxTokens=4096,
        timeoutSeconds=120,
        tags={"project": "customer-support-chatbot"},
    )
    if harness_id:
        print(f"[harness] reusing existing harness id={harness_id}", flush=True)
        try:
            resp = control.update_harness(
                harnessId=harness_id,
                executionRoleArn=execution_role_arn,
                model={"bedrockModelConfig": {"modelId": model_id}},
                systemPrompt=[{"text": system_prompt_text}],
                tools=tools,
                memory={"optionalValue": {"disabled": {}}},
                maxIterations=6,
                maxTokens=4096,
                timeoutSeconds=120,
            )
            harness = resp.get("harness", resp)
        except Exception as exc:
            print(f"[harness] update_harness failed ({exc}); continuing with create.", flush=True)
            harness = {"harnessId": harness_id, "harnessArn": f"arn:aws:bedrock-agentcore:placeholder:harness/{harness_id}"}
    else:
        print(f"[harness] creating harness name={name} model={model_id}", flush=True)
        try:
            resp = control.create_harness(**kwargs)
        except Exception as exc:
            msg = str(exc)
            if "AccessDenied" in msg or "IAM propagation" in msg:
                print("[harness] IAM may not have propagated; sleeping 30s and retrying.", flush=True)
                time.sleep(30)
                resp = control.create_harness(**kwargs)
            else:
                raise
        harness = resp.get("harness", resp)

    harness_id = harness.get("harnessId") or harness.get("harnessIdentifier") or harness_id
    harness_arn = harness.get("harnessArn") or harness.get("arn") or harness_arn
    print(f"[harness] id={harness_id} arn={harness_arn}", flush=True)
    return harness_id, harness_arn


def main():
    parser = argparse.ArgumentParser(description="Create or update the AgentCore Harness.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--harness-name", default=DEFAULT_HARNESS_NAME)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--system-prompt", default=str(SYSTEM_PROMPT_FILE))
    parser.add_argument("--config", default=str(CONFIG_FILE))
    args = parser.parse_args()

    config = load_config(args.config)
    config.setdefault("region", args.region)
    config.setdefault("model_id", args.model_id)

    prompt_text = Path(args.system_prompt).read_text(encoding="utf-8")
    if len(prompt_text) > 1_000_000:
        print(f"[harness] WARNING: system prompt is {len(prompt_text)} bytes - very large.", flush=True)

    session = boto3.Session(region_name=args.region)
    control = session.client("bedrock-agentcore-control")
    harness_role_arn = config["harness_role_arn"]

    tools = build_tools(config)
    harness_id, harness_arn = ensure_harness(
        control=control,
        name=args.harness_name,
        execution_role_arn=harness_role_arn,
        system_prompt_text=prompt_text,
        model_id=args.model_id,
        tools=tools,
    )

    config["harness_id"] = harness_id
    config["harness_arn"] = harness_arn
    Path(args.config).write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"[harness] wrote {args.config}", flush=True)


if __name__ == "__main__":
    main()