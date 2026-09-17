from app.api.v1.common import ApiModel, ErrorResponse, FieldError


class WidgetResponse(ApiModel):
    name: str
    """Display name of the widget."""
    size: int = 1


def test_api_model_turns_a_field_docstring_into_its_description():
    properties = WidgetResponse.model_json_schema()["properties"]

    assert properties["name"]["description"] == "Display name of the widget."
    assert "description" not in properties["size"]


def test_error_envelope_fields_are_described():
    for schema in (ErrorResponse, FieldError):
        for name, field in schema.model_json_schema()["properties"].items():
            assert field["description"], f"{schema.__name__}.{name}"
