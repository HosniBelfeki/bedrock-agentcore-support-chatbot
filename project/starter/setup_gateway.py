"""
setup_gateway.py
----------------
Creates (or reuses) an Amazon Bedrock AgentCore Gateway and registers the
Lambda tool created by the `bug-report-tool-stack` CloudFormation stack.

Reads stack outputs from CloudFormation - no copy-pasting needed.

What it does:
  1. Pulls LambdaFunctionArn, GatewayRoleArn, HarnessRoleArn from CFN outputs.
  2. Creates an AgentCore Gateway with authorizerType=AWS_IAM (so the
     caller's own IAM credentials are used to invoke the gateway).
  3. Creates a Gateway Target that exposes the Lambda as MCP tool
     `create_bug_report(description, stepsToReproduce, environment)`.
  4. Writes agentcore_config.json with the IDs that downstream scripts
     need.

Usage:
  python setup_gateway.py [--region us-east-1] [--tool-stack bug-report-tool-stack]

Idempotent: if a gateway with the same name exists in the same region under
the same account, the script reuses it and only creates the target if the
target is missing.
"""

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

import boto3


CONFIG_FILE = Path(__file__).parent / "agentcore_config.json"
DEFAULT_TOOL_STACK = "bug-report-tool-stack"
DEFAULT_REGION = "us-east-1"
DEFAULT_GATEWAY_NAME = "customer-support-gateway"
DEFAULT_TARGET_NAME = "createbugreporttool"


def get_stack_outputs(cfn_client, stack_name):
    resp = cfn_client.describe_stacks(StackName=stack_name)
    stacks = resp.get("Stacks") or []
    if not stacks:
        raise SystemExit(f"CloudFormation stack '{stack_name}' not found.")
    outputs = stacks[0].get("Outputs") or []
    by_key = {o["OutputKey"]: o["OutputValue"] for o in outputs}
    required = ("LambdaFunctionArn", "GatewayRoleArn", "HarnessRoleArn")
    missing = [k for k in required if k not in by_key]
    if missing:
        raise SystemExit(f"Stack '{stack_name}' missing outputs: {missing}")
    return by_key


def find_gateway_by_name(control, name):
    paginator = control.get_paginator("list_gateways")
    for page in paginator.paginate():
        for item in page.get("items", []) or page.get("gatewaySummaries", []) or []:
            if item.get("name") == name:
                return item.get("gatewayId") or item.get("gatewayIdentifier")
    return None


def find_target_by_name(control, gateway_id, name):
    paginator = control.get_paginator("list_gateway_targets")
    for page in paginator.paginate(gatewayIdentifier=gateway_id):
        for item in page.get("items", []) or page.get("targets", []) or []:
            if item.get("name") == name:
                return item.get("targetId") or item.get("gatewayTargetIdentifier")
    return None


def ensure_gateway(control, name, role_arn, region):
    gateway_id = find_gateway_by_name(control, name)
    if gateway_id:
        print(f"[gateway] reusing existing gateway id={gateway_id}", flush=True)
        meta = control.get_gateway(gatewayIdentifier=gateway_id).get("gateway", {})
        return gateway_id, meta.get("gatewayUrl") or meta.get("gatewayEndpoint")

    print(f"[gateway] creating gateway name={name} region={region}", flush=True)
    try:
        resp = control.create_gateway(
            name=name,
            roleArn=role_arn,
            protocolType="MCP",
            authorizerType="AWS_IAM",
            description="Customer support chatbot tool gateway - exposes create_bug_report Lambda as an MCP tool.",
            tags={"project": "customer-support-chatbot"},
        )
    except Exception as exc:
        msg = str(exc)
        if "IAM propagation" in msg or "AccessDenied" in msg:
            print("[gateway] IAM might not have propagated yet, sleeping 30s and retrying once...", flush=True)
            time.sleep(30)
            resp = control.create_gateway(
                name=name,
                roleArn=role_arn,
                protocolType="MCP",
                authorizerType="AWS_IAM",
                description="Customer support chatbot tool gateway - exposes create_bug_report Lambda as an MCP tool.",
                tags={"project": "customer-support-chatbot"},
            )
        else:
            raise
    gateway = resp.get("gateway", resp)
    gateway_id = gateway.get("gatewayId") or gateway.get("gatewayIdentifier")
    gateway_url = gateway.get("gatewayUrl") or gateway.get("gatewayEndpoint")
    print(f"[gateway] created gateway id={gateway_id} url={gateway_url}", flush=True)
    return gateway_id, gateway_url


def ensure_target(control, gateway_id, name, lambda_arn):
    target_id = find_target_by_name(control, gateway_id, name)
    if target_id:
        print(f"[target]  reusing existing target id={target_id}", flush=True)
        return target_id

    print(f"[target]  creating gateway target name={name} for lambda={lambda_arn}", flush=True)
    target_config = {
        "mcp": {
            "lambda": {
                "lambdaArn": lambda_arn,
                "toolSchema": {
                    "inlinePayload": [
                        {
                            "name": "create_bug_report",
                            "description": (
                                "Log a confirmed bug report from the customer. "
                                "Use ONLY after collecting description, stepsToReproduce, "
                                "and environment from the customer."
                            ),
                            "inputSchema": {
                                "type": "object",
                                "properties": {
                                    "description": {
                                        "type": "string",
                                        "description": "One-sentence summary of what is broken.",
                                    },
                                    "stepsToReproduce": {
                                        "type": "string",
                                        "description": "Numbered steps the engineer can run to reproduce the issue.",
                                    },
                                    "environment": {
                                        "type": "string",
                                        "description": "Browser + version, OS, device.",
                                    },
                                },
                                "required": ["description", "stepsToReproduce", "environment"],
                            },
                        }
                    ]
                },
            }
        }
    }
    creds = [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]

    target = None
    for attempt in range(3):
        try:
            resp = control.create_gateway_target(
                gatewayIdentifier=gateway_id,
                name=name,
                description="create_bug_report Lambda exposed as MCP tool.",
                targetConfiguration=target_config,
                credentialProviderConfigurations=creds,
            )
            target = resp.get("gatewayTarget", resp)
            break
        except Exception as exc:
            print(f"[target]  attempt {attempt+1} failed: {exc}", flush=True)
            time.sleep(15)
    if target is None:
        raise SystemExit("[target]  failed after 3 attempts")
    target_id = target.get("targetId") or target.get("gatewayTargetIdentifier")
    print(f"[target]  created target id={target_id}", flush=True)
    return target_id


def main():
    parser = argparse.ArgumentParser(description="Create the AgentCore Gateway for the support chatbot.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--tool-stack", default=DEFAULT_TOOL_STACK, help="CloudFormation stack that created the Lambda.")
    parser.add_argument("--gateway-name", default=DEFAULT_GATEWAY_NAME)
    parser.add_argument("--target-name", default=DEFAULT_TARGET_NAME)
    parser.add_argument("--config-out", default=str(CONFIG_FILE))
    args = parser.parse_args()

    print(f"[setup] region={args.region} tool-stack={args.tool_stack}", flush=True)

    session = boto3.Session(region_name=args.region)
    cf = session.client("cloudformation")
    control = session.client("bedrock-agentcore-control")

    outputs = get_stack_outputs(cf, args.tool_stack)
    lambda_arn = outputs["LambdaFunctionArn"]
    gateway_role_arn = outputs["GatewayRoleArn"]
    harness_role_arn = outputs["HarnessRoleArn"]

    gateway_id, gateway_url = ensure_gateway(control, args.gateway_name, gateway_role_arn, args.region)
    time.sleep(10)
    target_id = ensure_target(control, gateway_id, args.target_name, lambda_arn)

    config = {
        "region": args.region,
        "tool_stack": args.tool_stack,
        "harness_role_arn": harness_role_arn,
        "gateway_id": gateway_id,
        "gateway_url": gateway_url,
        "target_name": args.target_name,
        "target_id": target_id,
        "lambda_function_arn": lambda_arn,
        "tool_name": "create_bug_report",
        "model_id": "us.amazon.nova-pro-v1:0",
        "harness_role_arn": harness_role_arn,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    out = Path(args.config_out)
    out.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    print(f"[setup] wrote {out}", flush=True)
    print(json.dumps(config, indent=2), flush=True)


if __name__ == "__main__":
    main()