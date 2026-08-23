# Customer Support Chatbot - project/starter

> **Owner / author:** **Hosni Belfeki**
> **Course:** **Udacity "Future AWS Agent Engineer" nanodegree** (AWS AI & ML Scholars track)
> **Account:** AWS account `739892308444` - **Region:** `us-east-1` - **Model:** `us.amazon.nova-pro-v1:0`

This folder is the runnable starter of the **Customer Support Chatbot
with Amazon Bedrock AgentCore** project - the standard capstone-style
exercise for the **Udacity Future AWS Agent Engineer** nanodegree. It
builds an **AgentCore-managed Harness + AgentCore Gateway** chatbot
that routes customer messages to one of three branches - bug report,
FAQ answer, phone-line handoff.

## What the project demonstrates

- Routing customer messages into three deterministic categories
  (`BUG_REPORT`, `FAQ`, `OTHER`) using a single prompt with embedded
  FAQ.
- Calling a Lambda tool (`create_bug_report`) through the AgentCore
  Gateway when the customer reports a bug.
- Using **Amazon Nova Pro** as the chat model and the Bedrock
  Evaluations LLM-as-a-judge at the same time.
- Running an automated harness + Bedrock Evaluations in BYOI format
  (`Builtin.Correctness`) over a 13-test suite covering all rubric
  branches plus edge cases (prompt injection, vague greeting, account
  deletion, late package, payment methods, return policy).

## Run order

Use **`run_all_inprocess.py`** - it runs the whole pipeline inside one
Python process, so a short-lived STS triple stays valid throughout.

```powershell
cd project\starter

# 1. Paste the AWS STS credentials for account 739892308444 into the
#    four $env lines below.
$env:AWS_ACCESS_KEY_ID    = "ASIA..."
$env:AWS_SECRET_ACCESS_KEY= "..."
$env:AWS_SESSION_TOKEN    = "IQoJb3Jp..."
$env:AWS_DEFAULT_REGION   = "us-east-1"

# 2. Run the pipeline (one process; idle timeout-safe):
python run_all_inprocess.py

# 3. Check the eval result:
aws bedrock list-evaluation-jobs --region us-east-1 ^
  --query "jobSummaries[].[jobName,status,goalMetrics]" --output table
```

`run_all_inprocess.py` (one process, in order):

1. Probes the STS triple via `get-caller-identity`.
2. Re-attaches the broad IAM permission on the harness execution role
   (in case IAM propagation shifted during prior runs).
3. Fires four chat smoke messages - one per branch.
4. Runs the 13-test suite against the harness and writes
   `output_eval_dataset.jsonl`.
5. Deploys `bug-report-testing-stack` if missing, otherwise reads its
   outputs.
6. Uploads `output_eval_dataset.jsonl` to the eval bucket.
7. Submits a `Builtin.Correctness` Bedrock Evaluations job (BYOI,
   judge = `amazon.nova-pro-v1:0`).
8. Scans the DynamoDB tickets table to enumerate rows the chatbot
   filed.
9. Cleans up: harness -> gateway target -> gateway, S3 bucket,
   `bug-report-testing-stack`, `bug-report-tool-stack`.
10. Writes `chat_results.json` so a transcript can be screenshotted.

The `run_all.ps1` PowerShell wrapper orchestrates the same flow in a
discrete shell, recommended only if you have a token lifetime longer
than ~5 minutes.

## Files

| File | What it does |
|---|---|
| `system_prompt.txt` | Single prompt that classifies + routes + behaves. FAQ embedded verbatim. |
| `cloudformation-tool.yaml` | Lambda + DynamoDB + 3 IAM roles. |
| `cloudformation-testing.yaml` | S3 bucket + Bedrock Eval IAM role. |
| `create_bug_report.py` | Lambda runtime that writes tickets to DynamoDB. |
| `setup_gateway.py` | Idempotent. Reads CFN outputs, creates the Gateway + target. |
| `create_harness.py` | Idempotent. Creates the Harness with memory=disabled. |
| `chat.py` | Terminal REPL. Surfaces any `[tool call] create_bug_report(...)` line. |
| `harness_tests.template.json` | Schema template. |
| `harness_tests.json` | Real test suite (13 cases: 3 bug + 5 FAQ + 5 other). |
| `generate_eval_dataset.py` | Runs the harness once per test, emits BYOI JSONL. |
| `cleanup_agentcore.py` | Deletes harness -> target -> gateway. |
| `run_all_inprocess.py` | One-process driver. (NOT tracked in git.) |
| `EVALUATION_NOTES.md` | Rubric evidence + troubleshooting matrix. |
| `AUTHORS.md` | Attribution. |

`agentcore_config.json`, `run_all.ps1`, `output_eval_dataset.jsonl`,
`chat_results.json`, `run_all_inprocess.py`, `venv/`, `__pycache__/` are
all gitignored.

## Rubric mapping

| Rubric | Lives in |
|---|---|
| Classification + routing | `system_prompt.txt` + `chat.py` + 13-test suite |
| Bug-report path | `system_prompt.txt` Category A + `create_bug_report.py` + DynamoDB scan |
| FAQ + Other-request paths | `system_prompt.txt` Category B/C + FAQ embedded + FAQ tests |
| Testing + evaluation | `harness_tests.json` + `generate_eval_dataset.py` + `run_all_inprocess.py` step 7 |

See `EVALUATION_NOTES.md` for the detailed walk-through.

## Attribution

Authored by **Hosni Belfeki** as the capstone project submission for
the **Udacity Future AWS Agent Engineer** nanodegree. Architecture,
prompts, IAM permissions, and end-to-end driver are original work.
The starter files inherited from the local repo state pre-work and
the canonical AgentCore customer-support tutorial pattern are listed
in `AUTHORS.md`.
