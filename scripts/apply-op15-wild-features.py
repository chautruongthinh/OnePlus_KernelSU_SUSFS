#!/usr/bin/env python3
"""Port pinned WildKernels features to ACK r54, staging every change first."""

import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

ACK = '242e1a07f8782987c579929a20a74c0b26e88608'
PATCHES = '44b23a7601b98a3c548e2584e9f294d7b8b2d19b'
BBG = 'a54e0dc6cf0aff4dd87fec49644a02d2eb612905'

# The CPU-frequency limit patches require a separate port to r54's policy
# handling. Do not import OnePlus 6.12.23 module overlays into KMI generation 6.
OPTIMIZATIONS = [
    'optimized_mem_operations', 'file_struct_8bytes_align',
    'reduce_cache_pressure', 'mem_opt_prefetch', 'optimise_memcmp',
    'minimise_wakeup_time', 'int_sqrt', 'reduce_gc_thread_sleep_time',
    'add_timeout_wakelocks_globally', 'f2fs_reduce_congestion',
    'reduce_freeze_timeout', 'clear_page_16bytes_align',
    'adjust_cpu_scan_order', 'avoid_extra_s2idle_wake_attempts',
    'disable_cache_hot_buddy', 'f2fs_enlarge_min_fsync_blocks',
    'increase_ext4_default_commit_age', 'increase_sk_mem_packets',
    'reduce_pci_pme_wakeups', 'silence_irq_cpu_logspam', 'silence_system_logspam',
]

CONFIG = {
    'TCP_CONG_ADVANCED': 'y', 'TCP_CONG_BBR': 'y',
    'NET_SCHED': 'y', 'NET_SCH_FQ': 'y', 'NET_SCH_FQ_CODEL': 'y',
    'NET_SCH_CAKE': 'y', 'NET_SCH_PIE': 'y', 'NET_SCH_FQ_PIE': 'y',
    'NETFILTER': 'y', 'NETFILTER_ADVANCED': 'y', 'NETFILTER_XTABLES': 'y',
    'IP_NF_IPTABLES': 'y', 'IP_NF_MANGLE': 'y', 'IP_NF_TARGET_TTL': 'y',
    'IP6_NF_IPTABLES': 'y', 'IP6_NF_MANGLE': 'y', 'IP6_NF_TARGET_HL': 'y',
    'IP6_NF_MATCH_HL': 'y', 'IP_SET': 'y', 'IP_SET_MAX': '65534',
    **{f'IP_SET_{name}': 'y' for name in [
        'BITMAP_IP', 'BITMAP_IPMAC', 'BITMAP_PORT', 'HASH_IP', 'HASH_IPMARK',
        'HASH_IPPORT', 'HASH_IPPORTIP', 'HASH_IPPORTNET', 'HASH_IPMAC',
        'HASH_MAC', 'HASH_NETPORTNET', 'HASH_NET', 'HASH_NETNET',
        'HASH_NETPORT', 'HASH_NETIFACE', 'LIST_SET']},
    'NETFILTER_XT_MATCH_ADDRTYPE': 'y', 'NETFILTER_XT_SET': 'y',
    'NF_NAT': 'y', 'IP6_NF_NAT': 'y', 'IP6_NF_TARGET_MASQUERADE': 'y',
    'TMPFS_XATTR': 'y', 'TMPFS_POSIX_ACL': 'y', 'NTSYNC': 'y',
    'IP_NF_TARGET_REJECT': 'y', 'IP6_NF_TARGET_REJECT': 'y', 'NETFILTER_XT_TARGET_LOG': 'y',
    'NETFILTER_XT_MATCH_RECENT': 'y', 'BBG': 'y',
    'BBG_BLOCK_BOOT': 'n', 'BBG_BLOCK_RECOVERY': 'n',
    'LSM': '"landlock,lockdown,yama,loadpin,safesetid,selinux,smack,tomoyo,apparmor,ipe,bpf,baseband_guard"',
}

DROIDSPACES_CONFIG = {
    'SYSVIPC': 'y', 'IPC_NS': 'y', 'DEVTMPFS': 'y', 'PID_NS': 'y',
    'POSIX_MQUEUE': 'y', 'USER_NS': 'y',
}


def head(path):
    return subprocess.check_output(['git', '-C', str(path), 'rev-parse', 'HEAD'], text=True).strip()


def run(args):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('common', type=Path)
    parser.add_argument('patches', type=Path)
    parser.add_argument('bbg', type=Path)
    parser.add_argument('report', type=Path)
    parser.add_argument('--check-only', action='store_true')
    parser.add_argument('--droidspaces', action='store_true',
                        help='Experimental namespaces; changes Rust export CRCs')
    options = parser.parse_args(args)
    config = CONFIG | (DROIDSPACES_CONFIG if options.droidspaces else {})
    source, patches, bbg = (p.resolve() for p in (options.common, options.patches, options.bbg))
    assert head(source) == ACK, 'Unexpected kernel source revision'
    assert head(patches) == PATCHES, 'Unexpected WildKernels patch revision'
    assert head(bbg) == BBG, 'Unexpected Baseband-guard revision'
    assert not (source / '.op15-wild-features.json').exists(), 'Features already applied; start from the prepared baseline'
    applied = []
    changed = set()
    with tempfile.TemporaryDirectory(prefix='op15-wild-') as temporary:
        stage = Path(temporary)

        def prepare(path):
            relative = Path(path)
            assert not relative.is_absolute() and '..' not in relative.parts
            target = stage / relative
            if path not in changed:
                target.parent.mkdir(parents=True, exist_ok=True)
                if (source / relative).is_file():
                    shutil.copy2(source / relative, target)
                changed.add(path)
            return target

        def replace(path, old, new):
            target = prepare(path)
            text = target.read_text()
            assert text.count(old) == 1, f'{path}: expected one matching context: {old[:80]!r}'
            target.write_text(text.replace(old, new, 1))

        def apply(name, text=None, strip=1, directory=None):
            text = text if text is not None else (patches / 'common' / (name + '.patch')).read_text()
            if directory is None:
                for path in re.findall(r'^diff --git a/(\S+) b/\S+', text, re.M):
                    prepare(path)
            command = ['patch', '--batch', '--forward', '--fuzz=0', '--no-backup-if-mismatch',
                       f'-p{strip}', '-d', str(directory or stage)]
            for mode in [['--dry-run'], []]:
                result = subprocess.run(command + mode, input=text, text=True, capture_output=True)
                if result.returncode:
                    raise RuntimeError(f'{name}:\n{result.stdout}{result.stderr}')
            applied.append(name)
            print(f'Applied {name}', flush=True)

        def edit_patch(name, transformations):
            text = (patches / 'common' / (name + '.patch')).read_text()
            for old, new in transformations:
                assert text.count(old) == 1, f'{name}: patch context changed'
                text = text.replace(old, new, 1)
            apply(name, text)

        for name in OPTIMIZATIONS:
            if name == 'optimized_mem_operations':
                edit_patch(name, [(' #include <asm/word-at-a-time.h>\n #include <asm/page.h>',
                                   ' #include <linux/unaligned.h>\n #include <asm/word-at-a-time.h>')])
            elif name == 'clear_page_16bytes_align':
                edit_patch(name, [('SYM_FUNC_START_PI(clear_page)', 'SYM_FUNC_START(__pi_clear_page)')])
            elif name == 'f2fs_reduce_congestion':
                replace('fs/f2fs/f2fs.h',
                        '/* congestion wait timeout value, default: 20ms */\n#define\tDEFAULT_IO_TIMEOUT\t(msecs_to_jiffies(20))',
                        '/* congestion wait timeout value, default: 6ms */\n#define\tDEFAULT_IO_TIMEOUT\t(msecs_to_jiffies(6))')
                applied.append(name + ' (r54 context)')
            elif name == 'increase_sk_mem_packets':
                edit_patch(name, [('SK_WMEM_DEFAULT', 'SK_WMEM_MAX'), ('SK_RMEM_DEFAULT', 'SK_RMEM_MAX')])
            else:
                apply(name)

        apply('unicode_bypass_fix_6.1+')
        apply('ntsync/ntsync_compat_android16-6.12')
        for path in ['drivers/misc/ntsync.c', 'include/uapi/linux/ntsync.h']:
            prepare(path).unlink()
        apply('ntsync/ntsync_base')

        if options.droidspaces:
            # Use real reserved storage instead of inserting packed fields before
            # sched_entity. Both the runtime layout and gendwarfksyms must be kept.
            replace('include/linux/sched.h',
                    '#ifdef CONFIG_SYSVIPC\n\tstruct sysv_sem\t\t\tsysvsem;\n\tstruct sysv_shm\t\t\tsysvshm;\n#endif\n', '')
            replace('include/linux/sched.h',
                    '\tANDROID_KABI_RESERVE(3);\n\tANDROID_KABI_RESERVE(4);\n\tANDROID_KABI_RESERVE(5);\n\tANDROID_KABI_RESERVE(6);\n',
                    '#ifdef CONFIG_SYSVIPC\n'
                    '\tANDROID_KABI_USE(3, struct sysv_sem sysvsem);\n'
                    '\tANDROID_KABI_USE(4, struct list_head *sysvshm_next);\n'
                    '\tANDROID_KABI_USE(5, struct list_head *sysvshm_prev);\n'
                    '#else\n\tANDROID_KABI_RESERVE(3);\n\tANDROID_KABI_RESERVE(4);\n'
                    '\tANDROID_KABI_RESERVE(5);\n#endif\n\tANDROID_KABI_RESERVE(6);\n')
            applied.append('Droidspaces SYSVIPC using r54 task_struct reserves 3-5')
            # r54's gendwarfksyms supports one original field per replacement
            # union. Store the two list pointers in separate reserved words, and
            # validate their contiguity instead of hiding a second original field.
            target = prepare('include/linux/sched.h')
            text = target.read_text()
            boundary = '\n#ifdef CONFIG_SCHED_PROXY_EXEC\nDECLARE_STATIC_KEY_FALSE(__sched_proxy_exec);'
            assert text.count(boundary) == 1
            text = text.replace(boundary,
                '\n#ifdef CONFIG_SYSVIPC\n'
                '_Static_assert(__builtin_offsetof(struct task_struct, sysvshm_prev) ==\n'
                '\t       __builtin_offsetof(struct task_struct, sysvshm_next) + sizeof(void *),\n'
                '\t       "SYSVIPC list pointers must occupy adjacent reserved words");\n'
                '#endif\n' + boundary, 1)
            target.write_text(text)
            replace('include/linux/shm.h',
                    '#define shm_init_task(task) INIT_LIST_HEAD(&(task)->sysvshm.shm_clist)',
                    '#define task_shm_clist(task) ((struct list_head *)&(task)->sysvshm_next)\n'
                    '#define shm_init_task(task) INIT_LIST_HEAD(task_shm_clist(task))')
            target = prepare('ipc/shm.c')
            text = target.read_text()
            assert text.count('&task->sysvshm.shm_clist') == 2
            assert text.count('&current->sysvshm.shm_clist') == 1
            target.write_text(text.replace('&task->sysvshm.shm_clist', 'task_shm_clist(task)')
                             .replace('&current->sysvshm.shm_clist', 'task_shm_clist(current)'))
            edit_patch('droidspaces/0001-Return-ghost-task-if-task-is-null-and-is-requested-b',
                       [(' #include <linux/idr.h>\n #include <net/sock.h>',
                         ' #include <linux/pidfs.h>\n #include <net/sock.h>')])
            replace('kernel/pid.c',
                    'strncpy(ghost_task.comm, "ghost-task-sentinel", TASK_COMM_LEN);',
                    'strscpy(ghost_task.comm, "ghost-task", sizeof(ghost_task.comm));')
            for path, symbol in [('ipc/namespace.c', 'put_ipc_ns'), ('ipc/msgutil.c', 'init_ipc_ns')]:
                target = prepare(path)
                text = target.read_text()
                if not re.search(r'EXPORT_SYMBOL(?:_GPL)?\(' + symbol + r'\)', text):
                    target.write_text(text + f'\nEXPORT_SYMBOL_GPL({symbol});\n')

        bbg_target = stage / 'security/baseband-guard'
        shutil.copytree(bbg, bbg_target, ignore=shutil.ignore_patterns('.git', '.github'))
        for path in bbg_target.rglob('*'):
            if path.is_file():
                changed.add(str(path.relative_to(stage)))
        target = bbg_target / 'Makefile'
        text = target.read_text()
        start = text.index('GIT_BIN :=')
        end = text.index('ifeq ($(shell grep -q "file_ioctl_compat"', start)
        text = text[:start] + (f'REPO_LINK := https://github.com/vc-teahouse/Baseband-guard\n'
                               f'COMMIT_SHA := {BBG[:8]}\n\n') + text[end:]
        target.write_text(text)
        for path, addition in [
            ('security/Makefile', '\nobj-$(CONFIG_BBG) += baseband-guard/\n'),
            ('security/Kconfig', '\nsource "security/baseband-guard/Kconfig"\n')]:
            target = prepare(path)
            target.write_text(target.read_text() + addition)
        apply('bbg/0001-Enable-abl-and-efisp-flashing-for-efisp-exploit-devices',
              strip=2, directory=bbg_target)

        target = prepare('arch/arm64/configs/gki_defconfig')
        text = target.read_text()
        for key, value in config.items():
            text = re.sub(rf'^(?:CONFIG_{key}=.*|# CONFIG_{key} is not set)\n?', '', text, flags=re.M)
            text += f'\nCONFIG_{key}={value}'
        target.write_text(text + '\n')

        report = {'ack': ACK, 'wild_patches': PATCHES, 'bbg': BBG,
                  'patches': applied, 'required_config': config,
                  'droidspaces': options.droidspaces,
                  'excluded': ['OnePlus KMI5 module overlays', 'HMBIRD', 'BBRv3',
                               'scaling_min_freq limitation patches'],
                  'check_only': options.check_only}
        if not options.check_only:
            backup = source.parent / 'op15-wild-baseline-backup'
            assert not backup.exists(), f'Backup already exists: {backup}'
            backup.mkdir()
            missing = []
            for path in sorted(changed):
                destination = source / path
                if destination.exists():
                    saved = backup / path
                    saved.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(destination, saved)
                else:
                    missing.append(path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(stage / path, destination)
            (backup / 'new-files.json').write_text(json.dumps(missing, indent=2) + '\n')
            (source / '.op15-wild-features.json').write_text(json.dumps(report, indent=2) + '\n')
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2) + '\n')
        print(f'Validated {len(applied)} feature changes; check_only={options.check_only}', flush=True)


if __name__ == '__main__':
    import sys
    run(sys.argv[1:])
