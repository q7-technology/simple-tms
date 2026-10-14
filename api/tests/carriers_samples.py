"""Carriers made through the API, and consignments sent with them: what the
carrier tests start from. The names are samples, never real carriers."""
from runs_samples import DAY

RIDGELINE = "Ridgeline Express (sample)"
VALLEY = "Valley Freight (sample)"


def new_carrier(client, headers, name=RIDGELINE, **more) -> dict:
    r = client.post("/v1/carriers", headers=headers, json={"name": name, **more})
    assert r.status_code == 201, r.text
    return r.json()


def book(client, headers, carrier_id: int, consignment_id: int, day=DAY, **more):
    return client.post(f"/v1/carriers/{carrier_id}/book", headers=headers,
                       json={"consignment_id": consignment_id, "date": day.isoformat(), **more})


def con_note(client, headers, manifest_id: int, consignment_id: int, note: str):
    return client.post(f"/v1/manifests/{manifest_id}/con-note", headers=headers,
                       json={"consignment_id": consignment_id, "con_note": note})


def lines(manifest: dict) -> list[tuple[int, str | None]]:
    return [(line["consignment"]["id"], line["con_note"]) for line in manifest["lines"]]
