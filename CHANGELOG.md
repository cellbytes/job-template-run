## 1.1.1 (2026-09-14)

### Fix

- ci: pin setup-uv to an existing tag ([bea4780](https://github.com/cellbytes/job-template-run/commit/bea47809a03eb3be28f28ec5bfbcd0241b50c1c9))

  astral-sh/setup-uv publishes no floating major tag, so @v10 does not

  resolve and every workflow failed at action setup. Only v10.0.0, v10.0.1

  and v10.1.0 exist.

  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

## 1.1.0 (2026-09-03)

### Feat

- forward the terminal Job condition in the callback ([f54f81e](https://github.com/cellbytes/job-template-run/commit/f54f81e46be8e99e0ca8feb8a48b22d1cd9f819e))

  The callback said only "Complete" or "Failed", so a receiver could not tell

  a backoff limit from a deadline from a pod failure policy without holding

  Job read access of its own. Send the condition's own reason and message

  alongside the status, omitting either key the condition left empty.

  Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>

## 1.0.0 (2026-07-02)

### BREAKING CHANGE

- Jobs are named deterministically instead of using

  generateName, deleting a JobRun now deletes its Job, and the controller

  Deployment selector changed: delete the existing controller Deployment

  before upgrading a pre-1.0 install. ([ec3b0e5](https://github.com/cellbytes/job-template-run/commit/ec3b0e5a7ecd0f261f1ebfb4f6f0e2a383f34d6c))

### Feat

- harden controller and chart for 1.0 ([ec3b0e5](https://github.com/cellbytes/job-template-run/commit/ec3b0e5a7ecd0f261f1ebfb4f6f0e2a383f34d6c))

  - JobRuns are now one-shot: the Job gets a deterministic name

  (<template>-<run>, digest-truncated to fit 63 chars), is recorded in

  .status.jobName, and is never re-created after deletion or TTL

  garbage collection.

  - Created Jobs carry an owner reference to their JobRun, so deleting a

  JobRun cascades to its Job and pods.

  - Malformed JobTemplates and API-rejected Job specs fail the JobRun

  permanently with .status.error instead of crash-looping the timer.

  - Callback tokens can be read from a Secret via

  spec.callbackTokenSecretRef (opt-in via rbac.allowCallbackTokenSecrets);

  the payload now includes the namespace; 3xx responses are retried

  instead of dropped.

  - JobRun creation and Job status changes are handled by watch events with

  the timers kept as reconcile backstops, and JobRun status is only

  patched when something changed.

  - JobRun CRD gains the status subresource and a Job printer column; both

  CRDs are kept on helm uninstall; the controller deployment gets a

  security context, resource requests, a readiness probe, and a

  release-scoped selector.

  - Release workflow only releases the commit the tests validated and

  supports manual dispatch; the license changed to EUPL-1.2.

## 0.20.0 (2026-06-03)

### Feat

- ship controller NetworkPolicy and add callback-delivery e2e tests ([191f6fc](https://github.com/cellbytes/job-template-run/commit/191f6fc5e86191b6a4c7b813adf2469f78966096))

  Add a chart NetworkPolicy (egress to DNS, the API server, and the callback

  endpoint) so the controller functions under a default-deny policy; add

  chainsaw e2e tests verifying real callback delivery and the 5xx-retry

  behaviour; add a make dev-e2e target to run the suite against local code.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

### Fix

- deliver job-status callback reliably ([9c79201](https://github.com/cellbytes/job-template-run/commit/9c7920109c6da1465aeaba5e4e5a44baa72d06ce))

  Only mark the Job callback-sent after a genuine delivery (2xx, or 4xx which

  will not improve on retry); on a 5xx or network error leave it unset so the

  next timer tick retries instead of dropping the terminal status and leaving

  tasks stuck processing.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

## 0.19.1 (2026-06-03)

### Fix

- point chart image repository to ghcr.io/cellbytes ([029a322](https://github.com/cellbytes/job-template-run/commit/029a322057fe47e90d9ffdb7abb5584e69c9aa67))

  The chart default and Makefile still referenced the old ghcr.io/hematoscope

  org after the move to cellbytes, so deployed pods pulled from the wrong (stale)

  registry path. The release workflow already pushes to ghcr.io/<github.repository>

  (cellbytes/job-template-run), so this aligns the chart default with the image

  that CI actually publishes.

  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>

## 0.19.0 (2026-06-01)

### Feat

- upgrade dependencies ([e13006b](https://github.com/hematoscope/job-template-run/commit/e13006b067631d880dfb4121d3350bc106387921))

## 0.18.0 (2026-03-25)

### Feat

- add callback functionality to job status updates ([3570e16](https://github.com/hematoscope/job-template-run/commit/3570e16086b441d31a9136018b5d0de2a7274186))

## 0.17.0 (2025-07-22)

### Feat

- rename namespace from hematoscope.app to cellbytes.io ([17894f8](https://github.com/hematoscope/job-template-run/commit/17894f88f052ca4ddae6d801ed4b9ceca50f2749))

### Fix

- crd-jobtemplate whitespace ([4d4530b](https://github.com/hematoscope/job-template-run/commit/4d4530ba5c9ac781cb86791051955a83b32a1a8f))

## 0.16.1 (2025-05-08)

### Fix

- minimize granted rbac permissions ([dc0f75f](https://github.com/hematoscope/job-template-run/commit/dc0f75f44016b6ebde4e860bebdfcd20403f7278))

- only create job if jobrun has no jobs running ([7d2bf0d](https://github.com/hematoscope/job-template-run/commit/7d2bf0dabd9519a1b9b04d55825f7e91909e307f))

## 0.16.0 (2025-05-07)

### Feat

- bump kopf to 1.37.5 again ([d80455d](https://github.com/hematoscope/job-template-run/commit/d80455dce898429e70e514b9d742a3b8369b4080))

## 0.15.0 (2025-05-07)

### Feat

- use kopf timers instead of event handlers ([3d3ef6f](https://github.com/hematoscope/job-template-run/commit/3d3ef6fdbd11b0f15a928cd326d46e98cfa2e3e6))

## 0.14.0 (2025-05-07)

### Feat

- downgrade kopf to 1.37.1 ([afe5860](https://github.com/hematoscope/job-template-run/commit/afe5860fb8afa0d92d8ff5401c4b350e3b0362b9))

  We are having issues similar to

  https://github.com/nolar/kopf/issues/1158, so hopefully this downgrade

  might help keeping the controller stable over long periods of time.

## 0.13.0 (2025-04-30)

### Feat

- add tests ([bb5d326](https://github.com/hematoscope/job-template-run/commit/bb5d3268c4ed62718d8a1accd5ddec52925a8dd0))

- add a liveness probe ([2c2cb3d](https://github.com/hematoscope/job-template-run/commit/2c2cb3d644cddce7eecbdec3a765ea4389bc3bfc))

### Fix

- set getter timeouts ([58944c3](https://github.com/hematoscope/job-template-run/commit/58944c33b446260f0bf273cf49e45d9e7380e688))

## 0.12.1 (2025-04-22)

### Fix

- set a server timeout ([86f2dcf](https://github.com/hematoscope/job-template-run/commit/86f2dcf893e0bdf00c86c4f504a07fe6fce394b5))

## 0.12.0 (2025-04-16)

### Feat

- add LICENSE ([f438858](https://github.com/hematoscope/job-template-run/commit/f43885836740d54510914b0ca445554cf735edd2))

## 0.11.1 (2025-04-16)

### Fix

- additionalprintercolumns indentation ([49c506f](https://github.com/hematoscope/job-template-run/commit/49c506f374b3d61d0b4f7e45260c2582f9712d78))

## 0.11.0 (2025-04-16)

### Feat

- add additional printer columns to jobrun crd ([52523a7](https://github.com/hematoscope/job-template-run/commit/52523a72ad7c426c9b2b8ce3537f91f8c233f285))

## 0.10.0 (2025-04-16)

### Feat

- sync job statuses to jobruns ([7b32661](https://github.com/hematoscope/job-template-run/commit/7b32661ceb44fc8e3277fce5e6c038a5355c6826))

## 0.9.1 (2025-04-16)

### Fix

- job pod metadata keyerror ([1621010](https://github.com/hematoscope/job-template-run/commit/1621010bb96c0b0789768d9b33393e6377cb5ab1))

## 0.9.0 (2025-04-16)

### Feat

- apply common job labels also to job pods ([465f4c7](https://github.com/hematoscope/job-template-run/commit/465f4c75fd47e0f54c90d9b2e35f9b7b9e9c34cc))

## 0.8.0 (2025-04-16)

### Feat

- add job labels for template and run ([f94f644](https://github.com/hematoscope/job-template-run/commit/f94f644f094613c9d4301aee98654cae4b867ef2))

### Fix

- use chart version for label instead of appversion ([929ceb8](https://github.com/hematoscope/job-template-run/commit/929ceb857442ddb7aa3938dc61e90d1f891f226c))

## 0.7.0 (2025-04-16)

### Feat

- generate job name from template and run ([10b4ab2](https://github.com/hematoscope/job-template-run/commit/10b4ab2a09ef77a106cbbc127a77e355380abe02))

## 0.6.0 (2025-04-16)

### Feat

- remove jobrun job lifecycle management ([0d7b48d](https://github.com/hematoscope/job-template-run/commit/0d7b48dbb78166878c92cb1efdb0ab340f3acee6))

  this feature was stupid as ttlSecondsAfterFinished exists

## 0.5.0 (2025-04-16)

### Feat

- add jobrun created job lifecycle management ([0eec811](https://github.com/hematoscope/job-template-run/commit/0eec811fd93c22eab0741929fc1ad2673033cc3b))

- add labels to kubernetes resources ([09f00fe](https://github.com/hematoscope/job-template-run/commit/09f00fe9f91ed4008c5532e433e041ba1f74ca50))

## 0.4.2 (2025-04-15)

### Fix

- permissions for jobruns and events ([e59b398](https://github.com/hematoscope/job-template-run/commit/e59b398f7ed5edb5b9ad8d2d3754f11da3d231e9))

## 0.4.1 (2025-04-15)

### Fix

- allow job-template-run service account to see crds ([8a460d4](https://github.com/hematoscope/job-template-run/commit/8a460d402c81bf6e8c69e203033aa57eb1416256))

- rename job-template-run deployment to include controller ([23f801e](https://github.com/hematoscope/job-template-run/commit/23f801e1e9b8bb8af4f85c1a2425f5c762a00186))

## 0.4.0 (2025-04-15)

### Feat

- allow whole job spec for templates ([d2257e9](https://github.com/hematoscope/job-template-run/commit/d2257e974c12bc9242b10819d615fe72bdded9cf))

## 0.3.0 (2025-04-15)

### Feat

- allow overriding either command or args for jobrun ([ecbc627](https://github.com/hematoscope/job-template-run/commit/ecbc6275878ab4b88b1096e2f89d7d49e60f1231))

- flatten job template crd to accept a direct jobspec ([bb9507a](https://github.com/hematoscope/job-template-run/commit/bb9507a97917fd53aa683c0e8d2e0c0dceb2c6ba))

## 0.2.0 (2025-04-15)

### Feat

- use non-root user for dockerfile ([14fa169](https://github.com/hematoscope/job-template-run/commit/14fa16991de605961b2a9de77c4abcc2f7d497a0))

### Fix

- don't overwrite release notes ([f62a051](https://github.com/hematoscope/job-template-run/commit/f62a05184a9448f4af0f1482fef7c012f8eeefd4))

## 0.1.16 (2025-04-15)

### Fix

- changelog to stdout ([68c011c](https://github.com/hematoscope/job-template-run/commit/68c011c0475bbaa85071aab9c0f80cac903d9ce7))

## 0.1.15 (2025-04-15)

### Fix

- no commit arg for cz ([391fe0c](https://github.com/hematoscope/job-template-run/commit/391fe0c516723cb9cc4718f9b98da0f10e09fa3b))

- uv run cz ([1e1c454](https://github.com/hematoscope/job-template-run/commit/1e1c45499c2319a1620204c9fc3ba12c55d0e357))

- uv install commitizen for ci ([095e06a](https://github.com/hematoscope/job-template-run/commit/095e06abf41ad1379dcfdb2876544ab586795940))

## 0.1.14 (2025-04-15)

### Fix

- revert helm release tag format ([7222740](https://github.com/hematoscope/job-template-run/commit/72227406e66c53447dd9040cda9150068e31fc13))

## 0.1.13 (2025-04-15)

### Fix

- release notes in env ([b3b2f59](https://github.com/hematoscope/job-template-run/commit/b3b2f593b429237f54ca846d83b234b7a5c5646c))

## 0.1.12 (2025-04-15)

### Fix

- chart releaser release notes ([23eadfb](https://github.com/hematoscope/job-template-run/commit/23eadfb54c69471fc0b3aa138fa194b9df4739f2))

## 0.1.11 (2025-04-15)

### Fix

- todo docs ([4fe7c8d](https://github.com/hematoscope/job-template-run/commit/4fe7c8d1f1e302c162f878f026fecf5b807f4556))

## 0.1.10 (2025-04-15)

### Fix

- update readme ([d709610](https://github.com/hematoscope/job-template-run/commit/d7096108ff010c9aa592b949a84d7f6204b4ca9a))

## 0.1.9 (2025-04-15)

### Fix

- line ending ([9fac8fb](https://github.com/hematoscope/job-template-run/commit/9fac8fb66acd4dc9062eab9ce9f0d830668ac207))

## 0.1.8 (2025-04-15)

### Fix

- remove debug ([56c1677](https://github.com/hematoscope/job-template-run/commit/56c167739f002e5867740895bdcd90448a84d8a7))

## 0.1.7 (2025-04-15)

### Fix

- add cr temp folders to gitignore ([978bbc7](https://github.com/hematoscope/job-template-run/commit/978bbc7a60d8ab91c45df76d1bd2f501b67c27ff))

## 0.1.6 (2025-04-15)

### Fix

- add debug back ([e4110fe](https://github.com/hematoscope/job-template-run/commit/e4110feef5205d92606a57b36fc2d97560428112))

## 0.1.5 (2025-04-15)

### Fix

- remove debug from gha ([ae34917](https://github.com/hematoscope/job-template-run/commit/ae34917dd72ed0bec0e43ee79ae74bb3c0dab06f))

## 0.1.4 (2025-04-15)

### Fix

- line ending ([a086317](https://github.com/hematoscope/job-template-run/commit/a086317fe311b51f713632bf0e011ba092efa591))

## 0.1.3 (2025-04-15)

### Fix

- gitignore ci release notes ([a7678fd](https://github.com/hematoscope/job-template-run/commit/a7678fd85148f6267243352e1982274b199b5824))

- move release notes to chart folder in ci ([51cd620](https://github.com/hematoscope/job-template-run/commit/51cd620f69b47175f429dc09279b9d3560474a40))

## 0.1.2 (2025-04-15)

### Fix

- default image repository ([5cc05e2](https://github.com/hematoscope/job-template-run/commit/5cc05e2e1d3f60a2c38371d14af6886e57848ccd))

## 0.1.1 (2025-04-14)

### Fix

- commitizen action write permissions ([0a38734](https://github.com/hematoscope/job-template-run/commit/0a38734ce3f9f98eb13766d59f893edafd4ffb8a))

- add content to readme ([5552eb2](https://github.com/hematoscope/job-template-run/commit/5552eb273bbefe5febd30f4f167900b24dadf060))
