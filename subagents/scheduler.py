#!/usr/bin/env python3
"""Concurrent scheduler for Trading_assess subagents."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from agent_runner import AgentRunError, run_agent
from repair import repair_agent_payload
from state_store import StateStore
from validator import ValidationError, validate_agent_output, validate_final_payloads, validate_plan

AGENTS = ("macro", "bear", "technical")

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": "1.0",
    "scheduler": {
        "max_concurrency": 3,
        "timeout_seconds": 180,
        "retries": 2,
        "backoff_seconds": 2,
        "max_repair_attempts": 2,
        "failure_policy": "fail_fast",
        "run_root": "runs",
        "state_db": "runs/state.db",
        "summary_name": "summary.md",
        "report_name": "report.md",
    },
    "provider": {
        "mode": "mock",
        "mock_data_paths": {
            "macro": "examples/macro_industry.json",
            "bear": "examples/bear_case.json",
            "technical": "examples/technical.json",
        },
    },
    "agents": {
        "macro": {"mode": "mock"},
        "bear": {"mode": "mock"},
        "technical": {"mode": "mock"},
    },
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = deepcopy(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_path(path_str: str, subagents_dir: Path) -> Path:
    path = Path(path_str)
    return path if path.is_absolute() else (subagents_dir / path)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return _sha256_bytes(raw)


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON root must be object: {path}")
    return payload


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_runtime_config(config_path: Path) -> Dict[str, Any]:
    if config_path.exists():
        user = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
        if not isinstance(user, dict):
            raise ValueError(f"runtime config must be mapping: {config_path}")
        return _deep_merge(DEFAULT_CONFIG, user)
    return deepcopy(DEFAULT_CONFIG)


def _load_orchestrator(subagents_dir: Path):
    import importlib.util

    script = subagents_dir / "orchestrate_report.py"
    spec = importlib.util.spec_from_file_location("orchestrate_report", script)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load orchestrator: {script}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _append_event_jsonl(path: Path, event: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


async def _run_single_agent(
    agent: str,
    run_id: str,
    plan_path: Path,
    run_dir: Path,
    subagents_dir: Path,
    config: Dict[str, Any],
    store: StateStore,
    sem: asyncio.Semaphore,
    resume: bool,
    event_log_path: Path,
) -> Tuple[Dict[str, Any], Path]:
    cfg = config["scheduler"]
    timeout_seconds = int(cfg["timeout_seconds"])
    retries = int(cfg["retries"])
    backoff = int(cfg["backoff_seconds"])
    max_repair = int(cfg["max_repair_attempts"])

    output_dir = run_dir / agent
    canonical_output = output_dir / "output.json"
    input_hash = _sha256_file(plan_path)

    if resume and canonical_output.exists():
        cached = _load_json(canonical_output)
        validate_agent_output(agent, cached)
        store.log_event(run_id, "INFO", "task.resume_hit", {"agent": agent, "path": str(canonical_output)})
        _append_event_jsonl(
            event_log_path,
            {"ts": _now(), "run_id": run_id, "agent": agent, "event": "resume_hit", "path": str(canonical_output)},
        )
        return cached, canonical_output

    for attempt in range(1, retries + 2):
        task_id = store.start_task(run_id, agent, attempt, input_hash)
        started = time.perf_counter()
        attempt_output = output_dir / f"attempt_{attempt}.json"

        payload: Dict[str, Any] = {}
        metadata: Dict[str, Any] = {}
        try:
            async with sem:
                payload, metadata = await asyncio.to_thread(
                    run_agent,
                    agent,
                    plan_path,
                    attempt_output,
                    run_dir,
                    subagents_dir,
                    config,
                    timeout_seconds,
                )

            validate_agent_output(agent, payload)
            _write_json(canonical_output, payload)
            output_hash = _sha256_json(payload)
            latency = int((time.perf_counter() - started) * 1000)
            store.finish_task(
                task_id,
                status="SUCCEEDED",
                latency_ms=latency,
                output_hash=output_hash,
                output_path=str(canonical_output),
            )
            store.record_artifact(run_id, "agent_output", str(canonical_output), output_hash, agent_name=agent)
            store.log_event(run_id, "INFO", "task.succeeded", {"agent": agent, "attempt": attempt, "meta": metadata}, task_id)
            _append_event_jsonl(
                event_log_path,
                {"ts": _now(), "run_id": run_id, "agent": agent, "event": "succeeded", "attempt": attempt},
            )
            return payload, canonical_output

        except (ValidationError, AgentRunError, TimeoutError, Exception) as exc:
            err_text = str(exc)
            repaired = False

            for repair_idx in range(1, max_repair + 1):
                try:
                    repaired_payload = repair_agent_payload(agent, payload, err_text)
                    validate_agent_output(agent, repaired_payload)
                    repaired_path = output_dir / f"attempt_{attempt}.repaired_{repair_idx}.json"
                    _write_json(repaired_path, repaired_payload)
                    _write_json(canonical_output, repaired_payload)
                    output_hash = _sha256_json(repaired_payload)
                    latency = int((time.perf_counter() - started) * 1000)
                    store.finish_task(
                        task_id,
                        status="SUCCEEDED",
                        latency_ms=latency,
                        output_hash=output_hash,
                        output_path=str(canonical_output),
                    )
                    store.record_artifact(run_id, "agent_output", str(canonical_output), output_hash, agent_name=agent)
                    store.log_event(
                        run_id,
                        "WARN",
                        "task.repaired",
                        {"agent": agent, "attempt": attempt, "repair_attempt": repair_idx, "reason": err_text},
                        task_id,
                    )
                    _append_event_jsonl(
                        event_log_path,
                        {
                            "ts": _now(),
                            "run_id": run_id,
                            "agent": agent,
                            "event": "repaired",
                            "attempt": attempt,
                            "repair_attempt": repair_idx,
                        },
                    )
                    repaired = True
                    return repaired_payload, canonical_output
                except Exception:
                    continue

            latency = int((time.perf_counter() - started) * 1000)
            store.finish_task(
                task_id,
                status="FAILED",
                latency_ms=latency,
                output_path=str(attempt_output),
                error_code="TASK_FAILED",
                error_message=err_text[:1000],
            )
            store.log_event(
                run_id,
                "ERROR",
                "task.failed",
                {"agent": agent, "attempt": attempt, "error": err_text, "repaired": repaired},
                task_id,
            )
            _append_event_jsonl(
                event_log_path,
                {
                    "ts": _now(),
                    "run_id": run_id,
                    "agent": agent,
                    "event": "failed",
                    "attempt": attempt,
                    "error": err_text,
                },
            )

            if attempt <= retries:
                await asyncio.sleep(backoff * (2 ** (attempt - 1)))
                continue
            raise

    raise RuntimeError(f"agent {agent} reached unexpected terminal path")


def _build_summary(
    run_id: str,
    run_dir: Path,
    status: str,
    report_path: Path,
    agent_paths: Dict[str, Path],
    started_ts: float,
    error: str = "",
) -> str:
    elapsed = time.perf_counter() - started_ts
    lines = [
        f"# Run Summary: {run_id}",
        "",
        f"- status: {status}",
        f"- generated_at: {_now()}",
        f"- elapsed_seconds: {elapsed:.2f}",
        f"- report: {report_path}",
    ]
    for agent in AGENTS:
        lines.append(f"- {agent}_output: {agent_paths.get(agent, Path('-'))}")
    if error:
        lines += ["", "## Error", error]
    lines += ["", "## Artifacts", f"- run_dir: {run_dir}"]
    return "\n".join(lines) + "\n"


async def run_scheduler(args: argparse.Namespace) -> int:
    subagents_dir = Path(__file__).resolve().parent
    config_path = args.config if args.config else (subagents_dir / "runtime.yaml")
    config = _load_runtime_config(config_path)

    run_root = _resolve_path(config["scheduler"]["run_root"], subagents_dir)
    state_db = _resolve_path(config["scheduler"]["state_db"], subagents_dir)
    run_id = args.run_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = run_root / run_id
    inputs_dir = run_dir / "inputs"
    inputs_dir.mkdir(parents=True, exist_ok=True)
    event_log_path = run_dir / "events.jsonl"

    plan_data = _load_json(args.plan)
    validate_plan(plan_data)
    snap_plan = inputs_dir / "plan.json"
    _write_json(snap_plan, plan_data)
    plan_hash = _sha256_json(plan_data)

    store = StateStore(state_db)
    store.create_run(run_id, "RUNNING", plan_hash=plan_hash, config=config)
    store.log_event(run_id, "INFO", "run.started", {"run_dir": str(run_dir), "plan": str(args.plan)})
    _append_event_jsonl(event_log_path, {"ts": _now(), "run_id": run_id, "event": "run_started"})

    started_ts = time.perf_counter()
    sem = asyncio.Semaphore(int(config["scheduler"]["max_concurrency"]))

    try:
        tasks = [
            _run_single_agent(
                agent=agent,
                run_id=run_id,
                plan_path=snap_plan,
                run_dir=run_dir,
                subagents_dir=subagents_dir,
                config=config,
                store=store,
                sem=sem,
                resume=args.resume,
                event_log_path=event_log_path,
            )
            for agent in AGENTS
        ]

        results = await asyncio.gather(*tasks, return_exceptions=True)
        payloads: Dict[str, Dict[str, Any]] = {}
        agent_paths: Dict[str, Path] = {}
        failures = []

        for agent, result in zip(AGENTS, results):
            if isinstance(result, Exception):
                failures.append((agent, str(result)))
            else:
                payloads[agent], agent_paths[agent] = result

        if failures:
            msg = "; ".join([f"{a}: {e}" for a, e in failures])
            store.update_run(run_id, status="FAILED", error=msg)
            summary = _build_summary(
                run_id=run_id,
                run_dir=run_dir,
                status="FAILED",
                report_path=run_dir / config["scheduler"]["report_name"],
                agent_paths=agent_paths,
                started_ts=started_ts,
                error=msg,
            )
            summary_path = run_dir / config["scheduler"]["summary_name"]
            summary_path.write_text(summary, encoding="utf-8")
            store.update_run(run_id, status="FAILED", summary_path=str(summary_path), error=msg)
            print(f"[{_now()}] run failed: {msg}")
            print(f"summary: {summary_path}")
            return 1

        validate_final_payloads(
            subagents_dir=subagents_dir,
            macro_payload=payloads["macro"],
            bear_payload=payloads["bear"],
            technical_payload=payloads["technical"],
        )

        orchestrator = _load_orchestrator(subagents_dir)
        report_md = orchestrator.render_report(
            plan=plan_data,
            macro=payloads["macro"],
            bear=payloads["bear"],
            technical=payloads["technical"],
        )
        report_path = run_dir / config["scheduler"]["report_name"]
        report_path.write_text(report_md, encoding="utf-8")
        store.record_artifact(run_id, "report", str(report_path), _sha256_bytes(report_md.encode("utf-8")))

        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(report_md, encoding="utf-8")
            store.record_artifact(run_id, "report_copy", str(args.output), _sha256_bytes(report_md.encode("utf-8")))

        summary = _build_summary(
            run_id=run_id,
            run_dir=run_dir,
            status="SUCCEEDED",
            report_path=report_path,
            agent_paths=agent_paths,
            started_ts=started_ts,
        )
        summary_path = run_dir / config["scheduler"]["summary_name"]
        summary_path.write_text(summary, encoding="utf-8")

        store.update_run(run_id, status="SUCCEEDED", summary_path=str(summary_path))
        store.log_event(run_id, "INFO", "run.succeeded", {"report": str(report_path), "summary": str(summary_path)})
        _append_event_jsonl(event_log_path, {"ts": _now(), "run_id": run_id, "event": "run_succeeded"})

        print(f"[{_now()}] run succeeded")
        print(f"run_id: {run_id}")
        print(f"report: {report_path}")
        print(f"summary: {summary_path}")
        if args.output:
            print(f"report_copy: {args.output}")
        return 0

    except Exception as exc:
        err = str(exc)
        store.update_run(run_id, status="FAILED", error=err)
        _append_event_jsonl(event_log_path, {"ts": _now(), "run_id": run_id, "event": "run_failed", "error": err})
        print(f"[{_now()}] run failed: {err}")
        return 1
    finally:
        store.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrent scheduler for subagent workflow")
    parser.add_argument("--plan", required=True, type=Path, help="Path to plan.json")
    parser.add_argument("--config", type=Path, help="Path to runtime.yaml")
    parser.add_argument("--run-id", type=str, help="Optional run id")
    parser.add_argument("--output", type=Path, help="Optional final report copy path")
    parser.add_argument("--resume", action="store_true", help="Reuse existing validated outputs if present")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    return asyncio.run(run_scheduler(args))


if __name__ == "__main__":
    raise SystemExit(main())
