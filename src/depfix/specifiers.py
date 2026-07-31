"""Compatibility import for the permanent unified source parser."""

from .sources import SourceInfo as ParsedSpecifier
from .sources import parse_source as parse_specifier

__all__ = ["ParsedSpecifier", "parse_specifier"]
