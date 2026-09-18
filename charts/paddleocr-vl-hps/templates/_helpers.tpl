{{- define "paddleocr-vl-hps.name" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "paddleocr-vl-hps.labels" -}}
app.kubernetes.io/name: paddleocr-vl-hps
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "paddleocr-vl-hps.selectorLabels" -}}
app.kubernetes.io/name: paddleocr-vl-hps
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
