"""Workflow compiler: deterministic procedure-to-Python compiler.

This module generates executable Python from workflow procedure definitions.
No model in the control spine - pure stdlib code generation.
"""
from __future__ import annotations

import json

from selflearn.contracts import CandidateEntry, ContractError, ProcedureStep

from selflearn.compilation.models import (
    ExecutorCandidate,
    ExecutorSpec,
    canonical_procedure_hash,
    content_hash,
)

COMPILER_ID = "deterministic-workflow-compiler:v1"


class CompilerError(RuntimeError):
    """Error during workflow compilation."""
    pass


def is_approval_step(step: ProcedureStep) -> bool:
    """Check if step is an approval-type step.

    A step is approval-type if:
    - task_type == "approval" OR
    - bool(step.check_dict().get("approval"))
    """
    if step.task_type == "approval":
        return True
    if step.check_dict().get("approval"):
        return True
    return False


def _escape_string(s: str) -> str:
    """Escape a string for safe inclusion in Python source.

    Uses repr-style escaping - never format/f-string interpolation.
    """
    # Use repr to get proper escaping, then wrap in quotes
    return repr(s)


def _generate_step_dict(step: ProcedureStep) -> str:
    """Generate a Python dict literal for a step."""
    parts = []
    parts.append(f'"id": {_escape_string(step.id)}')
    parts.append(f'"objective": {_escape_string(step.objective)}')
    parts.append(f'"task_type": {_escape_string(step.task_type)}')
    parts.append(f'"tools": {json.dumps(list(step.tools))}')
    parts.append(f'"depends_on": {json.dumps(list(step.depends_on))}')
    # Check is tuple of tuples - convert to list of lists for JSON
    check_list = [list(pair) for pair in step.check]
    parts.append(f'"check": {json.dumps(check_list)}')
    return "{" + ", ".join(parts) + "}"


class WorkflowCompiler:
    """Compiles workflow entries to executable Python modules."""

    def compile(self, entry: CandidateEntry, *, pack: str,
                compiled_at: str) -> ExecutorCandidate:
        """Compile a workflow entry to an ExecutorCandidate.

        Args:
            entry: A CandidateEntry with kind == "workflow"
            pack: The pack name
            compiled_at: ISO timestamp

        Returns:
            ExecutorCandidate with generated source code

        Raises:
            CompilerError: If entry is not a workflow or has empty procedure
        """
        if entry.kind != "workflow":
            raise CompilerError(
                f"WorkflowCompiler requires workflow entry, got {entry.kind!r}")

        if not entry.procedure:
            raise CompilerError(
                "WorkflowCompiler requires non-empty procedure")

        # Build spec
        spec_hash = canonical_procedure_hash(entry.procedure)
        spec = ExecutorSpec(
            entry_id=entry.id,
            pack=pack,
            spec_hash=spec_hash,
            procedure=entry.procedure,
        )

        # Generate source code (FIX-10: compiled_at not in source body)
        source = self._generate_source(spec, entry.id)
        executor_hash = content_hash(source)

        return ExecutorCandidate(
            spec=spec,
            source=source,
            executor_hash=executor_hash,
            compiled_at=compiled_at,
            compiler_id=COMPILER_ID,
        )

    def _generate_source(self, spec: ExecutorSpec, entry_id: str) -> str:
        """Generate the Python source for the executor."""
        lines = []
        lines.append("# Auto-generated workflow executor")
        lines.append(f"# ENTRY_ID: {entry_id}")
        lines.append(f"# SPEC_HASH: {spec.spec_hash}")
        lines.append(f"# COMPILER: {COMPILER_ID}")
        lines.append("")

        # NOTE: 'json' is injected into the exec globals by the runtime.
        # Do NOT add 'import json' here — it would require __import__ in
        # builtins, which is excluded by the D3 sandbox whitelist.
        lines.append("# json module is injected by ExecutorRuntime._make_restricted_globals")
        lines.append("")

        # FIX-10: Add real constants (not only comments)
        lines.append(f'ENTRY_ID = {repr(entry_id)}')
        lines.append(f'SPEC_HASH = {repr(spec.spec_hash)}')
        lines.append("")

        # Module-level completed tracker (survives exception; read back in runtime)
        lines.append("_COMPLETED = []")
        lines.append("")

        # STEPS dict
        lines.append("STEPS = {")
        for step in spec.procedure:
            step_dict = _generate_step_dict(step)
            lines.append(f"    {_escape_string(step.id)}: {step_dict},")
        lines.append("}")
        lines.append("")

        # ORDER tuple
        order_ids = [step.id for step in spec.procedure]
        lines.append(f"ORDER = {json.dumps(order_ids)}")
        lines.append("")

        # Exceptions
        lines.append("class ApprovalRequired(Exception):")
        lines.append("    def __init__(self, step_id):")
        lines.append("        self.step_id = step_id")
        lines.append("        super().__init__(f'Approval required at step: {step_id}')")
        lines.append("")

        lines.append("class StepCheckFailed(Exception):")
        lines.append("    def __init__(self, step_id, check_key):")
        lines.append("        self.step_id = step_id")
        lines.append("        self.check_key = check_key")
        lines.append("        super().__init__(f'Step {step_id} check {check_key!r} failed')")
        lines.append("")

        # Run function
        lines.append("def run(step_handler):")
        lines.append('    """Execute the workflow, driving step_handler for each step."""')
        lines.append("    for step_id in ORDER:")
        lines.append("        step_data = STEPS[step_id]")
        lines.append("        task_type = step_data['task_type']")
        lines.append("")
        lines.append("        # Check if this is an approval step")
        lines.append("        check_dict = dict(step_data.get('check', []))")
        lines.append("        is_approval = task_type == 'approval' or check_dict.get('approval')")
        lines.append("        if is_approval:")
        lines.append("            raise ApprovalRequired(step_id)")
        lines.append("")
        lines.append("        # Execute step via handler")
        lines.append("        result = step_handler(step_id, step_data)")
        lines.append("        if not isinstance(result, dict):")
        lines.append("            raise StepCheckFailed(step_id, '<return-type>')")
        lines.append("")
        lines.append("        # Evaluate checks")
        lines.append("        check_passed = True")
        lines.append("        for check_key, check_value in step_data.get('check', []):")
        lines.append("            if check_key == 'approval':")
        lines.append("                continue  # handled above")
        lines.append("            if check_key == 'status':")
        lines.append("                if result.get('status') != check_value:")
        lines.append("                    check_passed = False")
        lines.append("                    raise StepCheckFailed(step_id, check_key)")
        lines.append("            else:")
        lines.append("                # Other checks are in result['checks']")
        lines.append("                if result.get('checks', {}).get(check_key) != check_value:")
        lines.append("                    check_passed = False")
        lines.append("                    raise StepCheckFailed(step_id, check_key)")
        lines.append("        if check_passed:")
        lines.append("            _COMPLETED.append(step_id)")
        lines.append("")
        lines.append('    return {"completed": _COMPLETED}')

        return "\n".join(lines)
