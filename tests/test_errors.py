"""Error taxonomy: codes, index, inheritance, and status mapping."""

import inspect

import pytest

from webanchor import errors
from webanchor.errors import (
    ERROR_BY_CODE,
    ContentError,
    FetchError,
    Forbidden,
    NotFound,
    PolicyError,
    RateLimited,
    UnexpectedStatus,
    UpstreamUnavailable,
    WebAnchorError,
    from_status,
)

CONCRETE = [
    errors.RateLimited,
    errors.UpstreamUnavailable,
    errors.NotFound,
    errors.Forbidden,
    errors.UnexpectedStatus,
    errors.NetworkError,
    errors.EmptyContent,
    errors.ContentTooLarge,
    errors.NotTextual,
    errors.BotWallDetected,
    errors.SoftErrorPage,
    errors.UnstableContent,
    errors.PolicyMismatch,
]


def _all_error_classes():
    return [
        obj
        for _, obj in inspect.getmembers(errors, inspect.isclass)
        if issubclass(obj, WebAnchorError)
    ]


def test_every_class_has_non_empty_code():
    for cls in _all_error_classes():
        assert isinstance(cls.code, str)
        assert cls.code.strip() != ""


def test_codes_are_unique():
    codes = [cls.code for cls in _all_error_classes()]
    assert len(codes) == len(set(codes))


@pytest.mark.parametrize("cls", CONCRETE, ids=lambda c: c.__name__)
def test_error_by_code_round_trips(cls):
    assert cls.code in ERROR_BY_CODE
    assert ERROR_BY_CODE[cls.code] is cls


def test_error_by_code_covers_every_class():
    for cls in _all_error_classes():
        assert ERROR_BY_CODE.get(cls.code) is cls


@pytest.mark.parametrize(
    "cls,parent",
    [
        (errors.RateLimited, FetchError),
        (errors.NetworkError, FetchError),
        (errors.EmptyContent, ContentError),
        (errors.BotWallDetected, ContentError),
        (errors.UnstableContent, PolicyError),
        (errors.PolicyMismatch, PolicyError),
    ],
    ids=lambda x: getattr(x, "__name__", str(x)),
)
def test_inheritance_chain(cls, parent):
    assert issubclass(cls, parent)
    assert issubclass(parent, WebAnchorError)
    assert issubclass(cls, WebAnchorError)
    assert isinstance(cls("x"), WebAnchorError)


@pytest.mark.parametrize("cls", CONCRETE, ids=lambda c: c.__name__)
def test_str_contains_code_and_detail(cls):
    err = cls("something broke", url="https://example.com/a")
    text = str(err)
    assert cls.code in text
    assert "something broke" in text


def test_attributes_exposed():
    err = errors.NotFound("missing", url="https://example.com/x")
    assert err.code == "fetch.not_found"
    assert err.detail == "missing"
    assert err.url == "https://example.com/x"


def test_as_dict_is_all_strings():
    err = errors.NotFound("missing", url="https://example.com/x")
    data = err.as_dict()
    assert data == {
        "code": "fetch.not_found",
        "detail": "missing",
        "url": "https://example.com/x",
    }
    assert all(isinstance(v, str) for v in data.values())


def test_as_dict_url_defaults_to_empty_string():
    data = errors.NetworkError("boom").as_dict()
    assert data["url"] == ""
    assert all(isinstance(v, str) for v in data.values())


def test_raise_and_catch_by_base():
    with pytest.raises(WebAnchorError):
        raise errors.BotWallDetected("challenge page")


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, None),
        (204, None),
        (301, UnexpectedStatus),
        (401, Forbidden),
        (403, Forbidden),
        (404, NotFound),
        (429, RateLimited),
        (500, UpstreamUnavailable),
        (503, UpstreamUnavailable),
        (599, UpstreamUnavailable),
        (999, UnexpectedStatus),
    ],
)
def test_from_status_mapping(status, expected):
    result = from_status(status, url="https://example.com")
    if expected is None:
        assert result is None
    else:
        assert type(result) is expected
        assert isinstance(result, FetchError)
        assert result.url == "https://example.com"


def test_from_status_url_optional():
    err = from_status(429)
    assert isinstance(err, RateLimited)
    assert err.url is None
    assert err.as_dict()["url"] == ""
