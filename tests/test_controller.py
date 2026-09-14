"""Unit tests for controller.py reconcile and callback logic."""

import base64
from collections.abc import Callable
from typing import cast
from unittest import mock

import kopf
import pytest
from kubernetes.client.exceptions import ApiException

import controller

# kopf's decorators re-type the handlers as protocols that demand every kwarg
# kopf itself would pass. The handlers take only the fields they act on and
# swallow the rest through `**_`, so the tests call them through these aliases.
jobrun_reconcile = cast(Callable[..., None], controller.jobrun_reconcile)
job_status_update = cast(Callable[..., None], controller.job_status_update)


MINIMAL_TEMPLATE = {
    "metadata": {"name": "my-template"},
    "spec": {
        "template": {
            "spec": {
                "containers": [
                    {"name": "busybox", "image": "busybox", "command": ["echo"]}
                ],
                "restartPolicy": "Never",
            }
        }
    },
}


def _created_body(mock_batch):
    return mock_batch.return_value.create_namespaced_job.call_args.kwargs["body"]


# ---------------------------------------------------------------------------
# create_job: manifest shape
# ---------------------------------------------------------------------------


def test_create_job_uses_deterministic_name():
    with mock.patch("controller.client.BatchV1Api") as mock_batch:
        job_name = controller.create_job(
            name="my-run", namespace="default", template=MINIMAL_TEMPLATE
        )
        body = _created_body(mock_batch)
        assert job_name == "my-template-my-run"
        assert body["metadata"]["name"] == "my-template-my-run"
        assert "generateName" not in body["metadata"]


def test_create_job_truncates_long_names_with_digest():
    long_run = "r" * 80
    with mock.patch("controller.client.BatchV1Api") as mock_batch:
        job_name = controller.create_job(
            name=long_run, namespace="default", template=MINIMAL_TEMPLATE
        )
        body = _created_body(mock_batch)
        assert len(job_name) == controller.JOB_NAME_MAX
        assert job_name == controller.job_name_for("my-template", long_run)
        # Truncated names stay deterministic and unique per (template, run).
        other = controller.job_name_for("my-template", "r" * 81)
        assert other != job_name
        assert len(body["metadata"]["labels"]["cellbytes.io/job-run"]) <= 63


def test_create_job_skips_unset_env_labels():
    with (
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch.dict("os.environ", {"APP_NAME": "jtr"}, clear=True),
    ):
        controller.create_job(
            name="my-run", namespace="default", template=MINIMAL_TEMPLATE
        )
        labels = _created_body(mock_batch)["metadata"]["labels"]
        assert labels["app.kubernetes.io/name"] == "jtr"
        assert "app.kubernetes.io/instance" not in labels
        assert None not in labels.values()


def test_create_job_rejects_template_without_containers():
    with (
        mock.patch("controller.client.BatchV1Api"),
        pytest.raises(ValueError, match="containers"),
    ):
        controller.create_job(
            name="my-run",
            namespace="default",
            template={"metadata": {"name": "broken"}, "spec": {}},
        )


def test_create_job_does_not_mutate_template():
    template = {
        "metadata": {"name": "my-template"},
        "spec": {
            "template": {
                "spec": {
                    "containers": [{"name": "busybox", "image": "busybox"}],
                    "restartPolicy": "Never",
                }
            }
        },
    }
    with mock.patch("controller.client.BatchV1Api"):
        controller.create_job(
            name="my-run",
            namespace="default",
            template=template,
            command=["sh", "-c"],
            args=["echo overridden"],
        )
        container = template["spec"]["template"]["spec"]["containers"][0]
        assert "command" not in container
        assert "args" not in container


def test_create_job_appends_owner_reference():
    owner = mock.sentinel.owner
    with (
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch("controller.kopf.append_owner_reference") as mock_adopt,
    ):
        controller.create_job(
            name="my-run", namespace="default", template=MINIMAL_TEMPLATE, owner=owner
        )
        mock_adopt.assert_called_once_with(_created_body(mock_batch), owner=owner)


# ---------------------------------------------------------------------------
# create_job: callback URL/token annotations
# ---------------------------------------------------------------------------


def test_create_job_sets_callback_url_annotation():
    with mock.patch("controller.client.BatchV1Api") as mock_batch:
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
            callback_url="https://example.com/cb",
        )
        annotations = _created_body(mock_batch)["metadata"]["annotations"]
        assert annotations["cellbytes.io/callback-url"] == "https://example.com/cb"
        assert "cellbytes.io/callback-token" not in annotations


def test_create_job_sets_callback_token_annotation():
    with mock.patch("controller.client.BatchV1Api") as mock_batch:
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
            callback_url="https://example.com/cb",
            callback_token="tok123",
        )
        annotations = _created_body(mock_batch)["metadata"]["annotations"]
        assert annotations["cellbytes.io/callback-url"] == "https://example.com/cb"
        assert annotations["cellbytes.io/callback-token"] == "tok123"


def test_create_job_sets_callback_token_secret_annotations():
    with mock.patch("controller.client.BatchV1Api") as mock_batch:
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
            callback_url="https://example.com/cb",
            callback_token_secret={"name": "cb-secret", "key": "token"},
        )
        annotations = _created_body(mock_batch)["metadata"]["annotations"]
        assert annotations["cellbytes.io/callback-token-secret-name"] == "cb-secret"
        assert annotations["cellbytes.io/callback-token-secret-key"] == "token"
        assert "cellbytes.io/callback-token" not in annotations


def test_create_job_without_callback_only_records_run_name():
    with mock.patch("controller.client.BatchV1Api") as mock_batch:
        controller.create_job(
            name="my-run",
            namespace="default",
            template=MINIMAL_TEMPLATE,
        )
        annotations = _created_body(mock_batch)["metadata"]["annotations"]
        assert annotations == {"cellbytes.io/job-run-name": "my-run"}


# ---------------------------------------------------------------------------
# jobrun_reconcile
# ---------------------------------------------------------------------------


def _make_patch():
    patch = mock.Mock()
    patch.status = {}
    return patch


def _call_reconcile(*, spec, status=None, patch=None, existing_jobs=(), template=None):
    patch = patch if patch is not None else _make_patch()
    with (
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
    ):
        job_list = mock.Mock()
        job_list.items = list(existing_jobs)
        mock_batch.return_value.list_namespaced_job.return_value = job_list
        if isinstance(template, Exception):
            mock_crd.return_value.get_namespaced_custom_object.side_effect = template
        else:
            mock_crd.return_value.get_namespaced_custom_object.return_value = template
        jobrun_reconcile(
            spec=spec,
            name="my-run",
            namespace="default",
            patch=patch,
            status=status or {},
            body=None,
        )
        return patch, mock_batch


def test_reconcile_creates_job_and_records_job_name():
    patch, mock_batch = _call_reconcile(
        spec={"templateRef": "my-template"}, template=MINIMAL_TEMPLATE
    )
    mock_batch.return_value.create_namespaced_job.assert_called_once()
    assert patch.status["jobName"] == "my-template-my-run"


def test_reconcile_skips_when_job_name_recorded():
    _patch, mock_batch = _call_reconcile(
        spec={"templateRef": "my-template"},
        status={"jobName": "my-template-my-run"},
        template=MINIMAL_TEMPLATE,
    )
    mock_batch.return_value.list_namespaced_job.assert_not_called()
    mock_batch.return_value.create_namespaced_job.assert_not_called()


def test_reconcile_skips_when_terminal():
    for terminal in ({"succeeded": 1}, {"failed": 1}):
        _patch, mock_batch = _call_reconcile(
            spec={"templateRef": "my-template"},
            status=terminal,
            template=MINIMAL_TEMPLATE,
        )
        mock_batch.return_value.create_namespaced_job.assert_not_called()


def test_reconcile_adopts_existing_labeled_job():
    existing = mock.Mock()
    existing.metadata.name = "legacy-job-abc12"
    patch, mock_batch = _call_reconcile(
        spec={"templateRef": "my-template"},
        existing_jobs=[existing],
        template=MINIMAL_TEMPLATE,
    )
    mock_batch.return_value.create_namespaced_job.assert_not_called()
    assert patch.status["jobName"] == "legacy-job-abc12"


def test_reconcile_fails_permanently_without_template_ref():
    patch = _make_patch()
    with pytest.raises(kopf.PermanentError):
        _call_reconcile(spec={}, patch=patch)
    assert patch.status["failed"] == 1
    assert "templateRef" in patch.status["error"]


def test_reconcile_fails_permanently_on_missing_template():
    patch = _make_patch()
    with pytest.raises(kopf.PermanentError):
        _call_reconcile(
            spec={"templateRef": "does-not-exist"},
            patch=patch,
            template=ApiException(status=404, reason="Not Found"),
        )
    assert patch.status["failed"] == 1
    assert "not found" in patch.status["error"]


def test_reconcile_fails_permanently_on_malformed_template():
    patch = _make_patch()
    with pytest.raises(kopf.PermanentError):
        _call_reconcile(
            spec={"templateRef": "broken"},
            patch=patch,
            template={"metadata": {"name": "broken"}, "spec": {}},
        )
    assert patch.status["failed"] == 1
    assert "containers" in patch.status["error"]


def test_reconcile_fails_permanently_on_incomplete_secret_ref():
    patch = _make_patch()
    with pytest.raises(kopf.PermanentError):
        _call_reconcile(
            spec={
                "templateRef": "my-template",
                "callbackTokenSecretRef": {"name": "cb-secret"},
            },
            patch=patch,
            template=MINIMAL_TEMPLATE,
        )
    assert patch.status["failed"] == 1


def test_reconcile_treats_conflict_as_created():
    patch = _make_patch()
    with (
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
    ):
        job_list = mock.Mock()
        job_list.items = []
        mock_batch.return_value.list_namespaced_job.return_value = job_list
        mock_crd.return_value.get_namespaced_custom_object.return_value = (
            MINIMAL_TEMPLATE
        )
        mock_batch.return_value.create_namespaced_job.side_effect = ApiException(
            status=409, reason="Conflict"
        )
        jobrun_reconcile(
            spec={"templateRef": "my-template"},
            name="my-run",
            namespace="default",
            patch=patch,
            status={},
            body=None,
        )
    assert patch.status["jobName"] == "my-template-my-run"


# ---------------------------------------------------------------------------
# Helpers for job_status_update tests
# ---------------------------------------------------------------------------


def _make_complete_conditions():
    return [{"type": "Complete", "status": "True"}]


def _make_failed_conditions(reason=None, message=None):
    condition = {"type": "Failed", "status": "True"}
    if reason is not None:
        condition["reason"] = reason
    if message is not None:
        condition["message"] = message
    return [condition]


def _call_status_update(
    *,
    conditions,
    callback_url=None,
    callback_token=None,
    callback_sent=None,
    extra_annotations=None,
    jobrun_name="my-run",
    jobrun_status=None,
    name="my-job",
    namespace="default",
    event=None,
):
    annotations = {"cellbytes.io/job-run-name": jobrun_name}
    if callback_url is not None:
        annotations["cellbytes.io/callback-url"] = callback_url
    if callback_token is not None:
        annotations["cellbytes.io/callback-token"] = callback_token
    if callback_sent is not None:
        annotations["cellbytes.io/callback-sent"] = callback_sent
    annotations.update(extra_annotations or {})

    meta = {
        "labels": {"cellbytes.io/job-run": jobrun_name},
        "namespace": namespace,
        "annotations": annotations,
    }
    status = {"conditions": conditions}

    job_status_update(
        name=name, namespace=namespace, status=status, meta=meta, event=event
    )


def _mock_jobrun_get(mock_crd, jobrun_status=None):
    if isinstance(jobrun_status, Exception):
        mock_crd.return_value.get_namespaced_custom_object.side_effect = jobrun_status
    else:
        mock_crd.return_value.get_namespaced_custom_object.return_value = {
            "status": jobrun_status or {}
        }


def _ok_response(status_code=204):
    """Returns a real httpx-like response mock with the given status code."""
    response = mock.Mock()
    response.status_code = status_code
    response.is_success = 200 <= status_code < 300
    response.is_client_error = 400 <= status_code < 500
    return response


# ---------------------------------------------------------------------------
# job_status_update: status propagation
# ---------------------------------------------------------------------------


def test_status_patched_only_with_changed_fields():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post", return_value=_ok_response()),
    ):
        _mock_jobrun_get(mock_crd, {"startTime": "t0", "active": 1})
        _call_status_update(conditions=None)
        mock_patch = mock_crd.return_value.patch_namespaced_custom_object_status
        mock_patch.assert_called_once()
        body = mock_patch.call_args.kwargs["body"]
        # startTime differs (None vs t0) and active differs (None vs 1);
        # conditions/succeeded/failed/completionTime are unchanged (None).
        assert body == {"status": {"startTime": None, "active": None}}


def test_status_not_patched_when_unchanged():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
    ):
        _mock_jobrun_get(mock_crd, {"conditions": _make_complete_conditions()})
        _call_status_update(conditions=_make_complete_conditions())
        mock_crd.return_value.patch_namespaced_custom_object_status.assert_not_called()


def test_status_update_skipped_when_jobrun_deleted():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _mock_jobrun_get(mock_crd, ApiException(status=404, reason="Not Found"))
        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
        )
        mock_crd.return_value.patch_namespaced_custom_object_status.assert_not_called()
        mock_post.assert_not_called()


def test_status_update_skipped_on_deleted_event():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            event={"type": "DELETED"},
        )
        mock_crd.return_value.get_namespaced_custom_object.assert_not_called()
        mock_post.assert_not_called()


# ---------------------------------------------------------------------------
# job_status_update: callback firing
# ---------------------------------------------------------------------------


def test_callback_fired_on_complete_with_token():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch("controller.httpx.post", return_value=_ok_response()) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            callback_token="bearer-tok",
        )

        mock_post.assert_called_once_with(
            "https://example.com/cb",
            json={"name": "my-run", "namespace": "default", "status": "Complete"},
            headers={"Authorization": "Bearer bearer-tok"},
            timeout=10,
        )
        mock_patch_job.assert_called_once_with(
            name="my-job",
            namespace="default",
            body={"metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}},
        )


def test_callback_fired_on_failed_without_token():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch("controller.httpx.post", return_value=_ok_response()) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_status_update(
            conditions=_make_failed_conditions(),
            callback_url="https://example.com/cb",
        )

        mock_post.assert_called_once_with(
            "https://example.com/cb",
            json={"name": "my-run", "namespace": "default", "status": "Failed"},
            headers={},
            timeout=10,
        )
        mock_patch_job.assert_called_once()


def test_callback_forwards_condition_reason_and_message():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post", return_value=_ok_response()) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)

        _call_status_update(
            conditions=_make_failed_conditions(
                reason="BackoffLimitExceeded",
                message="Job has reached the specified backoff limit",
            ),
            callback_url="https://example.com/cb",
        )

        assert mock_post.call_args.kwargs["json"] == {
            "name": "my-run",
            "namespace": "default",
            "status": "Failed",
            "reason": "BackoffLimitExceeded",
            "message": "Job has reached the specified backoff limit",
        }


def test_callback_omits_empty_condition_reason():
    """A condition carrying no reason sends no key, rather than an empty one."""
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post", return_value=_ok_response()) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)

        _call_status_update(
            conditions=_make_failed_conditions(reason="", message="only a message"),
            callback_url="https://example.com/cb",
        )

        assert mock_post.call_args.kwargs["json"] == {
            "name": "my-run",
            "namespace": "default",
            "status": "Failed",
            "message": "only a message",
        }


def test_callback_prefers_complete_over_failed():
    """A Job carrying both terminal conditions reports the successful one."""
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post", return_value=_ok_response()) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)

        _call_status_update(
            conditions=_make_failed_conditions(reason="BackoffLimitExceeded")
            + _make_complete_conditions(),
            callback_url="https://example.com/cb",
        )

        assert mock_post.call_args.kwargs["json"] == {
            "name": "my-run",
            "namespace": "default",
            "status": "Complete",
        }


def test_callback_uses_token_from_secret():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.client.CoreV1Api") as mock_core,
        mock.patch("controller.httpx.post", return_value=_ok_response()) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        secret = mock.Mock()
        secret.data = {"token": base64.b64encode(b"from-secret").decode()}
        mock_core.return_value.read_namespaced_secret.return_value = secret

        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            extra_annotations={
                "cellbytes.io/callback-token-secret-name": "cb-secret",
                "cellbytes.io/callback-token-secret-key": "token",
            },
        )

        mock_core.return_value.read_namespaced_secret.assert_called_once_with(
            "cb-secret", "default"
        )
        assert (
            mock_post.call_args.kwargs["headers"]["Authorization"]
            == "Bearer from-secret"
        )


def test_callback_retried_when_secret_missing():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch("controller.client.CoreV1Api") as mock_core,
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_core.return_value.read_namespaced_secret.side_effect = ApiException(
            status=404, reason="Not Found"
        )

        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            extra_annotations={
                "cellbytes.io/callback-token-secret-name": "cb-secret",
                "cellbytes.io/callback-token-secret-key": "token",
            },
        )

        # No token, no callback, and no callback-sent annotation: the next tick
        # must retry once the secret exists.
        mock_post.assert_not_called()
        mock_batch.return_value.patch_namespaced_job.assert_not_called()


def test_callback_not_fired_when_already_sent():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://example.com/cb",
            callback_sent="true",
        )

        mock_post.assert_not_called()


def test_callback_not_fired_when_not_terminal():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        _call_status_update(
            conditions=[{"type": "Active", "status": "True"}],
            callback_url="https://example.com/cb",
        )

        mock_post.assert_not_called()


def test_callback_not_fired_when_no_url():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api"),
        mock.patch("controller.httpx.post") as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        _call_status_update(conditions=_make_complete_conditions())

        mock_post.assert_not_called()


def test_annotation_not_marked_sent_when_http_call_fails():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch(
            "controller.httpx.post", side_effect=Exception("connection refused")
        ) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_status_update(
            conditions=_make_complete_conditions(),
            callback_url="https://bad-url.example.com/cb",
        )

        mock_post.assert_called_once()
        # A network failure must leave the annotation unset so the next timer tick
        # retries the callback instead of dropping it.
        mock_patch_job.assert_not_called()


def test_annotation_not_marked_sent_on_5xx_response():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch(
            "controller.httpx.post", return_value=_ok_response(503)
        ) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_status_update(
            conditions=_make_failed_conditions(),
            callback_url="https://example.com/cb",
        )

        mock_post.assert_called_once()
        # A 5xx is transient; leave unsent so it retries.
        mock_patch_job.assert_not_called()


def test_annotation_not_marked_sent_on_3xx_response():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch(
            "controller.httpx.post", return_value=_ok_response(302)
        ) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_status_update(
            conditions=_make_failed_conditions(),
            callback_url="https://example.com/cb",
        )

        mock_post.assert_called_once()
        # Redirects are not followed, so the callback never reached the target;
        # leave unsent so it retries.
        mock_patch_job.assert_not_called()


def test_annotation_marked_sent_on_4xx_response():
    with (
        mock.patch("controller.client.CustomObjectsApi") as mock_crd,
        mock.patch("controller.client.BatchV1Api") as mock_batch,
        mock.patch(
            "controller.httpx.post", return_value=_ok_response(401)
        ) as mock_post,
    ):
        _mock_jobrun_get(mock_crd)
        mock_patch_job = mock_batch.return_value.patch_namespaced_job

        _call_status_update(
            conditions=_make_failed_conditions(),
            callback_url="https://example.com/cb",
        )

        mock_post.assert_called_once()
        # A 4xx means the request is malformed/unauthorized; retrying will not help,
        # so mark it sent to stop retrying.
        mock_patch_job.assert_called_once_with(
            name="my-job",
            namespace="default",
            body={"metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}},
        )
