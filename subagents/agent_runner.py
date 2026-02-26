#!/usr/bin/env python3
"""Unified runner for subagents.

Modes:
- mock: load predefined JSON and write to output
- command: execute command template that produces output JSON
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
from pathlib import Path
from typing import Any, Dict, Tuple


class AgentRunError(Exception):
    """Raised when an agent execution fails."""


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise AgentRunError(f"JSON root must be object: {path}")
    return data


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _default_mock_path(subagents_dir: Path, agent: str) -> Path:
    mapping = {
        "macro": subagents_dir / "examples" / "macro_industry.json",
        "bear": subagents_dir / "examples" / "bear_case.json",
        "technical": subagents_dir / "examples" / "technical.json",
    }
    if agent not in mapping:
        raise AgentRunError(f"Unknown agent: {agent}")
    return mapping[agent]


def _resolve_agent_mode(config: Dict[str, Any], agent: str) -> str:
    agents = config.get("agents", {})
    provider = config.get("provider", {})
    agent_cfg = agents.get(agent, {})
    return str(agent_cfg.get("mode") or provider.get("mode") or "mock")


def _resolve_mock_input(config: Dict[str, Any], subagents_dir: Path, agent: str) -> Path:
    provider = config.get("provider", {})
    paths = provider.get("mock_data_paths", {}) if isinstance(provider, dict) else {}
    custom = paths.get(agent) if isinstance(paths, dict) else None
    if custom:
        p = Path(custom)
        return p if p.is_absolute() else (subagents_dir / p)
    return _default_mock_path(subagents_dir, agent)


def _run_command(
    template: str,
    plan_path: Path,
    output_path: Path,
    run_dir: Path,
    timeout_seconds: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    rendered = template.format(
        plan=shlex.quote(str(plan_path)),
        output=shlex.quote(str(output_path)),
        run_dir=shlex.quote(str(run_dir)),
    )
    result = subprocess.run(
        rendered,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        raise AgentRunError(
            "command failed: rc={rc} stderr={stderr}".format(
                rc=result.returncode,
                stderr=result.stderr.strip()[:500],
            )
        )
    if not output_path.exists():
        raise AgentRunError(f"command completed but output missing: {output_path}")
    payload = _read_json(output_path)
    meta = {
        "mode": "command",
        "stdout": result.stdout.strip()[:1000],
        "stderr": result.stderr.strip()[:1000],
    }
    return payload, meta


def run_agent(
    agent: str,
    plan_path: Path,
    output_path: Path,
    run_dir: Path,
    subagents_dir: Path,
    config: Dict[str, Any],
    timeout_seconds: int,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Run agent and return payload + metadata."""
    _ = _read_json(plan_path)
    mode = _resolve_agent_mode(config, agent)

    if mode == "mock":
        mock_path = _resolve_mock_input(config, subagents_dir, agent)
        payload = _read_json(mock_path)
        _write_json(output_path, payload)
        return payload, {"mode": "mock", "mock_input": str(mock_path)}

    if mode == "command":
        agent_cfg = config.get("agents", {}).get(agent, {})
        template = agent_cfg.get("command_template") or config.get("provider", {}).get("command_template")
        if not template:
            raise AgentRunError(f"agent={agent} mode=command but no command_template configured")
        return _run_command(str(template), plan_path, output_path, run_dir, timeout_seconds)

    raise AgentRunError(f"Unsupported mode for agent={agent}: {mode}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one subagent and produce JSON payload")
    parser.add_argument("--agent", required=True, choices=["macro", "bear", "technical"])
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--subagents-dir", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=180)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import yaml

    cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    payload, meta = run_agent(
        agent=args.agent,
        plan_path=args.plan,
        output_path=args.output,
        run_dir=args.run_dir,
        subagents_dir=args.subagents_dir,
        config=cfg,
        timeout_seconds=args.timeout,
    )
    print(json.dumps({"agent": args.agent, "meta": meta, "keys": sorted(payload.keys())}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
