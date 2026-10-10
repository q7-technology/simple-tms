import pytest

from tms.services import wms_client


def test_get_delivery_asks_with_our_key_and_the_owner(wms):
    wms.deliveries["0080/12"] = {"external_ref": "0080/12", "ship_to": {"name": "Ballarat Hardware"}}
    out = wms_client.get_delivery("0080/12", "ACME")
    assert out["ship_to"]["name"] == "Ballarat Hardware"
    assert wms.calls == [{"path": "/v1/deliveries/0080%2F12", "query": "owner=ACME",
                          "authorization": "Bearer wms_test-key-for-the-tms"}]


def test_anything_but_200_is_unavailable(wms):
    with pytest.raises(wms_client.WmsUnavailable, match="HTTP 404"):
        wms_client.get_delivery("NOT-THERE", "DEFAULT")
    wms.deliveries["0080012345"] = {}
    wms.statuses.append(503)
    with pytest.raises(wms_client.WmsUnavailable, match="HTTP 503"):
        wms_client.get_delivery("0080012345", "DEFAULT")


def test_nobody_listening_is_unavailable(monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_url", "http://127.0.0.1:9")
    with pytest.raises(wms_client.WmsUnavailable):
        wms_client.get_delivery("0080012345", "DEFAULT")


def test_not_configured_is_unavailable(monkeypatch):
    from tms.config import get_settings

    monkeypatch.setattr(get_settings(), "wms_url", "")
    with pytest.raises(wms_client.WmsUnavailable, match="not set"):
        wms_client.get_delivery("0080012345", "DEFAULT")


def test_an_answer_that_is_not_a_delivery_is_unavailable(wms):
    wms.deliveries["0080012345"] = b"<html>proxy error</html>"
    with pytest.raises(wms_client.WmsUnavailable, match="not a delivery"):
        wms_client.get_delivery("0080012345", "DEFAULT")
    wms.deliveries["0080012345"] = [1, 2]
    with pytest.raises(wms_client.WmsUnavailable, match="not a delivery"):
        wms_client.get_delivery("0080012345", "DEFAULT")


@pytest.mark.parametrize("status, hint", [
    (401, "TMS_WMS_API_KEY"),
    (403, "tasks:read"),
    (404, "owner"),
])
def test_a_refusal_from_simple_wms_is_logged_as_an_error_naming_the_likely_cause(
        wms, caplog, status, hint):
    """These will not mend themselves on a retry: someone has to fix the key or
    the subscriber, so they are errors, not warnings. Still a 503 to the WMS."""
    import logging

    from tms.config import get_settings

    wms.deliveries["0080012345"] = {"detail": "no"}
    wms.statuses.append(status)
    with caplog.at_level(logging.INFO, logger="tms.wms"):
        with pytest.raises(wms_client.WmsUnavailable, match=f"HTTP {status}"):
            wms_client.get_delivery("0080012345", "ACME")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    message = errors[0].getMessage()
    assert hint in message and "0080012345" in message and "ACME" in message
    assert get_settings().wms_api_key not in caplog.text


def test_other_failures_are_not_logged_as_errors(wms, caplog):
    import logging

    wms.deliveries["0080012345"] = {}
    wms.statuses.append(503)
    with caplog.at_level(logging.INFO, logger="tms.wms"):
        with pytest.raises(wms_client.WmsUnavailable):
            wms_client.get_delivery("0080012345", "DEFAULT")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
