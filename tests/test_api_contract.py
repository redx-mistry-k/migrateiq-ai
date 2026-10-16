from migrateiq.api import app


def test_required_api_workflow_routes_are_registered():
    routes = {
        (method, route.path) for route in app.routes for method in getattr(route, "methods", set())
    }
    required = {
        ("GET", "/health"),
        ("GET", "/api/dashboard"),
        ("POST", "/api/projects"),
        ("POST", "/api/sources/upload"),
        ("GET", "/api/sources/{connection_id}/preview"),
        ("POST", "/api/mappings/suggest"),
        ("PUT", "/api/projects/{project_id}/mapping"),
        ("POST", "/api/transformations/preview"),
        ("POST", "/api/validations/preview"),
        ("GET", "/api/projects/{project_id}/cleaning-suggestions"),
        ("POST", "/api/ai/explain-validation"),
        ("POST", "/api/jobs"),
        ("POST", "/api/jobs/{job_id}/retry"),
        ("POST", "/api/jobs/{job_id}/reconcile"),
        ("GET", "/api/jobs/{job_id}/report.json"),
        ("GET", "/api/jobs/{job_id}/rejected.csv"),
    }
    assert required <= routes


def test_openapi_schema_is_generated():
    schema = app.openapi()
    assert schema["info"]["title"] == "MigrateIQ AI API"
    assert "/api/jobs" in schema["paths"]
