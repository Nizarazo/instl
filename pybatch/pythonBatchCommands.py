import os
import re
import stat
import sys
import subprocess
import abc
import io
from contextlib import ExitStack, contextmanager

import utils

first_cap_re = re.compile('(.)([A-Z][a-z]+)')
all_cap_re = re.compile('([a-z0-9])([A-Z])')


def camel_to_snake_case(identifier):
    identifier1 = first_cap_re.sub(r'\1_\2', identifier)
    identifier2 = all_cap_re.sub(r'\1_\2', identifier1).lower()
    return identifier2


class PythonBatchCommandBase(abc.ABC):
    """ PythonBatchCommandBase is the base class for all classes implementing batch commands.
        PythonBatchCommandBase implement context manager interface:
        __enter__: will print progress message (if needed)
                    derived classes should not override __enter__ and should not do any actual work here but implement
                    the work in __call__. If something must be done in __enter__ override enter_self
        __exit__: will handle exceptions and print warning/error messages, or ignore errors if needed
                 derived classes should not override __exit__. If something must be done in __exit__ override exit_self
        Derived classes must implement some additional methods:
        __repr__: must be implemented correctly so the returned string can be passed to eval to recreate the object
        __init__: must record all parameters needed to implement __repr__ and must not do any actual work!
        __call__: here the real
    """
    instance_counter = 0
    total_progress = 0

    @abc.abstractmethod
    def __init__(self, identifier=None, report_own_progress=True, ignore_all_errors=False):
        PythonBatchCommandBase.instance_counter += 1
        if not isinstance(identifier, str) or not identifier.isidentifier():
            self.identifier = "obj"
        self.obj_name = camel_to_snake_case(f"{self.__class__.__name__}_{PythonBatchCommandBase.instance_counter:05}")
        self.report_own_progress = report_own_progress
        self.ignore_all_errors = ignore_all_errors
        self.progress = 0
        if self.report_own_progress:
            PythonBatchCommandBase.total_progress += 1
            self.progress = PythonBatchCommandBase.total_progress
        self.exceptions_to_ignore = []
        self.child_batch_commands = []

    @abc.abstractmethod
    def __repr__(self):
        the_repr = f"{self.__class__.__name__}(report_own_progress={self.report_own_progress}, ignore_all_errors={self.ignore_all_errors})"
        return the_repr

    def __eq__(self, other):
        do_not_compare_keys = ('progress', 'obj_name')
        dict_self =  {k:  self.__dict__[k] for k in  self.__dict__.keys() if k not in do_not_compare_keys}
        dict_other = {k: other.__dict__[k] for k in other.__dict__.keys() if k not in do_not_compare_keys}
        is_eq = dict_self == dict_other
        return is_eq

    def __hash__(self):
        the_hash = hash(tuple(sorted(self.__dict__.items())))
        return the_hash

    def progress_msg(self):
        the_progress_msg = f"Progress {self.progress} of {PythonBatchCommandBase.total_progress};"
        return the_progress_msg

    def progress_msg_self(self):
        """ classes overriding PythonBatchCommandBase should add their own progress message
        """
        return ""

    def error_msg_self(self):
        """ classes overriding PythonBatchCommandBase should add their own error message
        """
        return ""

    def enter_self(self):
        """ classes overriding PythonBatchCommandBase can add code here without
            repeating __enter__, bit not do any actual work!
        """
        pass

    def __enter__(self):
        try:
            if self.report_own_progress:
                print(f"{self.progress_msg()} {self.progress_msg_self()}")
            self.enter_self()
        except Exception as ex:
            suppress_exception = self.__exit__(*sys.exc_info())
            if not suppress_exception:
                raise
        return self

    def exit_self(self, exit_return):
        """ classes overriding PythonBatchCommandBase can add code here without
            repeating __exit__.
            exit_self will be called regardless of exceptions
            param exit_return is what __exit__ will return
        """
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        suppress_exception = False
        if self.ignore_all_errors or exc_type is None:
            suppress_exception = True
        elif exc_type in self.exceptions_to_ignore:
            print(f"{self.progress_msg()} WARNING; {exc_val}")
            suppress_exception = True
        else:
            print(f"{self.progress_msg()} ERROR; {self.error_msg_self()}; {exc_val.__class__.__name__}: {exc_val}")
        self.exit_self(exit_return=suppress_exception)
        return suppress_exception

    @abc.abstractmethod
    def __call__(self, *args, **kwargs):
        pass

# === classes with tests ===
class MakeDirs(PythonBatchCommandBase):
    """ Create one or more dirs
        when remove_obstacles==True if one of the paths is a file it will be removed
        when remove_obstacles==False if one of the paths is a file 'FileExistsError: [Errno 17] File exists' will raise
        it it always OK for a dir to already exists
        Tests: TestPythonBatch.test_MakeDirs_*
    """
    def __init__(self, *paths_to_make, remove_obstacles=True):
        super().__init__(report_own_progress=True)
        self.paths_to_make = paths_to_make
        self.remove_obstacles = remove_obstacles
        self.cur_path = None

    def __repr__(self):
        paths_csl = ", ".join(utils.quoteme_double_list(self.paths_to_make))
        the_repr = f"""{self.__class__.__name__}({paths_csl}, remove_obstacles={self.remove_obstacles})"""
        return the_repr

    def progress_msg_self(self):
        the_progress_msg = f"mkdir {self.paths_to_make}"
        return the_progress_msg

    def __call__(self):
        retVal = 0
        for self.cur_path in self.paths_to_make:
            if self.remove_obstacles:
                if os.path.isfile(self.cur_path):
                    os.unlink(self.cur_path)
            os.makedirs(self.cur_path, mode=0o777, exist_ok=True)
            retVal += 1
        return retVal

    def error_msg_self(self):
        return f"creating {self.cur_path}"


# === classes without tests (yet) ===
class Chmod(PythonBatchCommandBase):
    all_read_write = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IWGRP | stat.S_IROTH | stat.S_IWOTH
    all_read_write_exec = all_read_write | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH

    def __init__(self, path, mode):
        super().__init__(report_own_progress=True)
        self.path = path
        self.mode = mode
        self.exceptions_to_ignore.append(FileNotFoundError)

    def __repr__(self):
        the_repr = f"""{self.__class__.__name__}(path="{self.path}", mode={self.mode})"""
        return the_repr

    def progress_msg_self(self):
        the_progress_msg = f"Change mode {self.path}"
        return the_progress_msg

    def __call__(self):
        os.chmod(self.path, self.mode)
        return None


class Cd(PythonBatchCommandBase):
    def __init__(self, path):
        super().__init__(report_own_progress=True)
        self.new_path = path
        self.old_path = None

    def __repr__(self):
        the_repr = f"""{self.__class__.__name__}(path="{self.new_path}")"""
        return the_repr

    def progress_msg_self(self):
        the_progress_msg = f"cd to {self.new_path}"
        return the_progress_msg

    def __call__(self):
        self.old_path = os.getcwd()
        os.chdir(self.new_path)
        return None

    def exit_self(self, exit_return):
        os.chdir(self.old_path)


class Section(PythonBatchCommandBase):
    def __init__(self, name):
        super().__init__()
        self.name = name

    def __repr__(self):
        the_repr = f"""{self.__class__.__name__}(name="{self.name}")"""
        return the_repr

    def progress_msg_self(self):
        the_progress_msg = f"{self.name} ..."
        return the_progress_msg

    def __call__(self, *args, **kwargs):
        pass


class RunProcessBase(PythonBatchCommandBase):
    def __init__(self):
        super().__init__()

    @abc.abstractmethod
    def create_run_args(self):
        raise NotImplementedError

    def __call__(self, *args, **kwargs):
        run_args = self.create_run_args()
        completed_process = subprocess.run(run_args, check=True)
        return None  # what to return here?

    def __repr__(self):
        raise NotImplementedError


class Chown(RunProcessBase):
    def __init__(self, user_id, group_id, path, recursive=False):
        super().__init__(report_own_progress=True)
        self.user_id = user_id
        self.group_id = group_id
        self.path = path
        self.recursive = recursive
        self.exceptions_to_ignore.append(FileNotFoundError)

    def __repr__(self):
        the_repr = f"""{self.__class__.__name__}(user_id={self.user_id}, group_id={self.group_id}, path="{self.path}", recursive={self.recursive})"""
        return the_repr

    def create_run_args(self):
        run_args = list()
        run_args.append("chown")
        run_args.append("-f")
        if self.recursive:
            run_args.append("-R")
        run_args.append("".join((self.user_id, ":", self.group_id)))
        run_args.append(utils.quoteme_double(self.path))
        return run_args

    def progress_msg_self(self):
        the_progress_msg = f"Change owner {self.path}"
        return the_progress_msg

    def __call__(self):
        # os.chown is not recursive so call the system's chown
        if self.recursive:
            return super().__call__()
        else:
            os.chown(self.path, uid=self.user_id, gid=self.group_id)
            return None

class RsyncCopyBase(RunProcessBase):
    def __init__(self, src, trg, link_dest=False, ignore=None, preserve_dest_files=False):
        super().__init__()
        self.src = src
        self.trg = trg
        self.link_dest = link_dest
        self.ignore = ignore
        self.preserve_dest_files = preserve_dest_files

    def __repr__(self):
        the_repr = f"""{self.__class__.__name__}(src="{self.src}", trg="{self.trg}", link_dest={self.link_dest}, ignore={self.ignore}, preserve_dest_files={self.preserve_dest_files})"""
        return the_repr

    def create_run_args(self):
        run_args = list()
        ignore_spec = self.create_ignore_spec(self.ignore)
        if not self.preserve_dest_files:
            delete_spec = "--delete"
        else:
            delete_spec = ""

        run_args.extend(["rsync", "--owner", "--group", "-l", "-r", "-E", delete_spec, *ignore_spec, self.src, self.trg])
        if self.link_dest:
            the_link_dest = os.path.join(self.src, "..")
            run_args.append(f''''--link-dest="{the_link_dest}"''')

        return run_args

    def create_ignore_spec(self, ignore):
        retVal = []
        if ignore:
            if isinstance(ignore, str):
                ignore = (ignore,)
            retVal.extend(["--exclude=" + utils.quoteme_single(ignoree) for ignoree in ignore])
        return retVal

    def progress_msg_self(self):
        the_progress_msg = f"{self}"
        return the_progress_msg

class CopyDirToDir(RsyncCopyBase):
    def __init__(self, src, trg, link_dest=False, ignore=None, preserve_dest_files=False):
       src = src.rstrip("/")
       super().__init__(src=src, trg=trg, link_dest=link_dest, ignore=ignore, preserve_dest_files=preserve_dest_files)

class CopyDirContentsToDir(RsyncCopyBase):
    def __init__(self, src, trg, link_dest=False, ignore=None, preserve_dest_files=False):
        if not src.endswith("/"):
            src += "/"
        super().__init__(src=src, trg=trg, link_dest=link_dest, ignore=ignore, preserve_dest_files=preserve_dest_files)

class CopyFileToFile(RsyncCopyBase):
    def __init__(self, src, trg, link_dest=False, ignore=None, preserve_dest_files=False):
       src = src.rstrip("/")
       super().__init__(src=src, trg=trg, link_dest=link_dest, ignore=ignore, preserve_dest_files=preserve_dest_files)

class CopyFileToDir(RsyncCopyBase):
    def __init__(self, src, trg, link_dest=False, ignore=None, preserve_dest_files=False):
       src = src.rstrip("/")
       super().__init__(src=src, trg=trg, link_dest=link_dest, ignore=ignore, preserve_dest_files=preserve_dest_files)


class Dummy(PythonBatchCommandBase):
    def __init__(self, name):
        super().__init__()
        self.name = name

    def __repr__(self):
        the_repr = f"""{self.__class__.__name__}(name="{self.name}")"""
        return the_repr

    def progress_msg_self(self):
        the_progress_msg = f"Dummy {self.name} ..."
        return the_progress_msg

    def enter_self(self):
        print(f"Dummy __enter__ {self.name}")

    def exit_self(self, exit_return):
        print(f"Dummy __exit__ {self.name}")

    def __call__(self, *args, **kwargs):
        print(f"Dummy __call__ {self.name}")


class BatchCommandAccum(object):

    def __init__(self):
        self.context_stack = [list()]

    def __iadd__(self, other):
        self.context_stack[-1].append(other)
        return self

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    @contextmanager
    def sub_section(self, context):
        self.context_stack[-1].append(context)
        self.context_stack.append(context.child_batch_commands)
        yield self
        self.context_stack.pop()

    def __repr__(self):
        def _repr_helper(batch_items, io_str, indent):
            indent_str = "    "*indent
            if isinstance(batch_items, list):
                for item in batch_items:
                    _repr_helper(item, io_str, indent)
                    _repr_helper(item.child_batch_commands, io_str, indent+1)
            else:
                io_str.write(f"""{indent_str}with {repr(batch_items)} as {batch_items.obj_name}:\n""")
                io_str.write(f"""{indent_str}    {batch_items.obj_name}()\n""")
        PythonBatchCommandBase.total_progress = 0
        io_str = io.StringIO()
        _repr_helper(self.context_stack[0], io_str, 0)
        return io_str.getvalue()
