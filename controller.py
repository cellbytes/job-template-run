import base64
import copy
import hashlib
import logging
import os
from typing import NoReturn

import httpx
import kopf
from kubernetes import client, config
from kubernetes.client.exceptions import ApiException


logger = logging.getLogger("kopf.controller")

# Kubernetes label values are capped at 63 characters, and Job names must also
# fit in 63 characters because the job controller copies the name into the
# batch.kubernetes.io/job-name label on the pods it creates.
LABEL_VALUE_MAX = 63
JOB_NAME_MAX = 63


@kopf.on.login()
def login(**kwargs):
    # kopf's default client-piggybacking login reads the API token from the
    # kubernetes client's api_key['authorization'], but kubernetes>=33 stores it
    # under api_key['BearerToken']. That mismatch makes kopf's watch/patch stream
    # fall back to anonymous, 403-rejected requests, so nothing reconciles. Read
    # the in-cluster service-account token directly to stay independent of the
    # client's key naming, falling back to a kubeconfig for local runs.
    return kopf.login_with_service_account(**kwargs) or kopf.login_with_kubeconfig(
        **kwargs
    )


def get_template(namespace, name):
    crd_api = client.CustomObjectsApi()
    return crd_api.get_namespaced_custom_object(
        group="cellbytes.io",
        version="v1",
        namespace=namespace,
        plural="jobtemplates",
        name=name,
    )


def label_value(value):
    return value[:LABEL_VALUE_MAX]


def job_name_for(template_name, run_name):
    # Deterministic Job name so that creation is idempotent: a concurrent or
    # repeated reconcile gets a 409 instead of spawning a duplicate Job. Long
    # names are truncated with a stable digest suffix to stay unique.
    base = f"{template_name}-{run_name}"
    if len(base) <= JOB_NAME_MAX:
        return base
    digest = hashlib.sha256(base.encode()).hexdigest()[:8]
    return f"{base[: JOB_NAME_MAX - 9]}-{digest}"


def build_labels(template_name, run_name):
    env_labels = {
        "app.kubernetes.io/name": os.getenv("APP_NAME"),
        "app.kubernetes.io/instance": os.getenv("APP_INSTANCE"),
        "app.kubernetes.io/version": os.getenv("APP_VERSION"),
        "app.kubernetes.io/managed-by": os.getenv("APP_MANAGED_BY"),
    }
    labels = {key: value for key, value in env_labels.items() if value}
    labels["cellbytes.io/job-template"] = label_value(template_name)
    labels["cellbytes.io/job-run"] = label_value(run_name)
    return labels


def create_job(
    name,
    namespace,
    template,
    command=None,
    args=None,
    callback_url=None,
    callback_token=None,
    callback_token_secret=None,
    owner=None,
):
    template_name = template["metadata"]["name"]
    job_spec = copy.deepcopy(template.get("spec"))

    try:
        container = job_spec["template"]["spec"]["containers"][0]
    except (KeyError, IndexError, TypeError):
        raise ValueError(
            f"JobTemplate '{template_name}' has no"
            " .spec.template.spec.containers[0] to run"
        )
    if command:
        container["command"] = command
    if args:
        container["args"] = args

    labels = build_labels(template_name, name)
    job_spec["template"].setdefault("metadata", {}).setdefault("labels", {}).update(
        labels
    )

    # The label value is truncated to 63 characters, so keep the full JobRun
    # name in an annotation for the status-update path.
    annotations = {"cellbytes.io/job-run-name": name}
    if callback_url:
        annotations["cellbytes.io/callback-url"] = callback_url
    if callback_token:
        annotations["cellbytes.io/callback-token"] = callback_token
    if callback_token_secret:
        annotations["cellbytes.io/callback-token-secret-name"] = callback_token_secret[
            "name"
        ]
        annotations["cellbytes.io/callback-token-secret-key"] = callback_token_secret[
            "key"
        ]

    job_name = job_name_for(template_name, name)
    job_manifest = {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": job_name,
            "namespace": namespace,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": job_spec,
    }
    if owner is not None:
        # Owner reference to the JobRun: deleting the JobRun cascades to the
        # Job (and its pods).
        kopf.append_owner_reference(job_manifest, owner=owner)
    batch_v1 = client.BatchV1Api()
    # The client serializes a plain dict body; the stub only types V1Job.
    batch_v1.create_namespaced_job(namespace=namespace, body=job_manifest)  # ty: ignore[invalid-argument-type]
    return job_name


@kopf.on.startup()
def configure(settings, **_):
    # Load credentials for our own kubernetes client calls (the @kopf.on.login
    # handler only authenticates kopf's own watch/patch stream).
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()

    # kopf >=1.44 detects silently dropped watch connections behind cloud load
    # balancers (where the operator<->LB socket stays open while the LB<->API
    # connection dies) by requesting bookmark heartbeats and reconnecting when
    # no event arrives within inactivity_timeout.
    settings.networking.request_timeout = 60
    settings.watching.inactivity_timeout = 70
    settings.watching.server_timeout = 600


INTERVAL = float(os.getenv("TIMER_INTERVAL", "30"))


def _fail_permanently(patch, message) -> NoReturn:
    patch.status["error"] = message
    patch.status["failed"] = 1
    raise kopf.PermanentError(message)


# The on.create handler reacts immediately; the timer is the reconcile backstop
# for events missed while the controller was down (or dropped by the watch).
@kopf.on.create("cellbytes.io", "v1", "jobruns")
@kopf.timer("cellbytes.io", "v1", "jobruns", interval=INTERVAL)
def jobrun_reconcile(spec, name, namespace, patch, status, body, **_):
    # One-shot semantics: once a Job has been recorded on the JobRun (or the
    # JobRun reached a terminal state), never create another Job, even if the
    # original Job has since been garbage-collected or deleted.
    if status and (
        status.get("jobName") or status.get("succeeded") or status.get("failed")
    ):
        return

    # Fallback for JobRuns created before status.jobName existed: adopt an
    # already-running labeled Job instead of creating a duplicate.
    batch_api = client.BatchV1Api()
    existing_jobs = batch_api.list_namespaced_job(
        namespace,
        label_selector=f"cellbytes.io/job-run={label_value(name)}",
    )
    for existing_job in existing_jobs.items:
        # The API server always names what it lists; the generated client
        # models metadata as optional anyway.
        if existing_job.metadata is not None:
            patch.status["jobName"] = existing_job.metadata.name
            return

    template_name = spec.get("templateRef")
    if not template_name:
        _fail_permanently(patch, "templateRef must be specified")

    callback_token_secret = spec.get("callbackTokenSecretRef")
    if callback_token_secret and not (
        callback_token_secret.get("name") and callback_token_secret.get("key")
    ):
        _fail_permanently(
            patch, "callbackTokenSecretRef must specify both name and key"
        )

    try:
        template = get_template(namespace, template_name)
    except ApiException as e:
        if e.status == 404:
            _fail_permanently(patch, f"JobTemplate '{template_name}' not found")
        raise

    try:
        job_name = create_job(
            name,
            namespace,
            template,
            command=spec.get("command"),
            args=spec.get("args"),
            callback_url=spec.get("callbackUrl"),
            callback_token=spec.get("callbackToken"),
            callback_token_secret=callback_token_secret,
            owner=body,
        )
    except ValueError as e:
        _fail_permanently(patch, str(e))
    except ApiException as e:
        if e.status == 409:
            # Already created by a concurrent handler; deterministic naming
            # makes this safe to record as ours.
            patch.status["jobName"] = job_name_for(template_name, name)
            return
        if e.status == 422:
            _fail_permanently(
                patch, f"Job creation rejected by the API server: {e.reason}"
            )
        raise
    patch.status["jobName"] = job_name


def resolve_callback_token(annotations, namespace):
    token = annotations.get("cellbytes.io/callback-token")
    if token:
        return token
    secret_name = annotations.get("cellbytes.io/callback-token-secret-name")
    secret_key = annotations.get("cellbytes.io/callback-token-secret-key")
    if not secret_name or not secret_key:
        return None
    core_api = client.CoreV1Api()
    secret = core_api.read_namespaced_secret(secret_name, namespace)
    data = secret.data or {}
    if secret_key not in data:
        raise ValueError(f"key '{secret_key}' not found in secret '{secret_name}'")
    return base64.b64decode(data[secret_key]).decode()


def terminal_job_condition(conditions):
    """Returns the Job condition that ended it, or None while it is still going.

    "Complete" outranks "Failed" for a Job that somehow carries both, matching
    the resolution kubectl shows.
    """
    by_type = {
        c.get("type"): c
        for c in conditions
        if c.get("status") == "True" and c.get("type") in ("Complete", "Failed")
    }
    return by_type.get("Complete") or by_type.get("Failed")


def callback_payload(jobrun_name, namespace, condition):
    """Builds the callback body describing how a Job ended.

    `reason` and `message` are the Job condition's own, forwarded verbatim so a
    receiver can tell a backoff limit from a deadline from a pod failure policy
    without holding Job read access itself. Kubernetes leaves either of them
    empty on some conditions, and an absent key says so rather than sending a
    null a receiver would have to special-case.
    """
    payload = {"name": jobrun_name, "namespace": namespace, "status": condition["type"]}
    for key in ("reason", "message"):
        value = condition.get(key)
        if value:
            payload[key] = value
    return payload


# The on.event handler reacts to Job status changes as they happen; the timer
# is the reconcile backstop (missed events, callback retries after a 5xx).
@kopf.on.event("batch", "v1", "jobs", labels={"cellbytes.io/job-run": kopf.PRESENT})
@kopf.timer(
    "batch",
    "v1",
    "jobs",
    interval=INTERVAL,
    labels={"cellbytes.io/job-run": kopf.PRESENT},
)
def job_status_update(name, namespace, status, meta, event=None, **_):
    if event and event.get("type") == "DELETED":
        return

    annotations = meta.get("annotations") or {}
    jobrun_name = annotations.get("cellbytes.io/job-run-name") or meta.get(
        "labels", {}
    ).get("cellbytes.io/job-run")
    if not jobrun_name or not namespace:
        return

    crd_api = client.CustomObjectsApi()
    try:
        jobrun = crd_api.get_namespaced_custom_object(
            group="cellbytes.io",
            version="v1",
            namespace=namespace,
            plural="jobruns",
            name=jobrun_name,
        )
    except ApiException as e:
        if e.status == 404:
            # The JobRun is gone; nothing to update or notify.
            return
        raise

    desired_status = {
        "startTime": status.get("startTime"),
        "completionTime": status.get("completionTime"),
        "conditions": status.get("conditions"),
        "active": status.get("active"),
        "succeeded": status.get("succeeded"),
        "failed": status.get("failed"),
    }
    current_status = jobrun.get("status") or {}
    changed = {
        key: value
        for key, value in desired_status.items()
        if current_status.get(key) != value
    }
    if changed:
        try:
            crd_api.patch_namespaced_custom_object_status(
                group="cellbytes.io",
                version="v1",
                namespace=namespace,
                plural="jobruns",
                name=jobrun_name,
                body={"status": changed},
            )
        except ApiException as e:
            logger.warning(f"Failed to update JobRun status: {e}")

    terminal_condition = terminal_job_condition(status.get("conditions") or [])
    callback_url = annotations.get("cellbytes.io/callback-url")
    callback_sent = annotations.get("cellbytes.io/callback-sent") == "true"

    if not callback_url or not terminal_condition or callback_sent:
        return

    try:
        callback_token = resolve_callback_token(annotations, namespace)
    except Exception as e:
        # The referenced Secret is missing or malformed. Leave the callback
        # unsent so a later tick retries once the Secret is fixed.
        logger.warning(f"Cannot resolve callback token for job {name}: {e}")
        return

    headers = {}
    if callback_token:
        headers["Authorization"] = f"Bearer {callback_token}"
    delivered = False
    try:
        response = httpx.post(
            callback_url,
            json=callback_payload(jobrun_name, namespace, terminal_condition),
            headers=headers,
            timeout=10,
        )
        # 2xx means accepted; 4xx means our request is malformed/unauthorized
        # and retrying will not help, so both count as delivered. Redirects are
        # not followed, so 3xx (like 5xx) is worth retrying.
        delivered = response.is_success or response.is_client_error
        if not delivered:
            logger.warning(
                f"Callback to {callback_url} returned {response.status_code}, "
                "will retry on next tick"
            )
    except Exception as e:
        # Network-level failure (app rolling out, DNS blip, timeout). Leave the
        # callback unsent so the next timer tick retries it.
        logger.warning(f"Failed to send callback to {callback_url}: {e}")

    # Only mark sent once the callback was actually delivered. Retries are
    # naturally bounded by the job's ttlSecondsAfterFinished: once the job is
    # garbage-collected the timer stops firing for it.
    if delivered:
        batch_api = client.BatchV1Api()
        try:
            batch_api.patch_namespaced_job(
                name=name,
                namespace=namespace,
                body={
                    "metadata": {"annotations": {"cellbytes.io/callback-sent": "true"}}
                },
            )
        except ApiException as e:
            if e.status != 404:
                raise
