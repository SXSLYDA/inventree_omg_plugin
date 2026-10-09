"""
Background harness import - the work HarnessImportView used to do inside
the web request, run by InvenTree's background worker instead.

Flow:
  1. HarnessImportView (api.py) validates the request, creates a
     HarnessImportJob (status "queued") and offloads
     run_harness_import_job(job_id) with InvenTree's offload_task().
     It returns 202 with the job id straight away.
  2. The worker runs this module: fetch the BOM from OMG, import it
     (harness_import.import_or_update_harness_bom - unchanged), report
     back to OMG (reconciliation.push_reconciliation_to_omg - unchanged),
     updating job.progress at each stage.
  3. The panel polls HarnessImportJobView until status is done/failed.

If InvenTree's worker isn't running, offload_task() runs this
synchronously inside the request instead - the job is then already
finished when the endpoint returns, and the panel's first poll sees it.

Takes ids, not objects: background tasks are serialised by the worker
queue, so a request or user object can't be passed in.
"""
import logging

import requests
from django.utils import timezone

logger = logging.getLogger(__name__)

TASK_PATH = "omg_import_plugin.harness_import_job.run_harness_import_job"
OMG_BOM_TIMEOUT = 60   # seconds - plenty now this isn't racing the web request timeout


def _update(job, **fields):
    for name, value in fields.items():
        setattr(job, name, value)
    job.save(update_fields=list(fields))


def run_harness_import_job(job_id):
    """Run one queued HarnessImportJob. Never raises - failures are recorded on the job."""
    from plugin.registry import registry

    from .harness_import import import_or_update_harness_bom
    from .models import HarnessImportJob
    from .omg_credentials import get_omg_credentials
    from .reconciliation import push_reconciliation_to_omg

    job = HarnessImportJob.objects.filter(pk=job_id).select_related("user").first()
    if job is None:
        logger.warning("Harness import job %s no longer exists", job_id)
        return
    # Only ever run a queued job once. If the worker retries a task (e.g.
    # after it was killed for running past the background timeout), the
    # job is already "running" and this skips it rather than importing twice.
    if job.status != HarnessImportJob.Status.QUEUED:
        logger.info("Harness import job %s is %s - not running it again", job_id, job.status)
        return

    _update(job, status=HarnessImportJob.Status.RUNNING, started_at=timezone.now(),
            progress="Fetching the BOM from OMG")
    try:
        plugin = registry.get_plugin("omg-harness-import")
        omg_base_url, omg_token = get_omg_credentials(job.user, plugin=plugin)
        if not omg_base_url or not omg_token:
            raise _JobFailed("OMG Harness credentials aren't configured - set them in plugin settings, "
                             "or ask an admin to set up your personal OMG credential.")

        try:
            # inventree_pk lets OMG find the harness through its link to this
            # InvenTree part even after the harness was renamed in OMG.
            resp = requests.get(
                f"{omg_base_url.rstrip('/')}/api/harness/{job.harness_part_number}/inventree-bom/",
                headers={"Authorization": f"Token {omg_token}"},
                params={"inventree_pk": job.target_part_pk} if job.target_part_pk else None,
                timeout=OMG_BOM_TIMEOUT,
            )
            resp.raise_for_status()
            omg_bom_data = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise _JobFailed(f"Could not reach OMG Harness to fetch BOM data: {exc}")

        _update(job, progress="Updating the BOM in InvenTree")
        try:
            batch, resolved_matches = import_or_update_harness_bom(
                job.harness_part_number, omg_bom_data,
                category_pk=job.category_pk, target_part_pk=job.target_part_pk,
            )
        except ValueError as exc:   # the import's own "can't do this" messages
            raise _JobFailed(str(exc))

        _update(job, batch=batch, progress="Reporting the result back to OMG")
        pushed = push_reconciliation_to_omg(batch, resolved_matches=resolved_matches)

        _update(job, status=HarnessImportJob.Status.DONE, reconciliation_pushed=pushed,
                progress="", finished_at=timezone.now())
    except _JobFailed as exc:
        _update(job, status=HarnessImportJob.Status.FAILED, error=str(exc), progress="",
                finished_at=timezone.now())
    except Exception as exc:   # anything unexpected: record it, and keep the traceback in the log
        logger.exception("Harness import job %s failed", job_id)
        try:
            from InvenTree.exceptions import log_error
            log_error("omg_import_plugin.harness_import_job")
        except Exception:
            pass
        _update(job, status=HarnessImportJob.Status.FAILED, progress="", finished_at=timezone.now(),
                error=f"Unexpected error during the import: {exc}. Details are in InvenTree's error log.")


class _JobFailed(Exception):
    """An expected failure with a message fit to show the user as-is."""
