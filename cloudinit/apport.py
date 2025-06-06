# Copyright (C) 2017 Canonical Ltd.
#
# This file is part of cloud-init. See LICENSE file for license information.

"""Cloud-init apport interface"""

import json
import logging
import os
from typing import Dict

from cloudinit.cmd.devel.logs import (
    INSTALLER_APPORT_FILES,
    INSTALLER_APPORT_SENSITIVE_FILES,
)
from cloudinit.cmd.status import is_cloud_init_enabled

try:
    from apport.hookutils import (
        attach_file,
        attach_file_if_exists,
        attach_root_command_outputs,
        root_command_output,
    )

    has_apport = True
except ImportError:
    has_apport = False


KNOWN_CLOUD_NAMES = [
    "AliYun",
    "AltCloud",
    "Akamai",
    "Amazon - Ec2",
    "Azure",
    "Bigstep",
    "Brightbox",
    "CloudCIX",
    "CloudSigma",
    "CloudStack",
    "DigitalOcean",
    "E24Cloud",
    "GCE - Google Compute Engine",
    "Huawei Cloud",
    "Exoscale",
    "Hetzner Cloud",
    "NWCS",
    "IBM - (aka SoftLayer or BlueMix)",
    "LXD",
    "MAAS",
    "NoCloud",
    "OpenNebula",
    "OpenStack",
    "Oracle",
    "OVF",
    "RbxCloud - (HyperOne, Rootbox, Rubikon)",
    "OpenTelekomCloud",
    "Samsung Cloud Platform",
    "SAP Converged Cloud",
    "Scaleway",
    "SmartOS",
    "UpCloud",
    "VMware",
    "Vultr",
    "ZStack",
    "Outscale",
    "WSL",
    "Other",
]


def attach_cloud_init_logs(report, ui=None, include_sensitive=False):
    """Attach cloud-init logs and tarfile from 'cloud-init collect-logs'."""
    attach_root_command_outputs(  # pyright: ignore
        report,
        {
            "cloud-init-log-warnings": (
                'egrep -i "warn|error" /var/log/cloud-init.log'
            ),
        },
    )
    command = [
        "cloud-init",
        "collect-logs",
        "-t",
        "/tmp/cloud-init-logs.tgz",
    ]
    if not include_sensitive:
        command.append("--redact")
    root_command_output(command)
    attach_file(report, "/tmp/cloud-init-logs.tgz", "logs.tgz")


def attach_hwinfo(report, ui=None):
    """Optionally attach hardware info from lshw."""
    prompt = (
        "Your device details (lshw) may be useful to developers when"
        " addressing this bug, but gathering it requires admin privileges."
        " Would you like to include this info?"
    )
    if ui and ui.yesno(prompt):
        attach_root_command_outputs(report, {"lshw.txt": "lshw"})


def attach_installer_files(report, ui=None, include_sensitive=False):
    """Attach any subiquity installer logs config.

    To support decoupling apport integration from installer config/logs,
    we eventually want to either source this function or APPORT_FILES
    attribute from subiquity  and/or ubuntu-desktop-installer package-hooks
    python modules.
    """
    for apport_file in INSTALLER_APPORT_FILES:
        realpath = os.path.realpath(apport_file.path)
        attach_file_if_exists(report, realpath, apport_file.label)
    if include_sensitive:
        for apport_file in INSTALLER_APPORT_SENSITIVE_FILES:
            realpath = os.path.realpath(apport_file.path)
            attach_file_if_exists(report, realpath, apport_file.label)


def attach_ubuntu_pro_info(report, ui=None):
    """Attach ubuntu pro logs and tag if keys present in user-data."""
    realpath = os.path.realpath("/var/log/ubuntu-advantage.log")
    attach_file_if_exists(report, realpath)  # pyright: ignore
    if os.path.exists(realpath):
        report.setdefault("Tags", "")
        if report["Tags"]:
            report["Tags"] += " "
        report["Tags"] += "ubuntu-pro"


def can_attach_sensitive(report, ui=None) -> bool:
    """Optionally provide user-data if desired."""
    if not ui:
        return False
    prompt = (
        "Your user data, cloud-config, network config, or autoinstall "
        "files can optionally be provided and could be useful to "
        "developers when addressing this bug. However, this data should "
        "not be included if it contains any sensitive data such as "
        "passwords and secrets. Gathering it requires admin privileges. "
        "Would you like to include this info?"
    )
    response = ui.yesno(prompt)
    if response is None:
        raise StopIteration  # User cancelled
    return response


def add_bug_tags(report):
    """Add any appropriate tags to the bug."""
    new_tags = []
    if report.get("CurtinError"):
        new_tags.append("curtin")
    if report.get("SubiquityLog"):
        new_tags.append("subiquity")
    if "JournalErrors" in report.keys():
        errors = report["JournalErrors"]
        if "Breaking ordering cycle" in errors:
            new_tags.append("systemd-ordering")
    if report.get("UdiLog"):
        new_tags.append("ubuntu-desktop-installer")
    if new_tags:
        report.setdefault("Tags", "")
        if report["Tags"]:
            report["Tags"] += " "
        report["Tags"] += " ".join(new_tags)


def add_info(report, ui):
    """This is an entry point to run cloud-init's package-specific hook

    Distros which want apport support will have a cloud-init package-hook at
    /usr/share/apport/package-hooks/cloud-init.py which defines an add_info
    function and returns the result of cloudinit.apport.add_info(report, ui).
    """
    if not has_apport:
        raise RuntimeError(
            "No apport imports discovered. Apport functionality disabled"
        )
    include_sensitive = can_attach_sensitive(report, ui)
    attach_cloud_init_logs(report, ui, include_sensitive)
    attach_hwinfo(report, ui)
    attach_installer_files(report, ui, include_sensitive)
    attach_ubuntu_pro_info(report, ui)
    add_bug_tags(report)
    return True


def _get_azure_data(ds_data) -> Dict[str, str]:
    compute = ds_data.get("meta_data", {}).get("imds", {}).get("compute")
    if not compute:
        return {}
    name_to_report_map = {
        "publisher": "ImagePublisher",
        "offer": "ImageOffer",
        "sku": "ImageSKU",
        "version": "ImageVersion",
        "vmSize": "VMSize",
    }
    azure_data = {}
    for src_key, report_key_name in name_to_report_map.items():
        azure_data[report_key_name] = compute[src_key]
    return azure_data


def _get_ec2_data(ds_data) -> Dict[str, str]:
    document = (
        ds_data.get("dynamic", {}).get("instance-identity", {}).get("document")
    )
    if not document:
        return {}
    wanted_keys = {
        "architecture",
        "billingProducts",
        "imageId",
        "instanceType",
        "region",
    }
    return {
        key: value for key, value in document.items() if key in wanted_keys
    }


PLATFORM_SPECIFIC_INFO = {"azure": _get_azure_data, "ec2": _get_ec2_data}


def add_datasource_specific_info(report, platform: str, ds_data) -> None:
    """Add datasoure specific information from the ds dictionary.

    ds_data contains the "ds" entry from data from
    /run/cloud/instance-data.json.
    """
    platform_info = PLATFORM_SPECIFIC_INFO.get(platform)
    if not platform_info:
        return
    retrieved_data = platform_info(ds_data)
    for key, value in retrieved_data.items():
        if not value:
            continue
        report[platform.capitalize() + key.capitalize()] = value


def general_add_info(report, _) -> None:
    """Entry point for Apport.

    This hook runs for every apport report

    Add a subset of non-sensitive cloud-init data from
    /run/cloud/instance-data.json that will be helpful for debugging.
    """
    try:
        if not is_cloud_init_enabled():
            return
        with open("/run/cloud-init/instance-data.json", "r") as fopen:
            instance_data = json.load(fopen)
    except FileNotFoundError:
        logging.getLogger().warning(
            "cloud-init run data not found on system. "
            "Unable to add cloud-specific data."
        )
        return

    v1 = instance_data.get("v1")
    if not v1:
        logging.getLogger().warning(
            "instance-data.json lacks 'v1' metadata. Present keys: %s",
            sorted(instance_data.keys()),
        )
        return

    for key, report_key in {
        "cloud_id": "CloudID",
        "cloud_name": "CloudName",
        "machine": "CloudArchitecture",
        "platform": "CloudPlatform",
        "region": "CloudRegion",
        "subplatform": "CloudSubPlatform",
    }.items():
        value = v1.get(key)
        if value:
            report[report_key] = value

    add_datasource_specific_info(
        report, v1["platform"], instance_data.get("ds")
    )
