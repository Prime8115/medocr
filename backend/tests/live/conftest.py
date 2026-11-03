"""Each failing live test named as a GitHub annotation, so the run's page (and
the public API) says which test failed and why - the full log needs a login."""
import os


def pytest_runtest_logreport(report):
    if not os.environ.get("GITHUB_ACTIONS") or report.passed or report.skipped:
        return
    if report.when != "call" and not report.failed:
        return
    message = " ".join(str(report.longrepr).split())[-400:] if report.longrepr else "failed"
    print(f"\n::error title=Live test failed: {report.nodeid}::{message}", flush=True)
