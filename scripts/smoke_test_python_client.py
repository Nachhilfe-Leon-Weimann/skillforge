from importlib import import_module
from importlib.metadata import distribution

PACKAGE_NAME = "skillforge-client"
README_TITLE = "# Skillforge Python Client"
REPOSITORY_URL = "Repository, https://github.com/Nachhilfe-Leon-Weimann/skillforge"


def main() -> None:
    client_module = import_module("skillforge_client")
    endpoint_module = import_module("skillforge_client.api.system.liveness_check_health_live_get")

    client_module.Client(base_url="https://api.example.com")
    client_module.AuthenticatedClient(base_url="https://api.example.com", token="test-token")
    assert callable(endpoint_module.sync)

    package_distribution = distribution(PACKAGE_NAME)
    package_metadata = package_distribution.metadata
    project_urls = package_metadata.get_all("Project-URL") or []
    metadata_text = package_distribution.read_text("METADATA") or ""

    assert package_metadata["Name"] == PACKAGE_NAME
    assert REPOSITORY_URL in project_urls
    assert README_TITLE in metadata_text


if __name__ == "__main__":
    main()
