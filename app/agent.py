# app/agent.py
import psycopg
from psycopg.types.json import Jsonb

from app.config import settings


# --- Dummy Tool ---
def add_numbers(a: int, b: int) -> dict:
    return {"result": a + b}


TOOLS = {"add_numbers": add_numbers}


# --- Fake LLM ---
class FakeModel:
    def __init__(self):
        self.call_count = 0

    def decide(self, state: dict) -> dict:
        """Returns a hardcoded decision."""
        self.call_count += 1
        if self.call_count == 1:
            # First step: decide to use a tool
            return {"action": "tool_call", "tool_name": "add_numbers", "args": {"a": 5, "b": 7}}
        else:
            # Second step: finish the run
            return {"action": "finish", "result": {"message": "Calculation complete!"}}


# --- The Core Loop ---
def run_agent_loop(run_id: str, initial_payload: dict):
    """
    The synchronous agent loop.
    """
    model = FakeModel()
    step_number = 0

    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            # 1. Mark run as running
            cur.execute(
                "UPDATE agent_runs SET status = 'running', updated_at = NOW() WHERE id = %s",
                (run_id,),
            )

            while True:
                step_number += 1
                state = {"history": []}  # Simplified state for Phase 1

                # 2. DECIDE
                decision = model.decide(state)

                # Insert the step (Wrap decision in Jsonb)
                cur.execute(
                    """INSERT INTO run_steps (run_id, step_number, state, decision) 
                       VALUES (%s, %s, %s, %s) RETURNING id""",
                    (run_id, step_number, "deciding", Jsonb(decision)),
                )
                step_id = cur.fetchone()[0]

                # 3. ACT
                if decision["action"] == "tool_call":
                    tool_name = decision["tool_name"]
                    args = decision["args"]

                    # Execute tool
                    tool_func = TOOLS.get(tool_name)
                    if tool_func:
                        tool_result = tool_func(**args)
                    else:
                        tool_result = {"error": "Unknown tool"}

                    # Record tool call (Wrap args and tool_result in Jsonb)
                    cur.execute(
                        """INSERT INTO tool_calls (run_id, step_id, tool_name, args, result) 
                           VALUES (%s, %s, %s, %s, %s)""",
                        (run_id, step_id, tool_name, Jsonb(args), Jsonb(tool_result)),
                    )

                    # Update step state
                    cur.execute("UPDATE run_steps SET state = 'executed' WHERE id = %s", (step_id,))

                elif decision["action"] == "finish":
                    # Mark run as completed (Wrap result in Jsonb)
                    cur.execute(
                        """UPDATE agent_runs 
                           SET status = 'completed', result = %s, updated_at = NOW() 
                           WHERE id = %s""",
                        (Jsonb(decision.get("result")), run_id),
                    )
                    break  # Exit the loop

            conn.commit()
