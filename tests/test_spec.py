from ghes_openapi_mcp.spec import (
    external_refs,
    load_spec,
    match_paths,
    parse_api_url,
    rank_candidates,
    resolve_ref_path,
    template_to_regex,
)

OAS3 = {
    "openapi": "3.0.3",
    "info": {"title": "Payments", "version": "1.2.0"},
    "servers": [{"url": "https://api.example.com/payments-api"}],
    "paths": {
        "/v1/payments": {"get": {}, "post": {}},
        "/v1/payments/{paymentId}": {"get": {}, "delete": {}},
        "/v1/payments/batch": {"post": {}},
    },
}

SWAGGER2 = {
    "swagger": "2.0",
    "info": {"title": "Accounts", "version": "3"},
    "host": "accounts.example.com:443",
    "basePath": "/api",
    "paths": {"/accounts/{id}": {"get": {}}},
}


def test_parse_api_url_variants():
    assert parse_api_url("https://api.example.com/v1/x/?a=1") == ("api.example.com", "/v1/x")
    assert parse_api_url("api.example.com/v1/x") == ("api.example.com", "/v1/x")
    assert parse_api_url("/v1//x/") == (None, "/v1/x")
    assert parse_api_url("v1/x") == (None, "/v1/x")
    assert parse_api_url("customers/v1/address?x=1") == (None, "/customers/v1/address")
    assert parse_api_url("v1.0/customers") == (None, "/v1.0/customers")
    assert parse_api_url("localhost:8080/v1/x") == ("localhost", "/v1/x")
    assert parse_api_url("10.0.0.5/v1/x") == ("10.0.0.5", "/v1/x")


def test_template_to_regex_escapes_literals():
    assert template_to_regex("/v1/a.b/{id}") == r"/v1/a\.b/[^/]+"


def test_exact_match_with_server_base_path_and_host():
    matches = match_paths(OAS3, "https://api.example.com/payments-api/v1/payments/abc-123")
    assert matches[0].path == "/v1/payments/{paymentId}"
    assert matches[0].match_type == "exact"
    assert matches[0].host_matched is True


def test_literal_path_beats_templated_path():
    matches = match_paths(OAS3, "/v1/payments/batch", method="POST")
    assert [m.path for m in matches][:2] == ["/v1/payments/batch", "/v1/payments/{paymentId}"]


def test_method_mismatch_is_penalised():
    matches = match_paths(OAS3, "/v1/payments/batch", method="GET")
    assert matches[0].path == "/v1/payments/{paymentId}"
    assert matches[0].method_matched is True


def test_suffix_match_through_gateway_prefix():
    matches = match_paths(OAS3, "https://gw.internal/some/gateway/v1/payments")
    assert matches[0].path == "/v1/payments"
    assert matches[0].match_type == "suffix"


def test_swagger2_base_path_and_host():
    matches = match_paths(SWAGGER2, "https://accounts.example.com/api/accounts/42")
    assert matches[0].match_type == "exact"
    assert matches[0].host_matched is True


def test_partial_path_matches_inside_full_path():
    doc = {"openapi": "3.0.0", "paths": {"/customers/{id}/v1/address": {"get": {}}}}
    for fragment in ["v1/address", "customers/42/v1", "address"]:
        matches = match_paths(doc, fragment)
        assert matches and matches[0].match_type == "partial", fragment
    assert match_paths(doc, "42") == []  # a lone value never matches a lone {param}


def test_exact_beats_partial():
    doc = {"openapi": "3.0.0", "paths": {"/v1/address": {"get": {}}, "/customers/v1/address": {"get": {}}}}
    assert [m.match_type for m in match_paths(doc, "v1/address")] == ["exact", "partial"]


def test_no_match():
    assert match_paths(OAS3, "/v2/refunds") == []


def test_load_spec_accepts_yaml_and_json_only_for_specs():
    assert load_spec("openapi: 3.0.0\npaths: {}\n")["openapi"] == "3.0.0"
    assert load_spec('{"swagger": "2.0", "paths": {}}')["swagger"] == "2.0"
    assert load_spec("name: not-a-spec\n") is None
    assert load_spec("openapi: [unclosed") is None


def test_rank_candidates_orders_and_filters():
    entries = [
        {"type": "blob", "path": "config/app.yaml"},
        {"type": "blob", "path": "specs/payments.yaml"},
        {"type": "blob", "path": "payments/openapi.yaml"},
        {"type": "blob", "path": "node_modules/x/openapi.json"},
        {"type": "blob", "path": "package.json"},
        {"type": "blob", "path": "README.md"},
        {"type": "tree", "path": "specs"},
    ]
    assert [e["path"] for e in rank_candidates(entries)] == [
        "payments/openapi.yaml",
        "specs/payments.yaml",
        "config/app.yaml",
    ]
    assert [e["path"] for e in rank_candidates(entries, "specs")] == ["specs/payments.yaml"]


def test_external_refs_and_resolution():
    doc = {
        "a": {"$ref": "#/components/schemas/X"},
        "b": [{"$ref": "./schemas/pay.yaml#/Pay"}],
        "c": {"$ref": "https://example.com/x.yaml"},
    }
    assert external_refs(doc) == {"./schemas/pay.yaml"}
    assert resolve_ref_path("api/v1/openapi.yaml", "../common/err.yaml") == "api/common/err.yaml"
