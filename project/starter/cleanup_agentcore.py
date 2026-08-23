"""
cleanup_agentcore.py
--------------------
Deletes AgentCore resources created by setup_gateway.py and create_harness.py,
in the order: harness -> gateway target -> gateway.

Reads agentcore_config.json. Add --yes to skip the type-to-confirm prompt.

Usage:
  python cleanup_agentcore.py            # asks for confirmation
  python cleanup_agentcore.py --yes      # skip prompt
  python cleanup_agentcore.py --config agentcore_config.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import boto3


CONFIG_FILE = Path(__file__).parent / "agentcore_config.json"
DEFAULT_REGION = "us-east-1"


def confirm(prompt):
    sys.stdout.write(prompt + " [type YES to continue]: ")
    sys.stdout.flush()
    return sys.stdin.readline().strip() == "YES"


def main():
    parser = argparse.ArgumentParser(description="Delete the AgentCore harness and gateway.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()

    cfg_path = Path(args.config)
    if not cfg_path.exists():
        raise SystemExit(f"Config file not found: {args.config}. Nothing to delete.")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    region = cfg.get("region", args.region)
    session = boto3.Session(region_name=region)
    control = session.client("bedrock-agentcore-control")

    resources = []
    if cfg.get("harness_id"):
        resources.append(("harness", cfg["harness_id"]))
    if cfg.get("target_id") and cfg.get("gateway_id"):
        resources.append(("gateway target", f"{cfg['gateway_id']} / {cfg['target_id']}"))
    if cfg.get("gateway_id"):
        resources.append(("gateway", cfg["gateway_id"]))

    if not resources:
        print("[cleanup] nothing to do - config has no AgentCore ids.", flush=True)
        return

    print("[cleanup] about to delete in order:")
    for label, ident in resources:
        print(f"  - {label}: {ident}")

    if not args.yes:
        ok = confirm("Proceed")
        if not ok:
            print("[cleanup] aborted.", flush=True)
            return

    if cfg.get("harness_id"):
        print(f"[cleanup] deleting harness {cfg['harness_id']}", flush=True)
        for attempt in range(3):
            try:
                control.delete_harness(harnessIdentifier=cfg["harness_id"])
                break
            except Exception as exc:
                print(f"[cleanup] attempt {attempt+1}: {exc}", flush=True)
                time.sleep(15)
        cfg.pop("harness_id", None)
        cfg.pop("harness_arn", None)

    if cfg.get("target_id") and cfg.get("gateway_id"):
        print(f"[cleanup] deleting gateway target {cfg['target_id']} on gateway {cfg['gateway_id']}", flush=True)
        for attempt in range(3):
            try:
                control.delete_gateway_target(
                    gatewayIdentifier=cfg["gateway_id"],
                    gatewayTargetIdentifier=cfg["target_id"],
                )
                break
            except Exception as exc:
                print(f"[cleanup] attempt {attempt+1}: {exc}", flush=True)
                time.sleep(15)
        cfg.pop("target_id", None)

    if cfg.get("gateway_id"):
        print(f"[cleanup] deleting gateway {cfg['gateway_id']}", flush=True)
        for attempt in range(3):
            try:
                control.delete_gateway(gatewayIdentifier=cfg["gateway_id"])
                break
            except Exception as exc:
                print(f"[cleanup] attempt {attempt+1}: {exc}", flush=True)
                time.sleep(15)
        cfg.pop("gateway_id", None)
        cfg.pop("gateway_url", None)

    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"[cleanup] updated {cfg_path}", flush=True)


if __name__ == "__main__":
    main()