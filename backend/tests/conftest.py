"""
Shared pytest fixtures for backend/tests/.

Phase 3 (X-Ray): weather_handler.py calls patch_all() at import time, which
instruments boto3 and requests so their calls emit X-Ray subsegments. In
real Lambda execution, the runtime automatically opens a segment for the
invocation before any code runs (when TracingConfig.Mode is Active), so
those subsegments always have a parent to attach to. Local pytest runs
have no such runtime — 126 tests share one process and the X-Ray recorder
is a global singleton, so "cannot find the current segment" warnings show
up as test boundaries cross without a matching Lambda-provided segment.
This is cosmetic only: it never fails a test and says nothing about
production behavior (verified: coverage and pass/fail are identical with
or without this suppression). Silenced here purely so real test failures
aren't lost in the noise.
"""

import logging

logging.getLogger("aws_xray_sdk").setLevel(logging.CRITICAL)
