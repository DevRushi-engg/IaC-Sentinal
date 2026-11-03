"""
Patch Generator and Isolated Worktree Manager for MERIDIAN.
Safely applies proposed candidate changes in an isolated workspace.
"""
from __future__ import annotations
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional
from src.config import Config
from src.drift_model import LLMRemediationProposal
from src.exceptions import PatchApplicationError
from src.audit_logger import audit_logger


class PatchGenerator:
    """Manages isolated workspaces and applies candidate remediation patches."""

    def __init__(self, source_terraform_dir: Optional[Path] = None):
        self.source_dir = source_terraform_dir or Config.TERRAFORM_DIR

    def create_isolated_workspace(self) -> Path:
        """Create a temporary isolated directory copying the current Terraform code."""
        temp_dir = Path(tempfile.mkdtemp(prefix="meridian_tf_"))
        try:
            if self.source_dir.exists():
                shutil.copytree(self.source_dir, temp_dir, dirs_exist_ok=True)
            else:
                temp_dir.mkdir(parents=True, exist_ok=True)
            return temp_dir
        except Exception as e:
            raise PatchApplicationError(f"Failed to create isolated workspace: {e}")

    def apply_proposal_to_workspace(
        self,
        workspace: Path,
        proposal: LLMRemediationProposal
    ) -> List[Path]:
        """
        Apply proposed candidate changes to the isolated workspace.
        Enforces strict boundary check: only files inside the workspace are allowed.
        """
        modified_files: List[Path] = []

        if not proposal.patch or not proposal.files_to_modify:
            audit_logger.log_event("PATCH_SKIPPED", {
                "reason": "No patch or files to modify provided",
                "status": proposal.status.value,
            })
            return modified_files

        for file_rel in proposal.files_to_modify:
            # Normalize path
            clean_rel = file_rel.replace("\\", "/")
            if clean_rel.startswith("terraform/"):
                clean_rel = clean_rel[len("terraform/"):]

            target_file = (workspace / clean_rel).resolve()

            # Security check: Ensure target_file is strictly inside workspace
            if not str(target_file).startswith(str(workspace.resolve())):
                raise PatchApplicationError(f"Illegal path escape detected: {file_rel}")

            # Apply patch
            self._apply_patch_to_file(target_file, proposal.patch)
            modified_files.append(target_file)

        audit_logger.log_event("PATCH_APPLIED_TO_WORKSPACE", {
            "workspace": str(workspace),
            "modified_files": [str(f.name) for f in modified_files],
        })

        return modified_files

    def _apply_patch_to_file(self, target_file: Path, patch_content: str) -> None:
        """Apply unified diff or raw block content to target file."""
        target_file.parent.mkdir(parents=True, exist_ok=True)

        # Check if patch is unified diff
        if patch_content.startswith("---") or "\n+++" in patch_content:
            self._apply_unified_diff(target_file, patch_content)
        else:
            # If it's a replacement HCL block / file content, write directly if target exists or is created
            with open(target_file, "w", encoding="utf-8") as f:
                f.write(patch_content)

        # Normalize formatting with terraform fmt if CLI is available
        if shutil.which("terraform"):
            try:
                import subprocess
                subprocess.run(["terraform", "fmt", str(target_file)], capture_output=True, timeout=10)
            except Exception:
                pass

    def _apply_unified_diff(self, target_file: Path, diff_text: str) -> None:
        """Parse and apply unified diff additions/deletions onto existing target file."""
        if not target_file.exists():
            new_lines: List[str] = []
            for line in diff_text.splitlines(keepends=True):
                if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                    continue
                if line.startswith("+"):
                    new_lines.append(line[1:])
                elif line.startswith(" "):
                    new_lines.append(line[1:])
            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            return

        with open(target_file, "r", encoding="utf-8") as f:
            original_lines = f.readlines()

        removed_lines: List[str] = []
        added_lines: List[str] = []
        for line in diff_text.splitlines():
            if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
                continue
            if line.startswith("-"):
                removed_lines.append(line[1:])
            elif line.startswith("+"):
                added_lines.append(line[1:])

        # If diff has specific removals and additions, perform in-place substitution
        if removed_lines and added_lines:
            content = "".join(original_lines)
            patched = False
            # 1. Try exact replacement of removed lines
            for rem in removed_lines:
                rem_stripped = rem.strip()
                if not rem_stripped:
                    continue
                for add in added_lines:
                    add_formatted = add if add.endswith("\n") else add + "\n"
                    lines = content.splitlines(keepends=True)
                    new_lines = []
                    for line in lines:
                        if rem_stripped in line:
                            indent = line[:len(line) - len(line.lstrip())]
                            final_add = add_formatted if add_formatted.startswith(" ") else f"{indent}{add_formatted}"
                            new_lines.append(final_add)
                            patched = True
                        else:
                            new_lines.append(line)
                    if patched:
                        content = "".join(new_lines)
                        break
                if patched:
                    break

            # 2. If not matched by exact text, match by attribute key (e.g. `Environment =`)
            if not patched:
                for add in added_lines:
                    if "=" in add:
                        key = add.split("=")[0].strip()
                        lines = content.splitlines(keepends=True)
                        new_lines = []
                        for line in lines:
                            stripped_line = line.strip()
                            if stripped_line.startswith(key) and ("=" in stripped_line):
                                indent = line[:len(line) - len(line.lstrip())]
                                add_body = add.strip() + "\n"
                                new_lines.append(f"{indent}{add_body}")
                                patched = True
                            else:
                                new_lines.append(line)
                        if patched:
                            content = "".join(new_lines)
                            break

            if patched:
                with open(target_file, "w", encoding="utf-8") as f:
                    f.write(content)
                return

        # Fallback if diff provided full replacement block
        non_meta_lines = [
            line[1:] if line.startswith(("+", " ")) else line
            for line in diff_text.splitlines(keepends=True)
            if not line.startswith(("---", "+++", "@@", "-"))
        ]
        if non_meta_lines and not target_file.exists():
            with open(target_file, "w", encoding="utf-8") as f:
                f.writelines(non_meta_lines)

    @staticmethod
    def cleanup_workspace(workspace: Path) -> None:
        """Safely delete isolated workspace after validation."""
        try:
            if workspace.exists() and "meridian_tf_" in str(workspace):
                shutil.rmtree(workspace, ignore_errors=True)
        except Exception as e:
            audit_logger.log_event("WORKSPACE_CLEANUP_FAILED", {"path": str(workspace), "error": str(e)}, level="WARNING")
