{{/* Names and labels */}}
{{- define "cell.name" -}}{{- default .Chart.Name .Values.nameOverride | trunc 40 | trimSuffix "-" -}}{{- end -}}
{{- define "cell.fullname" -}}
{{- if .Values.fullnameOverride -}}{{- .Values.fullnameOverride | trunc 40 | trimSuffix "-" -}}
{{- else -}}{{- printf "%s" .Release.Name | trunc 40 | trimSuffix "-" -}}{{- end -}}
{{- end -}}
{{- define "cell.labels" -}}
app.kubernetes.io/name: {{ include "cell.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}
{{- define "cell.selector" -}}
app.kubernetes.io/name: {{ include "cell.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/* image: dict root, repo (image name), override (per-component {repository, tag}) */}}
{{- define "cell.image" -}}
{{- $tag := default (default .root.Chart.AppVersion .root.Values.global.imageTag) (.override).tag -}}
{{- $repo := default (printf "%s/%s" .root.Values.global.imageRegistry .repo) (.override).repository -}}
{{- printf "%s:%s" $repo $tag -}}
{{- end -}}

{{/* The one Secret all components read from: yours (secrets.existingSecret) or the generated one. */}}
{{- define "cell.secretName" -}}
{{- default (printf "%s-secrets" (include "cell.fullname" .)) .Values.secrets.existingSecret -}}
{{- end -}}
{{- define "cell.secretRef" -}}secretKeyRef: { name: {{ include "cell.secretName" .root }}, key: {{ .key }} }{{- end -}}

{{/* Endpoints that are bundled or external */}}
{{- define "cell.pgHost" -}}{{ if .Values.postgres.bundled.enabled }}{{ include "cell.fullname" . }}-postgres{{ else }}{{ required "postgres.external.host is required when postgres.bundled.enabled=false" .Values.postgres.external.host }}{{ end }}{{- end -}}
{{- define "cell.pgPort" -}}{{ if .Values.postgres.bundled.enabled }}5432{{ else }}{{ .Values.postgres.external.port }}{{ end }}{{- end -}}
{{- define "cell.pgUser" -}}{{ if .Values.postgres.bundled.enabled }}substrate{{ else }}{{ .Values.postgres.external.user }}{{ end }}{{- end -}}
{{- define "cell.pgSslmode" -}}{{ if .Values.postgres.bundled.enabled }}require{{ else }}{{ .Values.postgres.external.sslmode }}{{ end }}{{- end -}}
{{- define "cell.redisHost" -}}{{ if .Values.redis.bundled.enabled }}{{ include "cell.fullname" . }}-redis{{ else }}{{ required "redis.external.host is required when redis.bundled.enabled=false" .Values.redis.external.host }}{{ end }}{{- end -}}
{{- define "cell.s3Endpoint" -}}{{ if .Values.s3.bundled.enabled }}http://{{ include "cell.fullname" . }}-seaweedfs:8333{{ else }}{{ required "s3.external.endpoint is required when s3.bundled.enabled=false" .Values.s3.external.endpoint }}{{ end }}{{- end -}}
{{- define "cell.icebergUrl" -}}{{ if .Values.s3.bundled.enabled }}http://{{ include "cell.fullname" . }}-seaweedfs:8181{{ else }}{{ required "s3.external.icebergCatalogUrl is required when s3.bundled.enabled=false" .Values.s3.external.icebergCatalogUrl }}{{ end }}{{- end -}}
{{- define "cell.keycloakInternal" -}}http://{{ include "cell.fullname" . }}-keycloak:8080{{- end -}}
{{/* The issuer is the public address: the same string for browsers and for services. */}}
{{- define "cell.issuer" -}}{{ trimSuffix "/" (required "publicUrls.keycloak is required (the token issuer)" .Values.publicUrls.keycloak) }}/realms/{{ .Values.keycloak.realm.name }}{{- end -}}

{{/* A statement is "name: value" */}}
{{- define "cell.dbUrl" -}}
{{- /* dict root, db, scheme */ -}}
{{- printf "%s://%s:$(POSTGRES_PASSWORD)@%s:%v/%s" .scheme (include "cell.pgUser" .root) (include "cell.pgHost" .root) (include "cell.pgPort" .root) .db -}}
{{- end -}}

{{/* In-cluster hosts substrate-data may speak plain HTTP to (explicit opt-in there); NetworkPolicy fences the namespace. */}}
{{- define "cell.insecureHosts" -}}
{{- $f := include "cell.fullname" . -}}
{{- $own := list (printf "%s-seaweedfs" $f) (printf "%s-keycloak" $f) (printf "%s-data-controller" $f) (printf "%s-data-worker-sql" $f) (printf "%s-data-worker-jobs" $f) (printf "%s-data-worker-rag" $f) -}}
{{- $pub := list -}}
{{- range $u := list .Values.publicUrls.keycloak .Values.publicUrls.s3 -}}
{{- if hasPrefix "http://" $u }}{{ $pub = append $pub (regexReplaceAll "^http://([^/:]+).*$" $u "${1}") }}{{ end -}}
{{- end -}}
{{- concat $own $pub .Values.data.insecureHttpHosts | toJson -}}
{{- end -}}

{{/* Env shared by everything that talks to substrate-data's storage */}}
{{- define "cell.dataEnv" -}}
- name: POSTGRES_PASSWORD
  valueFrom: { {{ include "cell.secretRef" (dict "root" . "key" "postgres-password") }} }
- name: SUBSTRATE_DATA_INSECURE_HTTP_HOSTS
  value: {{ include "cell.insecureHosts" . | quote }}
- name: SUBSTRATE_DATA_BEHIND_TLS_PROXY
  value: {{ .Values.data.behindTlsProxy | quote }}
- name: SUBSTRATE_DATA_OIDC_ISSUER
  value: {{ include "cell.issuer" . | quote }}
{{- if .Values.keycloak.internalJwks }}
- name: SUBSTRATE_DATA_OIDC_JWKS_URL
  value: "{{ include "cell.keycloakInternal" . }}/realms/{{ .Values.keycloak.realm.name }}/protocol/openid-connect/certs"
{{- end }}
- name: SUBSTRATE_DATA_OIDC_AUDIENCE
  value: substrate-data
- name: SUBSTRATE_DATA_S3_ENDPOINT_URL
  value: {{ include "cell.s3Endpoint" . | quote }}
- name: SUBSTRATE_DATA_S3_REGION
  value: {{ if .Values.s3.bundled.enabled }}us-east-1{{ else }}{{ .Values.s3.external.region }}{{ end }}
- name: SUBSTRATE_DATA_ICEBERG_CATALOG_URL
  value: {{ include "cell.icebergUrl" . | quote }}
- name: SUBSTRATE_DATA_WAREHOUSE
  value: {{ .Values.s3.buckets.warehouse | quote }}
- name: SUBSTRATE_DATA_STAGING_BUCKET
  value: {{ .Values.s3.buckets.staging | quote }}
- name: SUBSTRATE_DATA_VOLUMES_BUCKET
  value: {{ .Values.s3.buckets.volumes | quote }}
- name: SUBSTRATE_DATA_STORAGE_ID
  value: {{ default (printf "%s-warehouse" .Release.Name) .Values.s3.storageId | quote }}
- name: SUBSTRATE_DATA_CONTROLLER_URL
  value: "http://{{ include "cell.fullname" . }}-data-controller:8040"
{{- if .Values.s3.stsRoleArn }}
- name: SUBSTRATE_DATA_STORAGE_STS_ROLE_ARN
  value: {{ .Values.s3.stsRoleArn | quote }}
{{- end }}
{{- end -}}

{{/*
A Deployment + Service (+ HPA). dict: root, component, repo, override, command, args, port, env (YAML list text),
cfg (replicas, resources, autoscaling), probePath, volumes, volumeMounts, publish (bool: gets an Ingress backend), writable (list of dirs)
*/}}
{{- define "cell.workload" -}}
{{- $root := .root -}}
{{- $full := printf "%s-%s" (include "cell.fullname" $root) .component -}}
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ $full }}
  labels:
    {{- include "cell.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .component }}
spec:
  {{- if not (.cfg.autoscaling).enabled }}
  replicas: {{ .cfg.replicas | default 1 }}
  {{- end }}
  selector:
    matchLabels:
      {{- include "cell.selector" (dict "root" $root "component" .component) | nindent 6 }}
  template:
    metadata:
      labels:
        {{- include "cell.labels" $root | nindent 8 }}
        app.kubernetes.io/component: {{ .component }}
      annotations:
        checksum/secret: {{ include (print $root.Template.BasePath "/secret.yaml") $root | sha256sum }}
    spec:
      serviceAccountName: {{ include "cell.fullname" $root }}
      automountServiceAccountToken: {{ .automountToken | default false }}
      {{- with $root.Values.global.hostAliases }}
      hostAliases: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with $root.Values.global.imagePullSecrets }}
      imagePullSecrets: {{- toYaml . | nindent 8 }}
      {{- end }}
      securityContext:
        runAsNonRoot: true
        runAsUser: 10001
        runAsGroup: 10001
        fsGroup: 10001
        seccompProfile: { type: RuntimeDefault }
      containers:
        - name: {{ .component }}
          image: {{ include "cell.image" (dict "root" $root "repo" .repo "override" .override) }}
          imagePullPolicy: {{ $root.Values.global.imagePullPolicy }}
          {{- with .command }}
          command: {{- toYaml . | nindent 12 }}
          {{- end }}
          {{- with .args }}
          args: {{- toYaml . | nindent 12 }}
          {{- end }}
          ports:
            - { name: http, containerPort: {{ .port }} }
          env:
            - { name: PORT, value: {{ .port | quote }} }
            {{- .env | nindent 12 }}
          {{- with $root.Values.global.extraEnv }}
            {{- toYaml . | nindent 12 }}
          {{- end }}
          readinessProbe:
            httpGet: { path: {{ .probePath }}, port: http }
            periodSeconds: 10
            failureThreshold: 6
          livenessProbe:
            httpGet: { path: {{ .probePath }}, port: http }
            initialDelaySeconds: 30
            periodSeconds: 20
            failureThreshold: 6
          resources:
            {{- toYaml (.cfg.resources | default dict) | nindent 12 }}
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
          volumeMounts:
            - { name: tmp, mountPath: /tmp }
            {{- range .writable }}
            - { name: {{ .name }}, mountPath: {{ .path }} }
            {{- end }}
            {{- with .volumeMounts }}
            {{- toYaml . | nindent 12 }}
            {{- end }}
      volumes:
        - { name: tmp, emptyDir: {} }
        {{- range .writable }}
        - { name: {{ .name }}, emptyDir: {} }
        {{- end }}
        {{- with .volumes }}
        {{- toYaml . | nindent 8 }}
        {{- end }}
      {{- with .cfg.nodeSelector }}
      nodeSelector: {{- toYaml . | nindent 8 }}
      {{- end }}
      {{- with .cfg.tolerations }}
      tolerations: {{- toYaml . | nindent 8 }}
      {{- end }}
---
apiVersion: v1
kind: Service
metadata:
  name: {{ $full }}
  labels:
    {{- include "cell.labels" $root | nindent 4 }}
    app.kubernetes.io/component: {{ .component }}
spec:
  selector:
    {{- include "cell.selector" (dict "root" $root "component" .component) | nindent 4 }}
  ports:
    - { name: http, port: {{ .port }}, targetPort: http }
{{- if (.cfg.autoscaling).enabled }}
---
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: {{ $full }}
  labels:
    {{- include "cell.labels" $root | nindent 4 }}
spec:
  scaleTargetRef: { apiVersion: apps/v1, kind: Deployment, name: {{ $full }} }
  minReplicas: {{ .cfg.autoscaling.minReplicas }}
  maxReplicas: {{ .cfg.autoscaling.maxReplicas }}
  metrics:
    - type: Resource
      resource:
        name: cpu
        target: { type: Utilization, averageUtilization: {{ .cfg.autoscaling.targetCPUUtilizationPercentage }} }
    {{- if .cfg.autoscaling.targetMemoryUtilizationPercentage }}
    - type: Resource
      resource:
        name: memory
        target: { type: Utilization, averageUtilization: {{ .cfg.autoscaling.targetMemoryUtilizationPercentage }} }
    {{- end }}
  {{- with .cfg.autoscaling.behavior }}
  behavior: {{- toYaml . | nindent 4 }}
  {{- end }}
{{- end }}
{{ "\n" }}{{- end -}}

{{/* ["Read:bucket", ...] as JSON, for SeaweedFS identities */}}
{{- define "cell.actions" -}}
{{- $out := list -}}{{- $b := .b -}}
{{- range .l }}{{ $out = append $out (printf "%s:%s" . $b) }}{{- end -}}
{{- toJson $out -}}
{{- end -}}

{{/* An existing secret value (base64 in the cluster), or the fresh one. */}}
{{- define "cell.pick" -}}
{{- if hasKey .old .key -}}{{ index .old .key | b64dec }}{{- else -}}{{ .fresh }}{{- end -}}
{{- end -}}
