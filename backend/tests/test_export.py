import pytest


@pytest.mark.parametrize(
    ("report_format", "content_type"),
    [
        ("pdf", "application/pdf"),
        ("xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("csv", "text/csv"),
    ],
)
def test_match_report_exports_all_formats(client, report_format, content_type):
    match = client.post("/api/matches", json={}).json()

    response = client.get(f"/api/matches/{match['id']}/export?format={report_format}")

    assert response.status_code == 200, response.text
    assert response.content
    assert response.headers["content-type"].startswith(content_type)
    assert response.headers["content-disposition"].endswith(f'"match-{match["id"]}-report.{report_format}"')