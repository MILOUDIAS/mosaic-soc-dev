"""`soc.schema`: a config states which rules accepted it.

`allowed_soc` is a single evolving frozenset, so before this key a config could
not record the schema it was authored against. Two consequences, both silent:
a config written months ago and one written today are indistinguishable, and a
config rots as the validator changes underneath it -- keys appear, meanings
tighten, and nothing says which version actually accepted the file.

The discipline is OpenADA's, whose operation profiles are immutable and
versioned by identifier: changing a required field, a value's meaning, or a
closed shape requires a NEW identifier while the old one keeps working.

What this file pins:

- omitting the key still works, so every existing config is valid unchanged;
- an unknown version is refused BY NAME, not as a wall of unknown-key errors
  from a validator that was never meant to read that file;
- the frozen tapeout config declares its schema, so the freeze artifact is
  self-describing;
- generated configs declare it too, so new files do not inherit the old
  ambiguity.
"""

import subprocess
import sys

import yaml

from harness.core import REPO_ROOT
from util.xheep_gen.core_registry import (
    CURRENT_SCHEMA,
    KNOWN_SCHEMAS,
    validate_soc_config,
)

MINIMAL = {
    "name": "t",
    "cores": [{"ip": "cv32e20", "isa": "rv32emc", "count": 1, "role": "titan"}],
}


def soc(**overrides):
    return {"soc": dict(MINIMAL, **overrides)}


def test_omitting_the_schema_key_is_still_valid():
    """Every config in the repo predates this key. None may break."""
    assert validate_soc_config(soc()) == []


def test_the_current_schema_validates():
    assert validate_soc_config(soc(schema=CURRENT_SCHEMA)) == []


def test_an_unknown_schema_is_refused_by_name():
    """Not as twenty unknown-key errors -- by name, with the known list.

    A v2 config fed to a v1 validator would otherwise produce a diagnosis of
    the wrong problem: every new key reported as unknown, and no indication
    that the file was simply written for a different format.
    """
    errors = validate_soc_config(soc(schema="mosaic/v2"))
    assert len(errors) == 1
    assert "mosaic/v2" in errors[0]
    assert "not a schema this generator implements" in errors[0]
    assert str(sorted(KNOWN_SCHEMAS)) in errors[0]


def test_a_non_string_schema_is_refused():
    errors = validate_soc_config(soc(schema=7))
    assert errors and "soc.schema must be a string" in errors[0]


def test_the_current_schema_is_a_known_schema():
    assert CURRENT_SCHEMA in KNOWN_SCHEMAS


def test_the_frozen_tapeout_config_declares_its_schema():
    """The freeze artifact must say which rules accepted it."""
    frozen = yaml.safe_load(
        (REPO_ROOT / "configs/mosaic_tapeout_ultra.yaml").read_text())
    assert frozen["soc"]["schema"] == CURRENT_SCHEMA


def test_generated_configs_declare_their_schema(tmp_path):
    out = tmp_path / "generated.yaml"
    result = subprocess.run(
        [sys.executable, "-m", "harness", "config-author", "generate",
         "--name", "probe", "--core", "serv:1:titan", "--output", str(out)],
        cwd=str(REPO_ROOT), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yaml.safe_load(out.read_text())["soc"]["schema"] == CURRENT_SCHEMA


def test_every_shipped_config_declares_a_known_schema_or_none():
    """A shipped config may omit the key, but must never name an unknown one."""
    wrong = {}
    for path in sorted((REPO_ROOT / "configs").glob("mosaic*.yaml")):
        data = yaml.safe_load(path.read_text()) or {}
        declared = (data.get("soc") or {}).get("schema")
        if declared is not None and declared not in KNOWN_SCHEMAS:
            wrong[path.name] = declared
    assert not wrong, f"configs naming an unknown schema: {wrong}"
