"""
chat.py
-------
Terminal REPL for trying out the customer-support chatbot.

Reads agentcore_config.json, holds a single multi-turn conversation with the
Bedrock AgentCore Harness, and prints the assistant's streamed reply after
each user message. Tool calls are surfaced to the terminal as
   [tool call] create_bug_report(description=...)

Usage:
  python chat.py            # interactive REPL
  python chat.py --reset    # start a fresh conversation (new session id)
  python chat.py --say "Hi" # send a single message then exit
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import boto3


CONFIG_FILE = Path(__file__).parent / "agentcore_config.json"
DEFAULT_REGION = "us-east-1"


def load_config(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"Config file not found: {path}. Run setup_gateway.py and create_harness.py first.")
    cfg = json.loads(p.read_text(encoding="utf-8"))
    for key in ("harness_arn", "region"):
        if key not in cfg or not cfg[key]:
            raise SystemExit(f"agentcore_config.json missing required key '{key}'.")
    return cfg


def fresh_session_id():
    """AgentCore requires session id >= 33 chars - pad if too short."""
    return str(uuid.uuid4()).ljust(33, "0")


def handle_stream(stream):
    """Walk the stream events; print deltas and surface tool calls.

    Returns:
      assistant_text (str) - accumulated message text
      tool_calls (list[dict]) - any tool-use blocks that ran
      stop_reason (str|None)
    """
    text_buf = []
    tool_calls = []
    stop_reason = None

    for event in stream:
        et = type(event).__name__
        # Most shapes come as dicts; defensive get
        if et == "dict":
            inner = event
        else:
            inner = {et: event.__dict__}

        for key, value in inner.items():
            if key == "contentBlockDelta":
                delta = value.get("delta", {})
                if "text" in delta:
                    sys.stdout.write(delta["text"])
                    sys.stdout.flush()
                    text_buf.append(delta["text"])
                elif "toolUse" in delta:
                    tu = delta["toolUse"]
                    tool_calls.append({"name": tu.get("name"), "input": tu.get("input", {})})
                    print(f"\n[tool call] {tu.get('name')}({json.dumps(tu.get('input', {}))})", flush=True)
            elif key == "contentBlockStart":
                start = value.get("start", {})
                if "toolUse" in start:
                    tu = start["toolUse"]
                    tool_calls.append({"name": tu.get("name"), "input": tu.get("input", {})})
                    print(f"\n[tool call] {tu.get('name')}({json.dumps(tu.get('input', {}))})", flush=True)
            elif key == "messageStop":
                stop_reason = value.get("stopReason")
            elif key == "runtimeClientError":
                print(f"\n[error] {value.get('message')}", file=sys.stderr, flush=True)
            elif key == "metadata":
                usage = value.get("usage") or {}
                if usage:
                    print(f"\n[usage] {usage}", flush=True)
    return "".join(text_buf), tool_calls, stop_reason


def run_turn(data, harness_arn, session_id, user_text, model_id, region):
    prompt = [{"role": "user", "content": [{"text": user_text}]}]
    resp = data.invoke_harness(
        harnessArn=harness_arn,
        runtimeSessionId=session_id,
        messages=prompt,
        model={"bedrockModelConfig": {"modelId": model_id}} if model_id else None,
    )
    return handle_stream(resp["stream"])


def repl_loop(config):
    region = config["region"]
    harness_arn = config["harness_arn"]
    model_id = config.get("model_id")
    session_id = fresh_session_id()

    data = boto3.client("bedrock-agentcore", region_name=region)

    print(f"\n[chat] connected to harness {harness_arn}", flush=True)
    print(f"[chat] session id {session_id}", flush=True)
    print("[chat] type 'quit' or 'exit' to leave the chat. Type 'reset' to start a new session.\n", flush=True)

    while True:
        try:
            user_text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n[chat] bye.", flush=True)
            return

        if user_text.lower() in ("quit", "exit"):
            print("[chat] bye.", flush=True)
            return
        if user_text.lower() == "reset":
            session_id = fresh_session_id()
            print(f"[chat] new session id {session_id}\n", flush=True)
            continue
        if not user_text:
            continue

        print("aria> ", end="", flush=True)
        try:
            text, calls, stop = run_turn(data, harness_arn, session_id, user_text, model_id, region)
        except Exception as exc:
            print(f"\n[chat] invoke failed: {exc}", file=sys.stderr, flush=True)
            continue
        if not text and not calls:
            print("[chat] (no response)", flush=True)
        print()


def main():
    parser = argparse.ArgumentParser(description="Terminal REPL for the customer-support chatbot harness.")
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--config", default=str(CONFIG_FILE))
    parser.add_argument("--reset", action="store_true", help="Start with a fresh session id.")
    parser.add_argument("--say", help="Send a single message and print the response then exit.")
    args = parser.parse_args()

    config = load_config(args.config)
    config.setdefault("region", args.region)

    if args.say is not None:
        data = boto3.client("bedrock-agentcore", region_name=args.region)
        text, calls, stop = run_turn(
            data=data,
            harness_arn=config["harness_arn"],
            session_id=str(uuid.uuid4()).ljust(33, "0"),
            user_text=args.say,
            model_id=config.get("model_id"),
            region=args.region,
        )
        print()
        sys.exit(0)

    repl_loop(config)


if __name__ == "__main__":
    main()