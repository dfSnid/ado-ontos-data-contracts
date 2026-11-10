"""Logging for the dpi command, with Azure DevOps annotations.

Records go to stderr as plain lines. Inside a pipeline (TF_BUILD=true) warnings and errors are
also written to stdout as ##vso[task.logissue] commands, so they show on the run summary and,
when the record names a file and line, on that line of the pull request:

    log.error("missing a named Data Steward", extra={"source": path, "line": 22})
"""
from __future__ import annotations

import logging
import os
import sys
from typing import TextIO

LOGGER_NAME = "dp_integration"


def in_azure_devops() -> bool:
    return os.environ.get("TF_BUILD", "").lower() == "true"


def _escape(text: str) -> str:
    """Escape a value for an Azure DevOps logging command."""
    return text.replace("%", "%AZP25").replace("\r", "%0D").replace("\n", "%0A").replace("]", "%5D")


class SourceFormatter(logging.Formatter):
    """'LEVEL source:line: message', with the source part only when the record has one."""

    def format(self, record: logging.LogRecord) -> str:
        where = getattr(record, "source", None)
        if where and getattr(record, "line", None):
            where = f"{where}:{record.line}"
        record.where = f"{where}: " if where else ""
        return super().format(record)


class AzureDevOpsIssueHandler(logging.Handler):
    """Turns WARNING and ERROR records into ##vso[task.logissue] commands."""

    def __init__(self, stream: TextIO | None = None):
        super().__init__(level=logging.WARNING)
        self.stream = stream

    def emit(self, record: logging.LogRecord) -> None:
        try:
            props = ["type=error" if record.levelno >= logging.ERROR else "type=warning"]
            if getattr(record, "source", None):
                props.append(f"sourcepath={_escape(str(record.source)).replace(';', '%3B')}")
            if getattr(record, "line", None):
                props.append(f"linenumber={int(record.line)}")
            stream = self.stream or sys.stdout
            stream.write(f"##vso[task.logissue {';'.join(props)}]{_escape(record.getMessage())}\n")
            stream.flush()
        except Exception:  # never let logging break the run
            self.handleError(record)


def configure(level: str = "INFO", annotate: bool | None = None,
              stream: TextIO | None = None) -> logging.Logger:
    """Set up the package logger once per process. Safe to call again (replaces its handlers)."""
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
    console = logging.StreamHandler(stream or sys.stderr)
    console.setFormatter(SourceFormatter("%(levelname)-7s %(where)s%(message)s"))
    logger.addHandler(console)
    if in_azure_devops() if annotate is None else annotate:
        logger.addHandler(AzureDevOpsIssueHandler())
    logger.setLevel(level)
    logger.propagate = False
    return logger
