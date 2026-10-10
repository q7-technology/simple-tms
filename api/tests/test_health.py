def test_health_answers_ok_once_the_database_is_there(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}
