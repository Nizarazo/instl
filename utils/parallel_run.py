#!/usr/bin/env python3.6


import subprocess
import sys
import os
import signal
import logging
import psutil
from itertools import repeat
from concurrent import futures
from threading import Timer

import utils

log = logging.getLogger()

exit_val = 0
aborted = False
process_list = list()


class ProcessTerminatedExternally(RuntimeError):
    pass


class ContinuousTimer(Timer):
    """
    See: https://hg.python.org/cpython/file/2.7/Lib/threading.py#l1079
    """

    def run(self):
        while not self.finished.is_set():
            self.finished.wait(self.interval)
            self.function(*self.args, **self.kwargs)

        self.finished.set()


def run_processes_in_parallel(commands, shell=False, do_enqueue_output=True, abort_file=None):
    global exit_val
    try:
        install_signal_handlers()

        lists_of_command_lists = utils.partition_list(commands, lambda c: c[0] == "wait")

        for command_list in lists_of_command_lists:
            with futures.ThreadPoolExecutor(len(command_list)) as executor:
                list(executor.map(run_process, command_list, repeat(shell), repeat(do_enqueue_output), repeat(abort_file)))
        log.debug('Finished all processes')
        exit_val = 0
        killall_and_exit()
    except Exception as e:
        log.error(e)
        killall_and_exit()


def run_process(command, shell, do_enqueue_output=True, abort_file=None):
    """
    Running a sub-process externally
    Args:
        command: The command to run as sub-process. list/string (when using shell=True)
        shell: Running the command in a shell
        do_enqueue_output: Printing sub-process output to the log file.
                           Should be used only when calling processes that are not instl.
                           The option blocks the main process and can't be used when using abort_file.
        abort_file: Using an abort file to monitor and killing the process in case the file was deleted.
                    This option overrides do_enqueue_output to be able to monitor the abort file.
    """
    global exit_val
    global process_list
    a_process = launch_process(command, shell)
    process_list.append(a_process)
    t = None
    if abort_file is not None:
        t = ContinuousTimer(1, check_abort_file, args=[abort_file])
        t.start()

    try:
        while True:
            if do_enqueue_output and not abort_file:  # Calling enqueue_output only if abort file is not used.
                enqueue_output(a_process)
            status = a_process.poll()
            if status is not None:  # None means it's still alive
                log.debug(f'Process finished - {command}')
                if aborted:
                    exit_val = status
                    raise ProcessTerminatedExternally(command)
                elif status != 0:
                    exit_val = status
                    raise RuntimeError(f'Command failed {command}')
                break
    finally:
        if t is not None:
            t.cancel()


def launch_process(command, shell):
    global exit_val
    if shell:
        full_command = " ".join(command)
        kwargs = {}
    else:
        full_command = command
        kwargs = {'executable': command[0]}
    if getattr(os, "setsid", None):  # UNIX
        kwargs['preexec_fn'] = os.setsid
    try:
        a_process = subprocess.Popen(full_command, shell=shell, env=os.environ, bufsize=128,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kwargs)
    except Exception as e:
        exit_val = 31
        raise RuntimeError(f"failed to start {command}") from e
    return a_process


def enqueue_output(a_process):
    out = a_process.stdout
    try:
        while True:
            buffer = b''
            while True:
                b = out.read(128)
                if b == b'':
                    break
                buffer += b

            if len(buffer) > 0:
                [log.info(line) for line in buffer.decode('utf-8').strip('\n').split('\n')]
            if a_process.poll() is not None:
                break
    except ValueError as e:
        pass  # on mac the stdout is closed when the process is terminated. In this case we ignore
    finally:
        if not out.closed:
            out.close()


def check_abort_file(abort_file):
    global aborted
    if not os.path.exists(abort_file):
        aborted = True
        log.debug(f'Process aborted - Abort file not found {abort_file}')
        killall_and_exit()


def signal_handler(signum, frame):
    global exit_val
    exit_val = signum
    killall_and_exit()


def killall_and_exit():
    for a_process in process_list:
        status = a_process.poll()
        if status is None:  # None means it's still alive
            if getattr(os, "killpg", None):
                os.killpg(a_process.pid, signal.SIGTERM)  # Unix
            else:
                kill_proc_tree(a_process.pid)  # Windows
    sys.exit(exit_val)


def kill_proc_tree(pid, including_parent=True):
    parent = psutil.Process(pid)
    children = parent.children(recursive=True)
    for child in children:
        child.kill()
    gone, still_alive = psutil.wait_procs(children, timeout=5)
    if including_parent:
        parent.kill()
        parent.wait(5)


def install_signal_handlers():
    for sig in (signal.SIGABRT, signal.SIGFPE, signal.SIGILL, signal.SIGINT, signal.SIGSEGV, signal.SIGTERM):
        signal.signal(sig, signal_handler)
