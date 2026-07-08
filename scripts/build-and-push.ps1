# Construye y publica las 5 imagenes en un registry (Docker Hub u otro),
# y genera k8s/generated/*.yaml con __REGISTRY__ ya sustituido.
#
# Uso: .\scripts\build-and-push.ps1 -Registry miusuario

param(
    [Parameter(Mandatory = $true)]
    [string]$Registry
)

$ErrorActionPreference = "Stop"

$services = @("api-gateway", "reservas-service", "inventario-service", "pagos-service", "notificaciones-service")

foreach ($svc in $services) {
    Write-Host "==> Construyendo $Registry/$svc:latest"
    docker build -t "$Registry/$svc:latest" "./services/$svc"
    Write-Host "==> Subiendo $Registry/$svc:latest"
    docker push "$Registry/$svc:latest"
}

Write-Host "==> Generando manifiestos con el registry sustituido en k8s/generated/"
New-Item -ItemType Directory -Force -Path "k8s/generated" | Out-Null

Get-ChildItem -Path "k8s" -Filter "*.yaml" | ForEach-Object {
    (Get-Content $_.FullName -Raw) -replace "__REGISTRY__", $Registry |
        Set-Content -Path "k8s/generated/$($_.Name)" -Encoding utf8
}

Write-Host "Listo. Aplica con: kubectl apply -f k8s/generated/"
