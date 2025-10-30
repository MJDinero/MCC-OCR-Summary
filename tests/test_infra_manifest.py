import pathlib
import yaml


def test_gmp_sidecar_present():
    doc = yaml.safe_load(pathlib.Path("pipeline.yaml").read_text())
    containers = doc["spec"]["template"]["spec"].get("containers", [])
    names = {container["name"] for container in containers}
    assert "gmp-sidecar" in names
    sidecar = next(
        container for container in containers if container["name"] == "gmp-sidecar"
    )
    env = {item["name"]: item["value"] for item in sidecar.get("env", [])}
    assert env["TARGET"].endswith("/metrics")
    assert env["PROJECT_ID"] == "quantify-agent"
