# oh-my-soc — Agentic harness for MOSAIC-SoC
# Based on oh-my-pi, adapted for MOSAIC-SoC EDA flows.

"""oh-my-soc: agentic harness for MOSAIC-SoC EDA flows.

Based on oh-my-pi (general-purpose agentic harness pattern),
customized for the MOSAIC-SoC multi-core RISC-V SoC generator.

Skills:
  - config-author: generate/validate mosaic.yaml configs
  - flow-runner:   invoke EDA flows with structured reporting
  - drc-triage:    parse DRC/LVS reports and propose fixes
  - doc-gen:       generate docs from configs and run artifacts
"""

__version__ = "0.3.0"
__original__ = "oh-my-pi"  # upstream pattern this is based on
