import os


def get_schemata(path: str = os.path.dirname(__file__)) -> frozenset[str]:
    """
    Checks all folders in app.core.db.models and
    bundles their names in a frozenset, excluding
    folders starting with an underscore.
    """

    model_folder = path
    schemata = set()
    for entry in os.listdir(model_folder):
        entry_path = os.path.join(model_folder, entry)
        if os.path.isdir(entry_path) and not entry.startswith("_"):
            schemata.add(entry)
    return frozenset(schemata)
