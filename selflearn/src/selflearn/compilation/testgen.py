"""Workflow test author: generates independent test suites for cross-validation.

The test author enforces identity separation from the compiler.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from selflearn.compilation.models import ExecutorSpec, IndependentTestSuite, canonical_json
from selflearn.compilation.compiler import COMPILER_ID
from selflearn.contracts import ContractError
from selflearn.ports import IdentityPort, ModelPort, ProvenancePort

AUTHOR_ROLE = "workflow-test-author"


class WorkflowTestAuthorError(RuntimeError):
    """Error during test generation."""
    pass


# Backward-compatible alias preserved outside the public __all__.
TestAuthorError = WorkflowTestAuthorError


# Marker to represent the compiler identity for distinctness check
class _CompilerMarker:
    """Marker class representing the deterministic workflow compiler."""
    model_id = COMPILER_ID


class WorkflowTestAuthor:
    """Generates independent test suites for workflow executors.

    The test author must be distinct from the compiler identity per D6.
    """

    def __init__(self, model: ModelPort, identity: IdentityPort):
        self.model = model
        self.identity = identity

        # Enforce identity separation - compiler model_id must be distinct
        try:
            if not identity.distinct(model, _CompilerMarker()):
                raise WorkflowTestAuthorError(
                    f"identity violation: test author must be distinct from "
                    f"compiler (basis: {identity.basis})")
        except Exception as exc:
            # F2-17: convert identity-port failures into WorkflowTestAuthorError,
            # preserving the underlying cause.
            if isinstance(exc, WorkflowTestAuthorError):
                raise
            raise WorkflowTestAuthorError(
                f"identity verification failed: {exc}"
            ) from exc

    def author_suite(
        self,
        spec: ExecutorSpec,
        *,
        authored_at: str,
        provenance: ProvenancePort,
        clock: Optional[Callable[[], Any]] = None,
    ) -> IndependentTestSuite:
        """Generate an independent test suite for the executor spec.

        Args:
            spec: The executor specification to test
            authored_at: ISO timestamp
            provenance: Provenance port for test-author events (F3-6)

        Returns:
            IndependentTestSuite with generated test source

        Raises:
            WorkflowTestAuthorError: If the model returns invalid output
        """
        # Build context - NEVER includes executor source (asserted in tests)
        context = {
            "entry_id": spec.entry_id,
            "pack": spec.pack,
            "spec_hash": spec.spec_hash,
            "procedure": self._canonical_procedure(spec.procedure),
        }

        # Call model for test plan
        prompt = (
            "Generate a JSON test plan for this workflow executor. "
            "Return JSON {\"tests\": [{\"name\": \"...\", "
            "\"kind\": \"order\"|\"check\"|\"approval\"|\"failure-path\", "
            "\"step_id\": \"...\", \"expect\": \"...\"}]}. "
            "Must include at least one 'order' test and one 'approval' test "
            "if the spec has approval steps."
        )

        result = self.model.complete(AUTHOR_ROLE, prompt, context)
        plan = result.get("tests") if isinstance(result, dict) else None

        # Schema validation
        if not isinstance(plan, list) or not plan:
            raise WorkflowTestAuthorError("Test author returned no tests")

        # FIX-5: validate test plan
        valid_kinds = {"order", "check", "approval", "failure-path"}
        spec_step_ids = {step.id for step in spec.procedure}
        has_order = False
        has_approval = False

        for i, test in enumerate(plan):
            if not isinstance(test, dict):
                raise WorkflowTestAuthorError(f"Test #{i} is not a dict")
            kind = test.get("kind", "")
            if kind not in valid_kinds:
                raise WorkflowTestAuthorError(f"Test #{i} has invalid kind {kind!r}")

            # FIX-5: name/step_id/expect must be strings
            name = test.get("name", "")
            step_id = test.get("step_id", "")
            expect = test.get("expect", "")
            if not isinstance(name, str):
                raise WorkflowTestAuthorError(f"Test #{i} name must be a string")
            if not isinstance(step_id, str):
                raise WorkflowTestAuthorError(f"Test #{i} step_id must be a string")
            if not isinstance(expect, str):
                raise WorkflowTestAuthorError(f"Test #{i} expect must be a string")

            if kind == "order":
                has_order = True
                # F2-10: order expect may be a JSON array or comma-separated ids.
                if not expect:
                    raise WorkflowTestAuthorError(
                        f"Test #{i} (order) requires non-empty expect")
                # Parse order; validate every id belongs to the spec.
                try:
                    parsed = json.loads(expect)
                    if not isinstance(parsed, list):
                        raise WorkflowTestAuthorError(
                            f"Test #{i} (order) expect must be a list")
                except json.JSONDecodeError:
                    parsed = [part.strip() for part in expect.split(",")]
                for ordered_id in parsed:
                    if ordered_id not in spec_step_ids:
                        raise WorkflowTestAuthorError(
                            f"Test #{i} (order) expect step {ordered_id!r} "
                            f"not in spec")
            if kind == "approval":
                has_approval = True
                # F3-4: approval step_id must be non-empty and a real spec step
                if not step_id:
                    raise WorkflowTestAuthorError(
                        f"Test #{i} (approval) requires a step_id")
                if step_id not in spec_step_ids:
                    raise WorkflowTestAuthorError(
                        f"Test #{i} (approval) step_id {step_id!r} not in spec")
            if kind == "check":
                # FIX-5: check/failure-path step_id must be in spec
                if step_id and step_id not in spec_step_ids:
                    raise WorkflowTestAuthorError(
                        f"Test #{i} step_id {step_id!r} not in spec")
            if kind == "failure-path":
                if step_id and step_id not in spec_step_ids:
                    raise WorkflowTestAuthorError(
                        f"Test #{i} step_id {step_id!r} not in spec")

        # Coverage floor: need order test
        if not has_order:
            raise WorkflowTestAuthorError("Test plan must include at least one 'order' test")

        # Coverage floor: need approval test if spec has approval steps
        if self._has_approval_step(spec.procedure) and not has_approval:
            raise WorkflowTestAuthorError(
                "Test plan must include at least one 'approval' test "
                "since the spec has approval steps")

        # Render test source
        test_source = self._render_tests(spec, plan)

        import hashlib

        author_id = getattr(self.model, "model_id", "unknown")
        identity_basis = self.identity.basis

        # F3-10: suite_hash binds test source + author identity so mutating
        # authorship invalidates the hash.
        suite_hash = hashlib.sha256(
            canonical_json([test_source, author_id, identity_basis]).encode()
        ).hexdigest()

        suite = IndependentTestSuite(
            spec_hash=spec.spec_hash,
            test_source=test_source,
            suite_hash=suite_hash,
            author_id=author_id,
            identity_basis=identity_basis,
            authored_at=authored_at,
        )

        # F3-6: always journal test-author event (provenance is required)
        timestamp = clock().isoformat() if clock is not None else authored_at
        provenance.append({
            "kind": "test-author",
            "entry_id": spec.entry_id,
            "spec_hash": spec.spec_hash,
            "suite_hash": suite_hash,
            "author_id": suite.author_id,
            "identity_basis": suite.identity_basis,
            "actor": AUTHOR_ROLE,
            "timestamp": timestamp,
        })

        return suite

    def _canonical_procedure(self, procedure: tuple) -> list:
        """Convert procedure to canonical list for context."""
        result = []
        for step in procedure:
            result.append({
                "id": step.id,
                "objective": step.objective,
                "task_type": step.task_type,
                "tools": list(step.tools),
                "depends_on": list(step.depends_on),
                "check": [list(pair) for pair in step.check],
            })
        return result

    def _has_approval_step(self, procedure: tuple) -> bool:
        """Check if any step is an approval step."""
        from selflearn.compilation.compiler import is_approval_step
        return any(is_approval_step(step) for step in procedure)

    def _render_tests(self, spec: ExecutorSpec, plan: list) -> str:
        """Render test plan into executable Python test source.

        FIX-5: every model-supplied string goes through json.dumps (repr)
        before rendering into test source. No raw concatenation/format.
        """
        lines = []
        lines.append("# Auto-generated workflow test suite")
        lines.append("# Generated by workflow-test-author")
        lines.append("")
        # F4-2: do not emit `import json`.  The sandbox harness injects `json`
        # directly into the restricted execution namespace; keeping imports out
        # of model-rendered code closes __import__ injection paths.

        lines.append("def run_tests(load_executor):")
        lines.append('    """Run the test suite against an executor."""')
        lines.append("    results = []")
        lines.append("")

        # Build set of valid step ids for this spec
        spec_step_ids = {step.id for step in spec.procedure}

        for i, test in enumerate(plan):
            name = test.get("name", f"test_{i}")
            kind = test.get("kind")
            step_id = test.get("step_id", "")
            expect = test.get("expect", "")

            # FIX-5: all strings go through json.dumps before code embedding
            safe_name = json.dumps(name)
            safe_step_id = json.dumps(step_id)
            safe_expect = json.dumps(expect)

            lines.append(f"    # Test: {safe_name} (kind={kind!r})")
            if kind == "order":
                # F2-10: expect is a JSON list or comma-separated ids; render
                # the parsed list deterministically. Validation above guarantees
                # it is a list of valid step ids.
                try:
                    parsed_order = json.loads(expect)
                except json.JSONDecodeError:
                    parsed_order = [part.strip() for part in expect.split(",")]
                rendered_expect = json.dumps(parsed_order)
                lines.append(f"    # Expect: step order matches {safe_expect}")
                lines.append("    try:")
                lines.append("        executor = load_executor()")
                lines.append("        def order_handler(sid, sdata):")
                lines.append("            result = {'status': 'ok', 'checks': {}}")
                lines.append("            for ck, cv in sdata.get('check', []):")
                lines.append("                if ck == 'approval':")
                lines.append("                    continue")
                lines.append("                if ck == 'status':")
                lines.append("                    result['status'] = cv")
                lines.append("                else:")
                lines.append("                    result['checks'][ck] = cv")
                lines.append("            return result")
                lines.append("        result = executor['run'](order_handler)")
                lines.append(f"        expected_order = {rendered_expect}")
                lines.append("        actual_order = result.get('completed', [])")
                lines.append("        assert actual_order == expected_order, f'Order mismatch: {actual_order} vs {expected_order}'")
                lines.append(f"        results.append(('pass', {safe_name}))")
                lines.append("    except Exception as e:")
                lines.append(f"        results.append(('fail', {safe_name} + ': ' + str(e)))")
                lines.append("")

            elif kind == "check":
                # F3-5: render a spec-aware check handler.  The executor evaluates
                # the step's declared check pairs, so the handler must satisfy them.
                lines.append(f"    # Expect: check {safe_expect}")
                lines.append("    try:")
                lines.append("        executor = load_executor()")
                lines.append("        step_id = " + safe_step_id)
                lines.append("        step_data = executor['STEPS'][step_id]")
                lines.append("        def check_handler(sid, sdata):")
                lines.append("            if sid != step_id:")
                lines.append("                return {'status': 'ok'}")
                lines.append("            result = {'status': 'ok', 'checks': {}}")
                lines.append("            for ck, cv in sdata.get('check', []):")
                lines.append("                if ck == 'approval':")
                lines.append("                    continue")
                lines.append("                if ck == 'status':")
                lines.append("                    result['status'] = cv")
                lines.append("                else:")
                lines.append("                    result['checks'][ck] = cv")
                lines.append("            return result")
                lines.append("        result = executor['run'](check_handler)")
                lines.append("        assert step_id in result.get('completed', [])")
                lines.append(f"        results.append(('pass', {safe_name}))")
                lines.append("    except Exception as e:")
                lines.append(f"        results.append(('fail', {safe_name} + ': ' + str(e)))")
                lines.append("")

            elif kind == "approval":
                lines.append(f"    # Expect: approval required at {safe_step_id}")
                lines.append("    try:")
                lines.append("        executor = load_executor()")
                lines.append("        def approval_handler(sid, sdata):")
                lines.append("            return {'status': 'ok'}")
                lines.append("        try:")
                lines.append("            executor['run'](approval_handler)")
                lines.append(f"            results.append(('fail', {safe_name} + ': approval not raised'))")
                lines.append("        except Exception as e:")
                lines.append("            if 'ApprovalRequired' in type(e).__name__:")
                lines.append(f"                results.append(('pass', {safe_name}))")
                lines.append("            else:")
                lines.append(f"                results.append(('fail', {safe_name} + ': wrong exception'))")
                lines.append("    except Exception as e:")
                lines.append(f"        results.append(('fail', {safe_name} + ': ' + str(e)))")
                lines.append("")

            elif kind == "failure-path":
                lines.append(f"    # Expect: failure at {safe_step_id}")
                lines.append("    try:")
                lines.append("        executor = load_executor()")
                lines.append(f"        def fail_handler(sid, sdata):")
                lines.append(f"            if sid == {safe_step_id}:")
                lines.append("                return {'status': 'fail'}")
                lines.append("            return {'status': 'ok'}")
                lines.append("        try:")
                lines.append("            executor['run'](fail_handler)")
                lines.append(f"            results.append(('fail', {safe_name} + ': no failure'))")
                lines.append("        except Exception as e:")
                lines.append(f"            results.append(('pass', {safe_name}))")
                lines.append("    except Exception as e:")
                lines.append(f"        results.append(('fail', {safe_name} + ': ' + str(e)))")
                lines.append("")

        lines.append("    return results")
        return "\n".join(lines)
