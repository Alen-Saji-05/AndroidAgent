"""Planner LLM backends.

Same discipline as perception's OCR layer: a protocol with a scripted no-API
implementation for tests (NullPlanner, cf. NullOCR) and a real one behind it
(GeminiPlanner). Swapping providers — or dropping in on-device Llama later —
touches only this file.

Both calls demand structured JSON output and return already-parsed dicts; the
Planner state machine (planner.py) turns those into Plan / PlanUpdate objects
and owns all the control logic, so backends stay thin and replaceable.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional, Protocol

from . import prompts

log = logging.getLogger(__name__)

# Load GEMINI_API_KEY (and friends) from a project .env if python-dotenv is
# present. Real env vars always win; a missing dotenv is not an error, so tests
# and CI that set the var directly are unaffected.
try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True), override=False)
except ImportError:
    pass


class PlannerError(RuntimeError):
    pass


class PlannerBackend(Protocol):
    def decompose(self, goal: str, context: Optional[str] = None) -> dict[str, Any]: ...

    def reassess(
        self,
        goal: str,
        plan_text: str,
        screen_digest: str,
        last_action: str,
        verifier_report: str,
    ) -> dict[str, Any]: ...


class NullPlanner:
    """Scripted backend for tests and offline runs — no network, no key.

    `decompose` returns a single catch-all subgoal echoing the goal (or a queued
    canned response); `reassess` returns queued responses, defaulting to
    'advance'. Tests push exact dicts via `script_*` to drive the state machine
    deterministically, exactly like perception's stub detectors.
    """

    def __init__(
        self,
        decompose_responses: Optional[list[dict[str, Any]]] = None,
        reassess_responses: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._decompose = list(decompose_responses or [])
        self._reassess = list(reassess_responses or [])

    def decompose(self, goal: str, context: Optional[str] = None) -> dict[str, Any]:
        if self._decompose:
            return self._decompose.pop(0)
        return {
            "subgoals": [
                {
                    "id": "s0",
                    "description": goal.strip(),
                    "success_hint": "the task appears complete on screen",
                    "requires_confirmation": False,
                }
            ]
        }

    def reassess(self, goal, plan_text, screen_digest, last_action, verifier_report):
        if self._reassess:
            return self._reassess.pop(0)
        return {"kind": "advance", "reason": "default NullPlanner advance"}


class GeminiPlanner:
    """Google Gemini (Flash) backend using JSON-mode structured output.

    Free-tier note: prompts contain screen digests that may hold personal data,
    and the free tier may use them to improve Google's products. Fine for
    development against throwaway screens; move to paid or on-device Llama before
    real accounts. The model id is a single constant so it is trivial to re-pin.
    """

    # Flash keeps the per-step loop cheap and fast. Use the moving alias, not a
    # pinned version: within days this project saw gemini-2.0-flash retired,
    # gemini-2.5-flash blocked for new users, and pinned 3.x ids gated per-project.
    # The alias tracks whatever Flash the key can actually use. Override with
    # GEMINI_MODEL if a specific version is required.
    DEFAULT_MODEL = "gemini-flash-latest"

    def __init__(self, model: Optional[str] = None, api_key: Optional[str] = None) -> None:
        self.model_name = model or os.environ.get("GEMINI_MODEL", self.DEFAULT_MODEL)
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY")
        self._model = None

    def _client(self):
        if self._model is not None:
            return self._model
        if not self._api_key:
            raise PlannerError(
                "GEMINI_API_KEY is not set. Export it, or use NullPlanner for offline runs."
            )
        try:
            import google.generativeai as genai
        except ImportError as e:
            raise PlannerError(
                "google-generativeai is not installed. `pip install -r requirements-planner.txt`"
            ) from e
        genai.configure(api_key=self._api_key)
        self._genai = genai
        self._model = genai.GenerativeModel(self.model_name)
        return self._model

    def _generate(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        model = self._client()
        cfg = self._genai.GenerationConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2,  # decomposition wants consistency, not creativity
        )
        try:
            resp = model.generate_content([system, user], generation_config=cfg)
            return json.loads(resp.text)
        except json.JSONDecodeError as e:
            raise PlannerError(f"model returned non-JSON: {e}") from e
        except Exception as e:  # SDK raises a variety of transport/quota errors
            raise PlannerError(f"Gemini request failed: {e}") from e

    def decompose(self, goal: str, context: Optional[str] = None) -> dict[str, Any]:
        return self._generate(
            prompts.DECOMPOSE_SYSTEM,
            prompts.decompose_user_prompt(goal, context),
            prompts.DECOMPOSE_SCHEMA,
        )

    def reassess(self, goal, plan_text, screen_digest, last_action, verifier_report):
        return self._generate(
            prompts.REASSESS_SYSTEM,
            prompts.reassess_user_prompt(
                goal, plan_text, screen_digest, last_action, verifier_report
            ),
            prompts.REASSESS_SCHEMA,
        )


def default_backend() -> PlannerBackend:
    """GeminiPlanner when a key is present, else NullPlanner with a warning."""
    if os.environ.get("GEMINI_API_KEY"):
        return GeminiPlanner()
    log.warning("GEMINI_API_KEY not set - using NullPlanner (echo plans, no reasoning)")
    return NullPlanner()
