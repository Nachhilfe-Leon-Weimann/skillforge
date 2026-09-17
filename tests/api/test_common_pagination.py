from typing import Annotated, Any

import pytest
from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import Field

from app.api.v1.common import ApiModel, Page, PageParams, PageQuery, register_exception_handlers

WIDGETS = ["red-0", "blue-1", "red-2", "blue-3", "red-4", "blue-5", "red-6"]


class WidgetResponse(ApiModel):
    name: str
    """Display name of the widget."""


class WidgetListParams(PageParams):
    kind: str | None = Field(None, description="Only widgets of this kind.")


type WidgetListQuery = Annotated[WidgetListParams, Query()]


def test_page_parameters_are_documented_with_defaults_and_bounds():
    parameters = _parameters("/widgets")

    assert parameters["limit"]["schema"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 50,
        "title": "Limit",
        "description": parameters["limit"]["description"],
    }
    assert parameters["offset"]["schema"] == {
        "type": "integer",
        "minimum": 0,
        "default": 0,
        "title": "Offset",
        "description": parameters["offset"]["description"],
    }
    assert parameters["limit"]["description"]
    assert parameters["offset"]["description"]
    assert not parameters["limit"]["required"]
    assert not parameters["offset"]["required"]


def test_page_parameters_default_to_the_first_fifty_items():
    body = _client().get("/widgets").json()

    assert body == {"items": [{"name": name} for name in WIDGETS], "total": 7, "limit": 50, "offset": 0}


def test_page_echoes_the_applied_window():
    body = _client().get("/widgets", params={"limit": 2, "offset": 3}).json()

    assert body == {"items": [{"name": "blue-3"}, {"name": "red-4"}], "total": 7, "limit": 2, "offset": 3}


@pytest.mark.parametrize(
    ("params", "offending"),
    [
        ({"limit": 0}, "limit"),
        ({"limit": 101}, "limit"),
        ({"offset": -1}, "offset"),
        ({"limt": 7}, "limt"),
    ],
)
def test_out_of_bounds_and_unknown_parameters_are_rejected_in_the_envelope(params: dict[str, int], offending: str):
    response = _client().get("/widgets", params=params)

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "validation_error"
    assert [error["loc"] for error in body["errors"]] == [["query", offending]]


def test_filter_subclass_documents_the_filter_next_to_the_page_parameters():
    parameters = _parameters("/filtered-widgets")

    assert set(parameters) == {"limit", "offset", "kind"}
    assert parameters["kind"]["description"] == "Only widgets of this kind."
    assert parameters["limit"]["schema"]["maximum"] == 100


def test_filter_subclass_parses_the_filter_and_the_page_parameters():
    body = _client().get("/filtered-widgets", params={"kind": "red", "limit": 2, "offset": 1}).json()

    assert body == {"items": [{"name": "red-2"}, {"name": "red-4"}], "total": 4, "limit": 2, "offset": 1}


def test_filter_subclass_still_rejects_unknown_parameters():
    response = _client().get("/filtered-widgets", params={"knd": "x"})

    assert response.status_code == 422


def test_page_schema_describes_the_envelope_and_its_item_type():
    schemas = _app().openapi()["components"]["schemas"]
    page = schemas["Page_WidgetResponse_"]

    assert page["required"] == ["items", "total", "limit", "offset"]
    assert page["properties"]["items"]["items"] == {"$ref": "#/components/schemas/WidgetResponse"}
    for name in ("items", "total", "limit", "offset"):
        assert page["properties"][name]["description"]


def test_page_of_takes_the_window_from_the_params():
    page = Page.of([WidgetResponse(name="a")], total=9, params=PageParams(limit=1, offset=4))

    assert page.model_dump() == {"items": [{"name": "a"}], "total": 9, "limit": 1, "offset": 4}


def _parameters(path: str) -> dict[str, dict[str, Any]]:
    return {parameter["name"]: parameter for parameter in _app().openapi()["paths"][path]["get"]["parameters"]}


def _client() -> TestClient:
    return TestClient(_app())


def _app() -> FastAPI:
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/widgets")
    async def list_widgets(params: PageQuery) -> Page[WidgetResponse]:
        window = WIDGETS[params.offset : params.offset + params.limit]
        return Page.of([WidgetResponse(name=name) for name in window], total=len(WIDGETS), params=params)

    @app.get("/filtered-widgets")
    async def list_filtered_widgets(params: WidgetListQuery) -> Page[WidgetResponse]:
        matching = [name for name in WIDGETS if params.kind is None or name.startswith(params.kind)]
        window = matching[params.offset : params.offset + params.limit]
        return Page.of([WidgetResponse(name=name) for name in window], total=len(matching), params=params)

    return app
