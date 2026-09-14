# Makefile for local testing with kind, Helm, and Chainsaw

KIND_CLUSTER_NAME=job-template-run-test
NAMESPACE=job-template-run
HELM_CHART=charts/job-template-run
HELM_RELEASE=job-template-run
DOCKER_IMAGE=ghcr.io/cellbytes/job-template-run


.PHONY: kind kind-down lint helm-install helm-uninstall build test all clean dev-e2e

kind:
	kind create cluster --name $(KIND_CLUSTER_NAME)

kind-down:
	kind delete cluster --name $(KIND_CLUSTER_NAME)

build:
	docker build -t $(DOCKER_IMAGE):$$(git rev-parse --short HEAD) .
	kind load --name $(KIND_CLUSTER_NAME) docker-image $(DOCKER_IMAGE):$$(git rev-parse --short HEAD)

lint:
	uv run ty check
	helm lint $(HELM_CHART)

helm-install:
	kubectl create namespace $(NAMESPACE) --dry-run=client -o yaml | kubectl apply -f -
	helm install $(HELM_RELEASE) $(HELM_CHART) -n $(NAMESPACE) \
		--set image.tag=$$(git rev-parse --short HEAD) \
		--set timerInterval=5.0 \
		--set rbac.allowCallbackTokenSecrets=true
	kubectl wait --for=create pod -l app=job-template-run --timeout=30s -n $(NAMESPACE)
	kubectl wait --for=condition=Ready pod -l app=job-template-run --timeout=30s -n $(NAMESPACE)

helm-uninstall:
	helm uninstall $(HELM_RELEASE) -n $(NAMESPACE) || true

test:
	TIMER_INTERVAL=15 uv run pytest tests/test_controller.py
	chainsaw test tests/

all: kind build helm-install test

# Full e2e run against a local kind cluster using the local controller code,
# from inside the cellbytes devcontainer. Unlike `make all`, this rewrites the
# kubeconfig to kind's in-network address: the devcontainer cannot reach kind's
# host-published 127.0.0.1 API port, but it is on the `kind` docker network and
# can reach the control-plane container by name. Idempotent, so it can be re-run
# to redeploy local changes.
KUBECONFIG_INTERNAL=/tmp/$(KIND_CLUSTER_NAME).kubeconfig
dev-e2e:
	kind create cluster --name $(KIND_CLUSTER_NAME) 2>/dev/null || true
	$(MAKE) build
	kind get kubeconfig --name $(KIND_CLUSTER_NAME) --internal > $(KUBECONFIG_INTERNAL)
	KUBECONFIG=$(KUBECONFIG_INTERNAL) helm upgrade --install $(HELM_RELEASE) $(HELM_CHART) \
		-n $(NAMESPACE) --create-namespace \
		--set image.tag=$$(git rev-parse --short HEAD) \
		--set timerInterval=5.0 \
		--set rbac.allowCallbackTokenSecrets=true
	KUBECONFIG=$(KUBECONFIG_INTERNAL) kubectl rollout status \
		deploy/$(HELM_RELEASE)-controller -n $(NAMESPACE) --timeout=90s
	KUBECONFIG=$(KUBECONFIG_INTERNAL) TIMER_INTERVAL=15 uv run pytest tests/test_controller.py
	KUBECONFIG=$(KUBECONFIG_INTERNAL) chainsaw test tests/

clean: helm-uninstall kind-down
