#!/usr/bin/env python3

# Copyright 2020 ETH Zurich and University of Bologna.
# Licensed under the Apache License, Version 2.0, see LICENSE for details.
# SPDX-License-Identifier: Apache-2.0

# Simplified version of occamygen.py https://github.com/pulp-platform/snitch/blob/master/util/occamygen.py

import argparse
import fcntl
import hjson
import pathlib
import sys
import re
import logging
from jsonref import JsonRef
from mako.template import Template
import load_config
from xheep import BusType
from cpu.cpu import CPU


# ANSI color codes for pretty printing
class Colors:
    BLUE = "\033[94m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    RESET = "\033[0m"
    BOLD = "\033[1m"


# Compile a regex to trim trailing whitespaces on lines.
re_trailws = re.compile(r"[ \t\r]+$", re.MULTILINE)


def string2int(hex_json_string):
    return (hex_json_string.split("x")[1]).split(",")[0]


def write_template(tpl_path, outfile, **kwargs):
    if tpl_path:
        tpl_path = pathlib.Path(tpl_path).absolute()
        if tpl_path.exists():
            tpl = Template(filename=str(tpl_path))
            if outfile:
                filename = outfile
            else:
                filename = tpl_path.with_suffix("")

            code = tpl.render_unicode(**kwargs, strict_undefined=True)
            code = re_trailws.sub("", code)
            # Import lazily so the traditional x-heep flow remains usable when
            # this file is copied as a standalone generator script.
            from build_manifest import atomic_write_text

            filename = pathlib.Path(filename)
            atomic_write_text(filename, code)
            return filename.resolve()
        else:
            raise FileNotFoundError("Template file not found: {0}".format(tpl_path))
    else:
        raise FileNotFoundError("Template file not provided")


def generate_xheep(args):

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG)

    # MOSAIC config mode — load from mosaic.yaml instead of HJSON
    if args.mosaic_config:
        from mosaic_config import load_mosaic_yaml, mosaic_to_xheep_kwargs

        cfg = load_mosaic_yaml(pathlib.PurePath(str(args.mosaic_config)))
        # Return kwargs directly; no further HJSON loading needed.
        # The base HJSON (default configs/general.hjson) provides the
        # peripheral/memory/interrupt infrastructure that mosaic.yaml overlays
        # with the multi-core CPU topology.
        base_cfg = (
            str(args.base_config) if args.base_config else "configs/general.hjson"
        )
        return mosaic_to_xheep_kwargs(
            cfg, base_config=base_cfg, pads_cfg_path=str(args.pads_cfg)
        )

    # Load general configuration file.
    # This can be either the Python or HJSON config file.
    # If using the Python config file, the HJSON parameters that are supported by Python will be ignored
    # except for the peripherals. Any peripheral not configured in Python will be added from the HJSON config.
    if args.python_config != None and args.python_config != "":
        xheep = load_config.load_cfg_file(pathlib.PurePath(str(args.python_config)))
    else:
        xheep = load_config.load_cfg_file(pathlib.PurePath(str(args.config)))

    # We still need to load from the HJSON config the configuration options that are not yet supported in the Python model of X-HEEP
    with open(args.config, "r") as file:
        try:
            srcfull = file.read()
            config = hjson.loads(srcfull, use_decimal=True)
            config = JsonRef.replace_refs(config)
        except ValueError:
            raise SystemExit(sys.exc_info()[1])

    # Load pads HJSON configuration file
    pad_ring = load_config.load_pad_cfg(pathlib.PurePath(str(args.pads_cfg)), xheep)
    if pad_ring is None:
        exit(f"Error loading pads configuration file: {args.pads_cfg}")
    xheep.set_padring(pad_ring)

    try:
        has_spi_slave = 1 if config["debug"]["has_spi_slave"] == "yes" else 0
    except KeyError:
        has_spi_slave = 0

    if args.bus != None and args.bus != "":
        xheep.set_bus_type(BusType(args.bus))

    if args.memorybanks != None and args.memorybanks != "":
        xheep.memory_ss().override_ram_banks(int(args.memorybanks))

    if args.memorybanks_il != None and args.memorybanks_il != "":
        xheep.memory_ss().override_ram_banks_il(int(args.memorybanks_il))

    # Override CPU setting if specified in the make arguments
    if args.cpu != None and args.cpu != "":
        xheep.set_cpu(CPU(args.cpu))

    debug_start_address = string2int(config["debug"]["address"])
    if int(debug_start_address, 16) < int("10000", 16):
        exit("debug start address must be greater than 0x10000")

    debug_size_address = string2int(config["debug"]["length"])
    ext_slave_start_address = string2int(config["ext_slaves"]["address"])
    ext_slave_size_address = string2int(config["ext_slaves"]["length"])

    flash_mem_start_address = string2int(config["flash_mem"]["address"])
    flash_mem_size_address = string2int(config["flash_mem"]["length"])

    stack_size = string2int(config["linker_script"]["stack_size"])
    heap_size = string2int(config["linker_script"]["heap_size"])

    plic_used_n_interrupts = len(config["interrupts"]["list"])
    plit_n_interrupts = config["interrupts"]["number"]
    ext_int_list = {
        f"EXT_INTR_{k}": v
        for k, v in enumerate(range(plic_used_n_interrupts, plit_n_interrupts))
    }

    interrupts = {**config["interrupts"]["list"], **ext_int_list}

    # Here the xheep system is built,
    # The missing gaps are filled, like the missing end address of the data section.
    xheep.build()

    # Validate the configuration, performing some sanity checks
    xheep.validate()

    if (
        int(stack_size, 16) + int(heap_size, 16)
    ) > xheep.memory_ss().ram_size_address():
        exit(
            "The stack and heap section must fit in the RAM size, instead they take "
            + str(int(stack_size, 16) + int(heap_size, 16))
            + " bytes while RAM size is "
            + str(xheep.memory_ss().ram_size_address())
            + " bytes."
        )

    kwargs = {
        "xheep": xheep,
        "debug_start_address": debug_start_address,
        "debug_size_address": debug_size_address,
        "has_spi_slave": has_spi_slave,
        "ext_slave_start_address": ext_slave_start_address,
        "ext_slave_size_address": ext_slave_size_address,
        "flash_mem_start_address": flash_mem_start_address,
        "flash_mem_size_address": flash_mem_size_address,
        "stack_size": stack_size,
        "heap_size": heap_size,
        "plic_used_n_interrupts": plic_used_n_interrupts,
        "plit_n_interrupts": plit_n_interrupts,
        "interrupts": interrupts,
    }

    return kwargs


def main():
    parser = argparse.ArgumentParser(prog="mcugen")

    parser.add_argument(
        "--config",
        metavar="file",
        type=str,
        required=False,
        default="",
        help="X-HEEP general HJSON configuration",
    )

    parser.add_argument(
        "--mosaic_config",
        metavar="file",
        type=str,
        nargs="?",
        default="",
        help="MOSAIC-SoC YAML configuration (alternative to --config). If set, --config is not required.",
    )

    parser.add_argument(
        "--base_config",
        metavar="file",
        type=str,
        required=False,
        default="",
        help="Base x-heep HJSON config used for peripheral/memory infrastructure in MOSAIC mode (default: configs/general.hjson).",
    )

    parser.add_argument(
        "--python_config",
        metavar="file",
        type=str,
        required=False,
        nargs="?",
        default="",
        help="X-HEEP general Python configuration",
    )

    parser.add_argument(
        "--pads_cfg",
        "-pc",
        metavar="file",
        type=str,
        required=True,
        help="Pads HJSON configuration",
    )

    parser.add_argument(
        "--cpu",
        metavar="cv32e20,cv32e40p,cv32e40x,cv32e40px",
        nargs="?",
        default="",
        help="CPU type (default value from cfg file)",
    )

    parser.add_argument(
        "--bus",
        metavar="onetoM,NtoM",
        nargs="?",
        default="",
        help="Bus type (default value from cfg file)",
    )

    parser.add_argument(
        "--memorybanks",
        metavar="from 2 to 16",
        nargs="?",
        default="",
        help="Number of 32KB Banks (default value from cfg file)",
    )

    parser.add_argument(
        "--memorybanks_il",
        metavar="0, 2, 4 or 8",
        nargs="?",
        default="",
        help="Number of interleaved memory banks (default value from cfg file)",
    )

    parser.add_argument(
        "-v", "--verbose", help="increase output verbosity", action="store_true"
    )

    parser.add_argument(
        "--outfile",
        "-o",
        type=pathlib.Path,
        required=False,
        help="Target filename. If not provided, the template filename will be used as the output filename.",
    )

    parser.add_argument(
        "--outtpl",
        "-ot",
        type=str,
        required=True,
        help="Target template filename or comma-separated list of template filenames",
    )

    parser.add_argument(
        "--externaltpl",
        "-et",
        type=str,
        required=False,
        help="External template filename or comma-separated list of external template filenames. "
        "Intended for templates that are not in the X-HEEP repository, e.g. in the user's CHEEP repository.",
    )

    parser.add_argument(
        "--output-root",
        type=pathlib.Path,
        default=pathlib.Path("build/mosaic"),
        help="Base directory for isolated MOSAIC bundles (default: build/mosaic). "
        "Each config renders below <soc-name>-<input-hash>/generated.",
    )

    parser.add_argument(
        "--legacy-in-place",
        action="store_true",
        help="Render MOSAIC templates beside their .tpl files (legacy compatibility). "
        "The default MOSAIC flow is isolated and never overwrites those files.",
    )

    args = parser.parse_args()

    repo_root = pathlib.Path(__file__).resolve().parents[2]
    isolated = bool(args.mosaic_config) and not args.legacy_in_place
    bundle = None
    generated_root = None
    manifest_path = None
    generated_records = []
    lock_file = None
    if isolated:
        from build_manifest import bundle_paths

        base_cfg = str(args.base_config) if args.base_config else "configs/general.hjson"
        bundle = bundle_paths(
            args.mosaic_config,
            base_cfg,
            args.pads_cfg,
            repo_root,
            args.output_root,
        )
        generated_root = bundle["generated"]
        manifest_path = bundle["manifest"]
        generated_root.mkdir(parents=True, exist_ok=True)
        # Same-config concurrent invocations share deterministic output paths.
        # Serialize rendering and use atomic file replacement so they cannot
        # expose partial RTL; different config hashes proceed independently.
        lock_file = open(bundle["bundle"] / ".generation.lock", "w")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        print(f"MOSAIC_BUILD_KEY={bundle['key']}")
        print(f"MOSAIC_BUILD_DIR={bundle['bundle']}")

    # Pin and lock the complete config/source identity before parsing any
    # generator input. The final manifest rehashes it and aborts on drift.
    print(f"{Colors.BLUE}[MCU-GEN]{Colors.RESET} Generating X-HEEP configuration...")
    kwargs = generate_xheep(args)
    print(
        f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} X-HEEP configuration generated successfully"
    )

    def output_for_template(tpl_path):
        tpl_path = pathlib.Path(tpl_path).resolve()
        if not isolated:
            text_path = str(tpl_path)
            return pathlib.Path(text_path[:-4] if text_path.endswith(".tpl") else text_path)
        from build_manifest import logical_output_path

        return generated_root / logical_output_path(tpl_path, repo_root)

    def record_generated(tpl_path, output_path):
        if not isolated:
            return
        from build_manifest import logical_output_path

        logical = logical_output_path(pathlib.Path(tpl_path), repo_root)
        generated_records.append(
            {
                "logical_path": str(logical),
                "path": str(pathlib.Path(output_path).resolve()),
                "template": str(pathlib.Path(tpl_path).resolve()),
            }
        )

    # Handle single template or multiple templates
    outtpl_list = [t for t in re.split(r"[,\s]+", args.outtpl or "") if t]
    externaltpl_list = [t for t in re.split(r"[,\s]+", args.externaltpl or "") if t]

    if len(outtpl_list) == 1:  # Single template case
        if externaltpl_list:
            parser.error("Cannot specify --externaltpl when using a single template.")
        print(
            f"{Colors.BLUE}[MCU-GEN]{Colors.RESET} Processing template: {Colors.BOLD}{outtpl_list[0]}{Colors.RESET}"
        )
        target = args.outfile if args.outfile is not None else output_for_template(outtpl_list[0])
        written = write_template(pathlib.Path(outtpl_list[0]), target, **kwargs)
        record_generated(outtpl_list[0], written)
        print(f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} Template processed successfully")
    else:
        # Multiple templates case
        if args.outfile is not None:
            parser.error(
                "Cannot specify --outfile when using multiple templates. Filenames will be generated from template names."
            )
        print(
            f"{Colors.BLUE}[MCU-GEN]{Colors.RESET} Processing {Colors.BOLD}{len(outtpl_list)}{Colors.RESET} templates..."
        )
        for idx, tpl in enumerate(outtpl_list, 1):
            tpl_path = pathlib.Path(tpl.strip())
            # Generate output filename from template name by removing .tpl extension
            generated_outfile = output_for_template(tpl_path)
            print(
                f"{Colors.YELLOW}[MCU-GEN]{Colors.RESET} [{idx}/{len(outtpl_list)}] {tpl_path.name} {Colors.YELLOW}→{Colors.RESET} {generated_outfile.name}"
            )
            written = write_template(tpl_path, generated_outfile, **kwargs)
            record_generated(tpl_path, written)
        print(
            f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} All templates processed successfully"
        )
        # Process external templates if provided
        if externaltpl_list:
            print(
                f"{Colors.BLUE}[MCU-GEN]{Colors.RESET} Processing {Colors.BOLD}{len(externaltpl_list)}{Colors.RESET} external templates..."
            )
            for idx, tpl in enumerate(externaltpl_list, 1):
                tpl_path = pathlib.Path(tpl.strip())
                # Generate output filename from template name by removing .tpl extension
                generated_outfile = output_for_template(tpl_path)
                print(
                    f"{Colors.YELLOW}[MCU-GEN]{Colors.RESET} [{idx}/{len(externaltpl_list)}] {tpl_path.name} {Colors.YELLOW}→{Colors.RESET} {generated_outfile.name}"
                )
                written = write_template(tpl_path, generated_outfile, **kwargs)
                record_generated(tpl_path, written)
            print(
                f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} All external templates processed successfully"
            )

    # MOSAIC fabric step: emit the FlooNoC fabric via floogen when the config
    # selects `bus: floonoc`, or stub files otherwise, so the checked-in
    # mosaic:ip:floonoc_fabric FuseSoC core always resolves.
    if args.mosaic_config:
        import floonoc_gen

        fabric_out = (
            generated_root / floonoc_gen.FABRIC_DIR if isolated else None
        )
        floonoc_gen.generate(
            kwargs["mosaic_cfg"],
            kwargs["num_harts"],
            kwargs["xheep"].memory_ss().ram_size_address(),
            repo_root,
            output_dir=fabric_out,
            work_dir=(bundle["bundle"] / "floonoc-work" if isolated else None),
        )
        if isolated:
            for filename in ("floo_mosaic_noc_pkg.sv", "floo_mosaic_noc.sv"):
                output = fabric_out / filename
                generated_records.append(
                    {
                        "logical_path": str(pathlib.Path(floonoc_gen.FABRIC_DIR) / filename),
                        "path": str(output.resolve()),
                        "generator": "floonoc_gen",
                    }
                )
        print(f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} FlooNoC fabric step done")

        # OpenTitan's vendored PLIC snapshot has NumTarget=1 baked into its
        # generated register types.  A true multi-hart build needs one claim,
        # enable, threshold and MSIP context per resolved hart, so generate a
        # self-consistent PLIC closure inside the isolated configuration bundle
        # and let FuseSoC staging overlay these logical source paths.
        if isolated:
            import plic_gen

            plic_out = generated_root / plic_gen.LOGICAL_RTL_DIR
            plic_outputs = plic_gen.generate(
                kwargs["num_harts"],
                repo_root,
                plic_out,
                bundle["bundle"] / "plic-work",
            )
            for output in plic_outputs:
                generated_records.append(
                    {
                        "logical_path": str(plic_gen.LOGICAL_RTL_DIR / output.name),
                        "path": str(output.resolve()),
                        "generator": "plic_gen",
                    }
                )
            print(
                f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} "
                f"Generated {kwargs['num_harts']}-target PLIC"
            )
        elif kwargs["num_harts"] > 1:
            parser.error(
                "--legacy-in-place cannot represent a configuration-sized "
                "multi-target PLIC; use the default isolated MOSAIC output"
            )

        # Firmware/BSP artifacts are another projection of the same resolved
        # MosaicConfig as the RTL.  Keep them inside the content-addressed
        # bundle so concurrent/different configurations cannot share stale
        # topology headers, ISA flags, link maps, or boot-image manifests.
        if isolated:
            import software_gen

            software_out = generated_root / "sw"
            software_gen.generate_software_artifacts(
                kwargs["mosaic_cfg"], software_out
            )
            for output in sorted(path for path in software_out.rglob("*") if path.is_file()):
                relative = output.relative_to(software_out)
                generated_records.append(
                    {
                        "logical_path": str(
                            pathlib.Path("sw/firmware/generated") / relative
                        ),
                        "path": str(output.resolve()),
                        "generator": "software_gen",
                    }
                )
            print(
                f"{Colors.GREEN}[MCU-GEN]{Colors.RESET} "
                "Generated topology-specific software contract"
            )

    if isolated:
        from build_manifest import resolved_manifest, write_manifest

        base_cfg = str(args.base_config) if args.base_config else "configs/general.hjson"
        manifest = resolved_manifest(
            kwargs=kwargs,
            config_path=args.mosaic_config,
            base_config=base_cfg,
            pads_cfg=args.pads_cfg,
            repo_root=repo_root,
            output_root=args.output_root,
            generated_files=generated_records,
            pinned_identity=bundle,
        )
        write_manifest(manifest_path, manifest)
        print(f"MOSAIC_MANIFEST={manifest_path}")
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


if __name__ == "__main__":
    main()
