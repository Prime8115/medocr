"""The job queue on real Postgres - what production runs.

The rest of the suite uses SQLite, which cannot show what matters most here:
many connections racing for the same row at once, timestamptz comparisons,
the partial unique index, and the migrations themselves. These run against a
real server and are skipped unless TEST_POSTGRES_URL points at an empty
database (CI provides one; see .github/workflows/ci.yml).
"""
import os
import threading
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

PG_URL = os.environ.get("TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="set TEST_POSTGRES_URL to run the Postgres tests")

RACERS = 20


def _alembic(url):
    from alembic.config import Config

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(backend, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend, "alembic"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="module")
def pg():
    from alembic import command

    cfg = _alembic(PG_URL)
    command.upgrade(cfg, "head")
    engine = create_engine(PG_URL, pool_size=RACERS + 5, max_overflow=0)
    yield engine
    engine.dispose()
    command.downgrade(cfg, "base")


@pytest.fixture
def Session(pg):
    yield sessionmaker(bind=pg, autoflush=False)
    with pg.begin() as conn:
        conn.execute(text("DELETE FROM ocr_jobs; DELETE FROM documents; DELETE FROM shops;"))


@pytest.fixture
def doc_id(Session):
    from app.models.document import Document
    from app.models.shop import Shop

    s = Session()
    try:
        shop = Shop(name="Shop A")
        s.add(shop)
        s.flush()
        doc = Document(shop_id=shop.id, doc_type="invoice", status="queued", image_ref="local://x.pdf")
        s.add(doc)
        s.commit()
        return doc.id
    finally:
        s.close()


def _race(fn):
    """Run fn(i) in RACERS threads released at the same instant."""
    gate = threading.Barrier(RACERS)
    results = [None] * RACERS

    def run(i):
        gate.wait()
        results[i] = fn(i)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(RACERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_migrations_build_the_job_table_on_postgres(pg):
    insp = inspect(pg)
    assert "ocr_jobs" in insp.get_table_names()
    cols = {c["name"]: c for c in insp.get_columns("ocr_jobs")}
    assert {"locked_by", "locked_at", "attempts", "busy_attempts", "run_after"} <= set(cols)
    assert cols["run_after"]["type"].timezone is True  # timestamptz, not a naive timestamp
    assert "requested_doc_type" in {c["name"] for c in insp.get_columns("documents")}


def test_exactly_one_of_many_racing_claims_wins(Session, doc_id):
    from app.services import jobs

    s = Session()
    job_id = jobs.enqueue(s, doc_id, "application/pdf", None).id
    s.commit()
    s.close()

    def claim(_i):
        db = Session()
        try:
            return jobs.claim(db, job_id)
        finally:
            jobs.let_go(job_id)
            db.close()

    assert sum(_race(claim)) == 1


def test_many_racing_enqueues_make_exactly_one_job(Session, doc_id):
    from app.models.job import OcrJob
    from app.services import jobs

    def enqueue(_i):
        db = Session()
        try:
            job = jobs.enqueue(db, doc_id, "application/pdf", None)
            db.commit()
            return job.id if job else None
        finally:
            db.close()

    ids = _race(enqueue)
    s = Session()
    try:
        assert s.query(OcrJob).count() == 1
        assert len(set(ids)) == 1 and ids[0] is not None  # everyone got the same job
    finally:
        s.close()


def test_the_database_refuses_a_second_active_job(Session, doc_id):
    from app.models.job import OcrJob
    from app.services import jobs

    s = Session()
    try:
        jobs.enqueue(s, doc_id, "application/pdf", None)
        s.commit()
        s.add(OcrJob(document_id=doc_id, status="running", content_type="x",
                     run_after=datetime.now(timezone.utc), busy_attempts=0, attempts=0))
        with pytest.raises(IntegrityError):
            s.commit()
    finally:
        s.rollback()
        s.close()


def test_leases_and_fencing_on_postgres(Session, doc_id):
    from app.models.job import OcrJob
    from app.services import jobs

    s = Session()
    try:
        job_id = jobs.enqueue(s, doc_id, "application/pdf", None).id
        s.commit()
        assert jobs.claim(s, job_id)
        assert jobs.release_expired(s, 90) == 0
        assert jobs.heartbeat(s, [job_id]) == 1
        later = datetime.now(timezone.utc) + timedelta(seconds=91)
        assert jobs.release_expired(s, 90, now=later) == 1

        # Another process takes it; our late writes are refused.
        s.execute(jobs._update(OcrJob.id == job_id).values(status="running", locked_by="other:1:b"))
        s.commit()
        assert jobs.finish(s, job_id, ok=True) is False
        s.commit()
        s.expire_all()
        assert s.get(OcrJob, job_id).locked_by == "other:1:b"
    finally:
        jobs.let_go(job_id)
        s.close()
