$ErrorActionPreference = "Stop"

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
}

Write-Host "Starting MigrateIQ services with Docker Compose..."
docker compose up --build -d
Write-Host "Frontend: http://localhost:8501"
Write-Host "Backend API docs: http://localhost:8000/docs"
Write-Host "Demo CRM docs: http://localhost:8001/docs"
