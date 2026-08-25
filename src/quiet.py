"""Suppress noisy Blender operator and add-on console output."""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterator


@contextlib.contextmanager
def suppress_output() -> Iterator[None]:
    """Redirect Python and native stdout/stderr writes to the null device."""

    streams = (sys.stdout, sys.stderr)
    saved_descriptors: list[tuple[int, int]] = []

    for stream in streams:
        try:
            stream.flush()
        except (AttributeError, OSError):
            pass

    with open(os.devnull, "w", encoding="utf-8") as sink:
        try:
            for stream in streams:
                try:
                    descriptor = stream.fileno()
                    saved_descriptors.append((descriptor, os.dup(descriptor)))
                    os.dup2(sink.fileno(), descriptor)
                except (AttributeError, OSError):
                    continue

            with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
                yield
        finally:
            for descriptor, saved in reversed(saved_descriptors):
                try:
                    os.dup2(saved, descriptor)
                finally:
                    os.close(saved)
