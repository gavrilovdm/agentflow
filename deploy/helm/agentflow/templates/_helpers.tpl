{{- define "agentflow.name" -}}{{ .Release.Name }}{{- end -}}
{{- define "agentflow.labels" -}}
app.kubernetes.io/name: agentflow
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}
{{- define "agentflow.env" -}}
envFrom:
  - configMapRef: { name: {{ include "agentflow.name" . }}-config }
  - secretRef: { name: {{ include "agentflow.name" . }}-secrets }
{{- end -}}
