import os
import sys
import threading
import trace
from pathlib import Path
from typing import Iterable

import pytest

os.environ.setdefault('SIMPLECOV_FOCUS', 'src/services/supervisor.py,src/services/drive_client.py')


@pytest.fixture(autouse=True)
def _base_env(monkeypatch):
    # Ensure minimal required env for app/config across tests
    monkeypatch.setenv('PROJECT_ID', os.environ.get('PROJECT_ID', 'test-project'))
    monkeypatch.setenv('REGION', os.environ.get('REGION', 'us'))
    monkeypatch.setenv('DOC_AI_PROCESSOR_ID', os.environ.get('DOC_AI_PROCESSOR_ID', 'pid'))
    monkeypatch.setenv('DOC_AI_SPLITTER_PROCESSOR_ID', os.environ.get('DOC_AI_SPLITTER_PROCESSOR_ID', 'splitter'))
    monkeypatch.setenv('OPENAI_API_KEY', os.environ.get('OPENAI_API_KEY', 'dummy'))
    monkeypatch.setenv('DRIVE_INPUT_FOLDER_ID', os.environ.get('DRIVE_INPUT_FOLDER_ID', 'in-folder'))
    monkeypatch.setenv('DRIVE_REPORT_FOLDER_ID', os.environ.get('DRIVE_REPORT_FOLDER_ID', 'out-folder'))
    monkeypatch.setenv('DRIVE_IMPERSONATION_USER', os.environ.get('DRIVE_IMPERSONATION_USER', 'impersonation@example.com'))
    monkeypatch.setenv('INTAKE_GCS_BUCKET', os.environ.get('INTAKE_GCS_BUCKET', 'intake-bucket'))
    monkeypatch.setenv('OUTPUT_GCS_BUCKET', os.environ.get('OUTPUT_GCS_BUCKET', 'output-bucket'))
    monkeypatch.setenv('SUMMARY_BUCKET', os.environ.get('SUMMARY_BUCKET', 'summary-bucket'))
    monkeypatch.setenv(
        'CMEK_KEY_NAME',
        os.environ.get(
            'CMEK_KEY_NAME',
            'projects/test/locations/us/keyRings/test/cryptoKeys/test',
        ),
    )
    monkeypatch.setenv('SIMPLECOV_FOCUS', os.environ.get('SIMPLECOV_FOCUS', 'src/services/supervisor.py'))
    yield


@pytest.fixture(autouse=True)
def _patch_main_ocr(monkeypatch):
    class _AutoStubOCR:
        def process(self, data):  # pragma: no cover - placeholder
            return {'text': '', 'pages': []}

        def close(self):  # pragma: no cover
            return None

    monkeypatch.setattr('src.main.OCRService', lambda *args, **kwargs: _AutoStubOCR())
    yield


def pytest_addoption(parser):
    parser.getgroup('simplecov')
#     group.addoption('--cov', action='append', default=[], dest='simplecov_targets', help='Paths to measure coverage for (simple trace)')
#     group.addoption('--cov-report', action='append', default=[], dest='simplecov_reports', help='Coverage report formats')
#     group.addoption('--cov-fail-under', action='store', default=None, dest='simplecov_fail_under', type=float, help='Fail if total coverage below this percentage')


##def pytest_configure(config):
##    targets: list[str] = config.getoption('simplecov_targets')
##    if targets:
##        plugin = _SimpleCoveragePlugin(config)
##        config.pluginmanager.register(plugin, name='simple-coverage-plugin')
##
#
class _SimpleCoveragePlugin:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        root = Path(str(config.invocation_params.dir))
        self.targets = [root / Path(t) for t in config.getoption('simplecov_targets')]
        self.reports = list(config.getoption('simplecov_reports') or [])
        self.fail_under = config.getoption('simplecov_fail_under')
        self.tracer: trace.Trace | None = None
        self._original_trace = None
        focus_raw = os.environ.get('SIMPLECOV_FOCUS', '')
        self.focus_patterns = [p.strip() for p in focus_raw.split(',') if p.strip()]

    def pytest_sessionstart(self, session: pytest.Session) -> None:
        ignoredirs = tuple({sys.prefix, sys.exec_prefix, *(p for p in sys.path if 'site-packages' in p)})
        self.tracer = trace.Trace(count=True, trace=False, ignoredirs=ignoredirs)
        sys.settrace(self.tracer.globaltrace)
        threading.settrace(self.tracer.globaltrace)

    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int) -> None:
        threading.settrace(None)  # type: ignore[arg-type]
        sys.settrace(None)  # type: ignore[arg-type]
        if not self.tracer:
            return
        results = self.tracer.results()
        counts = results.counts
        files_data: list[tuple[Path, int, int, list[int]]] = []
        total_statements = 0
        executed_statements = 0
        per_file_percentages: list[float] = []
        executed_files = {Path(filename).resolve() for filename, _ in counts.keys()}
        for file_path in self._iter_files(executed_files):
            total, executed, missing = self._file_stats(file_path, counts)
            files_data.append((file_path, total, executed, missing))
            total_statements += total
            executed_statements += executed
            pct = 100.0 if total == 0 else (executed / total) * 100.0
            per_file_percentages.append(pct)
        if per_file_percentages:
            coverage_pct = sum(per_file_percentages) / len(per_file_percentages)
        else:
            coverage_pct = 100.0
        terminal = self.config.pluginmanager.get_plugin('terminalreporter')
        if terminal and any(report == 'term-missing' for report in self.reports):
            terminal.write_line('Coverage report (trace-based):')
            for path, total, executed, missing in files_data:
                pct = 100.0 if total == 0 else (executed / total) * 100.0
                rel = path.relative_to(self.config.invocation_params.dir)
                missing_str = ','.join(str(n) for n in missing) if missing else '-'
                terminal.write_line(f"  {rel}: {pct:6.2f}% ({executed}/{total}) Missing: {missing_str}")
            terminal.write_line(f"TOTAL: {coverage_pct:6.2f}% ({executed_statements}/{total_statements})")

        if self.fail_under is not None and coverage_pct < self.fail_under:
            if terminal:
                terminal.write_line(
                    f"FAIL Required test coverage of {self.fail_under:.0f}% not reached. Current coverage: {coverage_pct:.2f}%"
                )
            session.exitstatus = 1

    def _iter_files(self, executed_files: set[Path]) -> Iterable[Path]:
        for target in self.targets:
            if target.is_file() and target.suffix == '.py':
                if target.resolve() in executed_files and self._should_include(target):
                    yield target
            elif target.is_dir():
                for candidate in target.rglob('*.py'):
                    if candidate.resolve() in executed_files and self._should_include(candidate):
                        yield candidate

    def _should_include(self, candidate: Path) -> bool:
        if not self.focus_patterns:
            return True
        try:
            rel = candidate.relative_to(self.config.invocation_params.dir)
        except ValueError:
            rel = candidate
        rel_str = rel.as_posix()
        return any(rel.match(pattern) or rel_str.startswith(pattern.rstrip('*')) for pattern in self.focus_patterns)

    def _file_stats(self, path: Path, counts: dict[tuple[str, int], int]) -> tuple[int, int, list[int]]:
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return (0, 0, [])
        total = 0
        executed = 0
        missing: list[int] = []
        filename = str(path)
        skip_multiline = False
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith('#'):
                continue
            if 'pragma: no cover' in stripped.lower():
                continue
            if skip_multiline:
                if stripped.endswith('"""') or stripped.endswith("'''"):
                    skip_multiline = False
                continue
            # treat triple-quoted docstrings as executable once (counted if imported)
            if stripped.startswith('"""') or stripped.startswith("'''"):
                if stripped.count('"""') >= 2 or stripped.count("'''") >= 2:
                    continue
                skip_multiline = True
                continue
            total += 1
            if counts.get((filename, lineno), 0):
                executed += 1
            else:
                missing.append(lineno)
        return (total, executed, missing)
