#!/usr/bin/env python3

#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
check the effective CPU frequency (MHz) per vCPU of virtual machines
"""

__cmd__ = 'vm-cpufreq'

from pyVmomi import vim
from monplugin import Check, Status
from checkvsphere.tools import cli, service_instance
from checkvsphere.tools.helper import (
    find_entity_views, isbanned, isallowed, CheckArgument,
)


def run():
    parser = cli.Parser()
    parser.add_optional_arguments(cli.Argument.VIHOST)
    parser.add_optional_arguments(CheckArgument.CRITICAL_THRESHOLD)
    parser.add_optional_arguments(CheckArgument.WARNING_THRESHOLD)
    parser.add_optional_arguments(CheckArgument.ALLOWED('regex, checked against <vm-name>'))
    parser.add_optional_arguments(CheckArgument.BANNED('regex, checked against <vm-name>'))
    parser.add_optional_arguments({
        'name_or_flags': ['--percent'],
        'options': {
            'action': 'store_true',
            'default': False,
            'help': 'thresholds apply to the MHz per vCPU as a percentage of '
                    'the host core frequency instead of absolute MHz',
        }
    })
    args = parser.get_args()

    check = Check()
    check.set_threshold(warning=args.warning, critical=args.critical)

    si = service_instance.connect(args)

    vms = find_entity_views(
        si,
        vim.VirtualMachine,
        begin_entity=si.content.rootFolder,
        properties=[
            'name',
            'runtime.powerState',
            'runtime.host',
            'config.hardware.numCPU',
            'summary.quickStats.overallCpuUsage',
        ],
    )

    seen = 0
    for vm in vms:
        props = vm['props']
        name = props.get('name')

        if isbanned(args, name) or not isallowed(args, name):
            continue
        if props.get('runtime.powerState') != 'poweredOn':
            continue

        host = props.get('runtime.host')
        if args.vihost and (host is None or host.name != args.vihost):
            continue

        used_mhz = props.get('summary.quickStats.overallCpuUsage')
        num_cpu = props.get('config.hardware.numCPU')
        if used_mhz is None or not num_cpu:
            continue

        per_vcpu = used_mhz / num_cpu
        value = per_vcpu
        uom = 'MHz'
        text = f"{name}: {per_vcpu:.0f} MHz per vCPU ({num_cpu} vCPU, {used_mhz} MHz total)"

        if args.percent:
            core_mhz = host.summary.hardware.cpuMhz if host else None
            if not core_mhz:
                continue
            value = per_vcpu / core_mhz * 100
            uom = '%'
            text = f"{name}: {value:.1f}% of host core frequency ({per_vcpu:.0f} of {core_mhz} MHz)"

        seen += 1
        check.add_perfdata(
            label=f"{name} cpu_mhz_per_vcpu" if not args.percent else f"{name} cpu_freq_pct",
            value=round(value, 2),
            uom=uom,
            threshold=check.threshold,
        )
        check.add_message(check.threshold.get_status(value), text)

    if seen == 0:
        check.exit(Status.WARNING, "no powered-on VM with CPU statistics found")

    (code, message) = check.check_messages(separator='\n', separator_all='\n')
    check.exit(code=code, message=message or "all vCPU frequencies ok")
