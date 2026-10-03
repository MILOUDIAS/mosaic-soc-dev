"""Namespace for MOSAIC generator-side modules.

This file exists so `util.xheep_gen` can be declared as a distribution package.
`harness.core` imports `util.xheep_gen.core_registry` -- the single source of
core capabilities and config validation, shared so that a config accepted by
the RTL generator is accepted by the harness with identical semantics -- which
makes it part of the installed application, not merely a build-time script.
"""
