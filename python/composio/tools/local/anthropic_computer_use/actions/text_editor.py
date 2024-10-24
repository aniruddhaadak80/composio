import typing as t
from pathlib import Path

from pydantic import Field

from composio.tools.base.local import LocalAction
from composio.tools.base.exceptions import ExecutionFailed
from composio.tools.local.filetool.actions.base_action import (
    BaseFileRequest,
    BaseFileResponse,
    include_cwd,
)
from composio.tools.env.filemanager.file import File, FileOperationScope, TextReplacement
from composio.tools.env.filemanager.manager import FileManager

# Import the run function
from .run import run, maybe_truncate

Command = t.Literal["view", "create", "str_replace", "insert", "undo_edit"]
SNIPPET_LINES: int = 4

class TextEditorRequest(BaseFileRequest):
    """Request for text editor operations."""
    command: Command = Field(..., description="The command to execute")
    file_path: str = Field(..., description="The path to the file to edit")
    file_text: t.Optional[str] = Field(None, description="The text to write to the file when creating")
    view_range: t.Optional[t.List[int]] = Field(None, description="The range of lines to view")
    old_str: t.Optional[str] = Field(None, description="The string to replace")
    new_str: t.Optional[str] = Field(None, description="The new string to insert")
    insert_line: t.Optional[int] = Field(None, description="The line number to insert at")

class TextEditorResponse(BaseFileResponse):
    """Response from text editor operations."""
    output: str = Field(..., description="The output of the operation")
    error: t.Optional[str] = Field(default=None, description="Error message, if any")

class TextEditor(LocalAction[TextEditorRequest, TextEditorResponse]):
    """
    A text editor tool that allows viewing, creating, and editing files.
    """

    @include_cwd  # type: ignore
    def execute(self, request: TextEditorRequest, metadata: t.Dict) -> TextEditorResponse:
        try:
            return self._execute(request, metadata)
        except Exception as e:
            return TextEditorResponse(output="", error=str(e))

    async def execute_async(self, request: TextEditorRequest, metadata: t.Dict) -> TextEditorResponse:
        try:
            return await self._execute_async(request, metadata)
        except Exception as e:
            return TextEditorResponse(output="", error=str(e))

    def _execute(self, request: TextEditorRequest, metadata: t.Dict) -> TextEditorResponse:
        file_manager = self.filemanagers.get(request.file_manager_id)
        
        if request.command == "view":
            return self.view(file_manager, request.file_path, request.view_range)
        elif request.command == "create":
            return self.create(file_manager, request.file_path, request.file_text)
        elif request.command == "str_replace":
            if request.old_str is None or request.new_str is None:
                raise ExecutionFailed("old_str and new_str are required for str_replace command")
            return self.str_replace(file_manager, request.file_path, request.old_str, request.new_str)
        elif request.command == "insert":
            if request.insert_line is None or request.new_str is None:
                raise ExecutionFailed("insert_line and new_str are required for insert command")
            return self.insert(file_manager, request.file_path, request.insert_line, request.new_str)
        elif request.command == "undo_edit":
            return self.undo_edit(file_manager, request.file_path)
        else:
            raise ExecutionFailed(f"Unknown command: {request.command}")

    async def _execute_async(self, request: TextEditorRequest, metadata: t.Dict) -> TextEditorResponse:
        file_manager = self.filemanagers.get(request.file_manager_id)
        
        if request.command == "view":
            return await self.view_async(file_manager, request.file_path, request.view_range)
        # Implement other async methods as needed
        else:
            return self._execute(request, metadata)

    def view(self, file_manager: FileManager, file_path: str, view_range: t.Optional[t.List[int]]) -> TextEditorResponse:
        file = self._get_file(file_manager, file_path)
        content = self._get_content(file, view_range)
        return TextEditorResponse(output=file.format_text(content))

    async def view_async(self, file_manager: FileManager, file_path: str, view_range: t.Optional[t.List[int]]) -> TextEditorResponse:
        file_path = Path(file_path)
        if not file_path.is_file():
            raise ExecutionFailed(f"File not found: {file_path}")
        
        cmd = f"cat {file_path}"
        if view_range:
            cmd = f"sed -n '{view_range[0]},{view_range[1]}p' {file_path}"
        
        _, stdout, stderr = await run(cmd)
        if stderr:
            raise ExecutionFailed(f"Error viewing file: {stderr}")
        return TextEditorResponse(output=stdout)

    def create(self, file_manager: FileManager, file_path: str, file_text: t.Optional[str]) -> TextEditorResponse:
        if not file_text:
            raise ExecutionFailed("file_text is required for create command")
        file = file_manager.create(file_path)
        file.write(file_text)
        return TextEditorResponse(output=f"File created successfully at: {file_path}")

    def str_replace(self, file_manager: FileManager, file_path: str, old_str: str, new_str: str) -> TextEditorResponse:
        file = self._get_file(file_manager, file_path)
        result = file.replace(old_str, new_str)
        if "error" in result and result["error"]:
            raise ExecutionFailed(result["error"])
        snippet = self._get_snippet(file, new_str)
        return TextEditorResponse(output=f"File edited successfully. Snippet:\n{snippet}")

    def insert(self, file_manager: FileManager, file_path: str, insert_line: int, new_str: str) -> TextEditorResponse:
        file = self._get_file(file_manager, file_path)
        result = file.edit(new_str, insert_line, insert_line)
        if "error" in result and result["error"]:
            raise ExecutionFailed(result["error"])
        return TextEditorResponse(output=f"Text inserted successfully. Updated content:\n{result['replaced_with']}")

    def undo_edit(self, file_manager: FileManager, file_path: str) -> TextEditorResponse:
        file = self._get_file(file_manager, file_path)
        previous_content = file.undo()
        if previous_content is None:
            raise ExecutionFailed(f"No edit history found for {file_path}.")
        return TextEditorResponse(output=f"Last edit to {file_path} undone successfully. Updated content:\n{file.format_text(file.read())}")

    def _get_file(self, file_manager: FileManager, file_path: str) -> File:
        try:
            return file_manager.open(file_path)
        except FileNotFoundError:
            raise ExecutionFailed(f"File not found: {file_path}")

    def _get_content(self, file: File, view_range: t.Optional[t.List[int]]) -> t.Dict[int, str]:
        if view_range:
            file.goto(view_range[0])
            content = file.read()
            if view_range[1] != -1:
                content = {k: v for k, v in content.items() if k <= view_range[1]}
        else:
            content = file.read()
        return content

    def _get_snippet(self, file: File, search_str: str) -> str:
        content = file.read()
        for line_num, line in content.items():
            if search_str in line:
                start = max(1, line_num - SNIPPET_LINES)
                end = line_num + SNIPPET_LINES
                return file.format_text({k: v for k, v in content.items() if start <= k <= end})
        return ""
