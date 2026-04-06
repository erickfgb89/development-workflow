"""Terminal console UI utilities with prompt_toolkit."""
import os
import re
from pathlib import Path
from typing import Iterable, List

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.patch_stdout import patch_stdout


class AtMentionCompleter(Completer):
    def __init__(self, root_dir: str = "."):
        self.root_dir = Path(root_dir).resolve()
        
    def get_completions(self, document: Document, complete_event) -> Iterable[Completion]:
        text_before_cursor = document.text_before_cursor
        # Look for the last '@' followed by non-whitespace characters
        match = re.search(r'@([^\s]*)$', text_before_cursor)
        if not match:
            return
            
        search_term = match.group(1)
        if len(search_term) < 3:
            return
            
        search_term_lower = search_term.lower()
        for file_path in self._get_all_files():
            if search_term_lower in file_path.lower():
                yield Completion(
                    file_path,
                    start_position=-len(search_term),
                    display=file_path
                )

    def _get_all_files(self) -> List[str]:
        # Consider caching this for large codebases if needed
        files = []
        for root, dirs, filenames in os.walk(self.root_dir):
            if '.git' in dirs:
                dirs.remove('.git')
            if '.venv' in dirs:
                dirs.remove('.venv')
            if '__pycache__' in dirs:
                dirs.remove('__pycache__')
                
            for filename in filenames:
                if filename.startswith('.'):
                    continue
                full_path = Path(root) / filename
                rel_path = full_path.relative_to(self.root_dir)
                files.append(str(rel_path))
        return files


_global_session = None

def get_prompt_session(root_dir: str = None) -> PromptSession:
    """Returns a singleton PromptSession configured with the AtMentionCompleter."""
    global _global_session
    if _global_session is None:
        target_dir = root_dir if root_dir else os.getcwd()
        _global_session = PromptSession(completer=AtMentionCompleter(target_dir))
    return _global_session

def patch_console():
    """Context manager to avoid scramble for background outputs."""
    return patch_stdout()
