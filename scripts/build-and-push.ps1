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
    $image = "${Registry}/${svc}:latest"
    Write-Host "==> Construyendo $image"
    docker build -t $image "./services/$svc"
    if ($LASTEXITCODE -ne 0) { throw "docker build failed for $svc" }
    Write-Host "==> Subiendo $image"
    docker push $image
    if ($LASTEXITCODE -ne 0) { throw "docker push failed for $svc" }
}

Write-Host "==> Generando manifiestos con el registry sustituido en k8s/generated/"
New-Item -ItemType Directory -Force -Path "k8s/generated" | Out-Null

Get-ChildItem -Path "k8s" -Filter "*.yaml" | ForEach-Object {
    (Get-Content $_.FullName -Raw) -replace "__REGISTRY__", $Registry |
        Set-Content -Path "k8s/generated/$($_.Name)" -Encoding utf8
}

Write-Host "Listo. Aplica con: kubectl apply -f k8s/generated/"
