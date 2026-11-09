"""Host-monotonic event recording and causal checks for experimental runners."""
import json
from pathlib import Path
import time


class EventLog:
    def __init__(self, path: Path, run_id: str, experiment_id="B0", repeat_id="baseline"):
        self.file = path.open("x", encoding="utf-8", buffering=1)
        self.context = dict(run_id=run_id, experiment_id=experiment_id, repeat_id=repeat_id)

    def record(self, event, *, session_id, epoch=0, **fields):
        row = self.context | dict(event=event, session_id=session_id, epoch=epoch,
                                  t_ns=time.monotonic_ns()) | fields
        self.file.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        return row

    def close(self):
        self.file.close()


def validate_causality(events):
    if not events:
        return ["empty event stream"]
    sessions, errors = {}, []
    for index, row in enumerate(events):
        key = (row["session_id"], row["epoch"])
        state = sessions.setdefault(key, dict(accepted=0, consumed=0, seq=0, end=0, t=-1, closed=False))
        try:
            t = row["t_ns"]
            if type(t) is not int or t < state["t"]:
                raise ValueError("events must use ordered host monotonic_ns")
            state["t"] = t
            event = row["event"]
            if event in ("pcm_accepted", "pcm_consumed"):
                field = "accepted" if event == "pcm_accepted" else "consumed"
                value = row[field + "_samples"]
                if type(value) is not int or value < state[field] or state["closed"]:
                    raise ValueError("invalid sample counter or closed epoch")
                if field == "consumed" and value > state["accepted"]:
                    raise ValueError("model consumed future audio")
                state[field] = value
            if event in ("source_commit", "caption_emit"):
                start, end = row["start_sample"], row["end_sample"]
                if state["closed"]:
                    raise ValueError("output after cancellation/finish")
                if not (type(start) is int and type(end) is int and 0 <= start < end <= state["accepted"]):
                    raise ValueError("output exceeds accepted audio")
                if event == "caption_emit":
                    if row["seq"] != state["seq"] + 1 or end < state["end"]:
                        raise ValueError("duplicate/out-of-order caption")
                    state["seq"], state["end"] = row["seq"], end
            if event in ("cancel", "finished"):
                state["closed"] = True
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(f"event {index}: {exc}")
    return errors
