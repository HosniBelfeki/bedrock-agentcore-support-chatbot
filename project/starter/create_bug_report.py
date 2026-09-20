import json
import os
import uuid
from datetime import datetime, timezone
import boto3

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

REQUIRED_FIELDS = ("description", "stepsToReproduce", "environment")

def lambda_handler(event, _):
    print("EVENT:", json.dumps(event, indent=2, default=str))

    description = (event.get("description") or "").strip()
    steps = (event.get("stepsToReproduce") or "").strip()
    environment = (event.get("environment") or "").strip()

    if not description:
        return {"error": "missing", "field": "description"}

    ticket_id = str(uuid.uuid4())
    item = {
        "ticketId": ticket_id,
        "description": description,
        "stepsToReproduce": steps,
        "environment": environment,
        "status": "OPEN",
        "createdAt": datetime.now(timezone.utc).isoformat(),
    }

    table.put_item(Item=item)

    return {"ticketId": ticket_id, "status": "OPEN"}